"""Image generation behind one interface, with a Gemini adapter (generateContent).

Every call: render the prompt from the store repo -> authorize the estimated cost ->
one generateContent call with the reference images inline -> read the image part
(fail closed) -> record the cost. Model ids, sizes and prices come only from store.yaml.
"""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image

from autolist.core.cost_ledger import CostLedger
from autolist.core.errors import ConfigInvalid
from autolist.core.logging import get_logger, register_secret
from autolist.core.models import (
    CostEntry,
    GeneratedImage,
    ImageModelPrices,
    ImageRequest,
    StoreConfig,
)
from autolist.images.errors import (
    BillingRequired,
    ImageNotReturned,
    ImageRequestRejected,
    ImageUnavailable,
)
from autolist.llm.prompt_loader import load_prompt, render

log = get_logger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1"
MAX_TRIES = 3
BACKOFF_START_S = 1.0
TIMEOUT_S = 180.0
_MTOK = Decimal(1_000_000)


class ImageGenerator(Protocol):
    def generate(self, req: ImageRequest) -> GeneratedImage: ...


def _shape(node: Any, depth: int = 0) -> Any:
    """Keys of a JSON response, with every leaf replaced by its type. Never image bytes."""
    if depth > 6:
        return "..."
    if isinstance(node, dict):
        return {k: _shape(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        shapes: list[Any] = []
        for item in node[:5]:
            sh = _shape(item, depth + 1)
            if sh not in shapes:
                shapes.append(sh)
        return shapes
    return type(node).__name__


class GeminiImageGenerator:
    def __init__(
        self,
        config: StoreConfig,
        api_key: str,
        ledger: CostLedger,
        *,
        prompts_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if config.images.provider != "gemini":
            raise ConfigInvalid("images.provider", "GeminiImageGenerator needs provider 'gemini'")
        register_secret(api_key)
        self._config = config
        self._key = api_key
        self._ledger = ledger
        self._prompts_dir = prompts_dir
        self._sleep = sleep
        self._now = now
        self._http = httpx.Client(timeout=TIMEOUT_S, transport=transport)
        self.last_shape: Any = None  # redacted shape of the last response, for live reports

    def close(self) -> None:
        self._http.close()

    # ----------------------------------------------------------------- public

    def generate(self, req: ImageRequest) -> GeneratedImage:
        size: str = self._config.require("images.generate_size")
        prices = self._prices(req.model)
        per_image = prices.per_image_usd.get(size)
        if per_image is None:
            raise ConfigInvalid(
                f"images.prices.{req.model}.per_image_usd.{size}", "no price for this size"
            )
        per_ref: int = self._config.require("images.est_input_tokens_per_reference")
        text = render(load_prompt(req.prompt_name, self._prompts_dir), req.variables)

        est_in = len(text.encode("utf-8")) + per_ref * len(req.references)
        estimate = per_image + prices.input_usd_per_mtok * est_in / _MTOK
        self._ledger.require(req.job_id, "gemini", f"image:{req.role}", estimate)

        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": text},
                        *(
                            {
                                "inline_data": {
                                    "mime_type": r.mime_type,
                                    "data": base64.b64encode(r.data).decode("ascii"),
                                }
                            }
                            for r in req.references
                        ),
                    ],
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": "1:1", "imageSize": size},
            },
        }
        data = self._post(req.model, body)
        self.last_shape = _shape(data)

        image, mime, texts, finish = self._read(data)
        usage = data.get("usageMetadata")
        prompt_tokens = usage.get("promptTokenCount") if isinstance(usage, dict) else None
        if image is not None and isinstance(prompt_tokens, int):
            usd = per_image + prices.input_usd_per_mtok * prompt_tokens / _MTOK
            estimated = False
        else:
            # no usage data, or no image (text-only output is not priced in store.yaml):
            # record the pre-call estimate, which is an upper bound for the caps
            usd, estimated = estimate, True
        # record before anything can fail on the response
        self._ledger.record(
            CostEntry(
                at=self._now(),
                job_id=req.job_id,
                provider="gemini",
                category="image",
                operation=f"image:{req.role}:{req.model}",
                units=1,
                unit="image",
                usd=usd,
                estimated=estimated,
            )
        )
        if image is None:
            raise ImageNotReturned(
                f"{req.model} returned no image (finish reason {finish})",
                finish_reason=finish,
                text=" ".join(texts),
                shape=str(self.last_shape),
            )
        with Image.open(io.BytesIO(image)) as im:
            size_px = (im.width, im.height)
        log.info(
            "image generated",
            extra={"model": req.model, "role": req.role, "px": size_px, "estimated": estimated},
        )
        return GeneratedImage(
            model=req.model,
            role=req.role,
            mime_type=mime,
            data=image,
            size_px=size_px,
            text_parts=texts,
            finish_reason=finish,
            usd=usd,
            estimated_cost=estimated,
        )

    # ----------------------------------------------------------------- internals

    def _prices(self, model: str) -> ImageModelPrices:
        prices = self._config.images.prices.get(model)
        if prices is None:
            raise ConfigInvalid(f"images.prices.{model}", "no price for this model")
        return prices

    def _post(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_BASE}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        backoff = BACKOFF_START_S
        for attempt in range(1, MAX_TRIES + 1):
            try:
                r = self._http.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                if attempt == MAX_TRIES:
                    raise ImageUnavailable(f"gemini: {type(exc).__name__}") from None
                self._sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 200:
                parsed: dict[str, Any] = r.json()
                return parsed
            if r.status_code == 402:
                raise BillingRequired("gemini 402: prepaid balance is empty; add credit")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == MAX_TRIES:
                    raise ImageUnavailable(f"gemini {r.status_code}")
                self._sleep(_retry_after(r) or backoff)
                backoff *= 2
                continue
            raise ImageRequestRejected(f"gemini {r.status_code}: {_error_message(r)}")
        raise ImageUnavailable("unreachable")  # pragma: no cover

    @staticmethod
    def _read(data: dict[str, Any]) -> tuple[bytes | None, str, list[str], str | None]:
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        finish = first.get("finishReason")
        parts = (first.get("content") or {}).get("parts") or []
        texts = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
        for p in parts:
            inline = p.get("inlineData") if isinstance(p, dict) else None
            if isinstance(inline, dict) and isinstance(inline.get("data"), str):
                mime = str(inline.get("mimeType", ""))
                return base64.b64decode(inline["data"]), mime, texts, finish
        return None, "", texts, finish


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _error_message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return r.text[:300]
