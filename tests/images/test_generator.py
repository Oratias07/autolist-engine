"""images.generator: synthetic Gemini responses, no network."""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from autolist.core.config import parse_store_config
from autolist.core.cost_ledger import CostLedger, InMemoryLedgerStore
from autolist.core.errors import ConfigInvalid, CostCapExceeded
from autolist.core.models import CostEntry, ImageRequest, ReferenceImage, StoreConfig
from autolist.images.errors import (
    BillingRequired,
    ImageNotReturned,
    ImageRequestRejected,
    ImageUnavailable,
)
from autolist.images.generator import GeminiImageGenerator
from tests.core.conftest import VALID_STORE

KEY = "test-gemini-key-not-real"
NOW = datetime(2026, 11, 15, tzinfo=UTC)
MODEL = "image-model-a"


def _png(w: int = 64, h: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _ok(prompt_tokens: int | None = 2000, image: bool = True) -> httpx.Response:
    parts: list[dict[str, Any]] = [{"text": "here you go"}]
    if image:
        b64 = base64.b64encode(_png(32, 32)).decode()
        parts.append({"inlineData": {"mimeType": "image/png", "data": b64}})
    body: dict[str, Any] = {
        "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": "STOP"}]
    }
    if prompt_tokens is not None:
        body["usageMetadata"] = {"promptTokenCount": prompt_tokens, "candidatesTokenCount": 1}
    return httpx.Response(200, json=body)


@pytest.fixture
def prompts(tmp_path: Path) -> Path:
    (tmp_path / "image_main.md").write_text(
        "---\nname: image_main\nversion: 1\nstatus: draft\nvariables: [product]\n"
        "untrusted: [product]\n---\nMain image of the product.\n{{ product }}\n",
        encoding="utf-8",
    )
    return tmp_path


def _cfg(**images: Any) -> StoreConfig:
    data = copy.deepcopy(VALID_STORE)
    data["images"].update(images)
    return parse_store_config(data)


class Rig:
    def __init__(
        self, prompts: Path, responses: list[httpx.Response], cfg: StoreConfig | None = None
    ):
        self.cfg = cfg or _cfg()
        self.calls: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.ledger = CostLedger(
            self.cfg.costs, self.cfg.store.timezone, InMemoryLedgerStore(), lambda: NOW
        )
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return queue.pop(0)

        self.gen = GeminiImageGenerator(
            self.cfg,
            KEY,
            self.ledger,
            prompts_dir=prompts,
            transport=httpx.MockTransport(handler),
            sleep=self.sleeps.append,
            now=lambda: NOW,
        )


def _req(**kw: Any) -> ImageRequest:
    base: dict[str, Any] = {
        "job_id": "job-1",
        "model": MODEL,
        "role": "main",
        "prompt_name": "image_main",
        "variables": {"product": "Task chair, black fabric"},
        "references": [ReferenceImage(name="ref.png", mime_type="image/png", data=_png())],
    }
    base.update(kw)
    return ImageRequest(**base)


def test_generates_image_and_records_cost(prompts: Path) -> None:
    rig = Rig(prompts, [_ok(prompt_tokens=2000)])
    out = rig.gen.generate(_req())
    assert out.size_px == (32, 32)
    assert out.mime_type == "image/png"
    assert out.text_parts == ["here you go"]
    assert out.usd == Decimal("0.05") + Decimal("1.00") * 2000 / 1_000_000
    assert out.estimated_cost is False
    assert rig.ledger.month_summary(NOW).by_category["image"] == out.usd
    req = rig.calls[0]
    assert req.url.path == f"/v1/models/{MODEL}:generateContent"
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "1:1", "imageSize": "2K"}
    parts = body["contents"][0]["parts"]
    assert '<untrusted name="product">' in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "image/png"


def test_refused_authorization_sends_nothing(prompts: Path) -> None:
    rig = Rig(prompts, [_ok()])
    rig.ledger.record(
        CostEntry(
            at=NOW,
            job_id=None,
            provider="seed",
            category="llm",
            operation="seed",
            units=0,
            unit="usd",
            usd=Decimal(60),
            estimated=False,
        )
    )
    with pytest.raises(CostCapExceeded):
        rig.gen.generate(_req())
    assert rig.calls == []


def test_402_raises_billing_required_without_retry(prompts: Path) -> None:
    rig = Rig(prompts, [httpx.Response(402, json={"error": {"message": "no credit"}})])
    with pytest.raises(BillingRequired):
        rig.gen.generate(_req())
    assert len(rig.calls) == 1


def test_429_honors_retry_after_then_succeeds(prompts: Path) -> None:
    rig = Rig(prompts, [httpx.Response(429, headers={"retry-after": "7"}), _ok()])
    rig.gen.generate(_req())
    assert rig.sleeps == [7.0]
    assert len(rig.calls) == 2


def test_5xx_gives_up_after_three_tries(prompts: Path) -> None:
    rig = Rig(prompts, [httpx.Response(503)] * 3)
    with pytest.raises(ImageUnavailable):
        rig.gen.generate(_req())
    assert len(rig.calls) == 3
    assert rig.sleeps == [1.0, 2.0]


def test_transport_error_retries_then_fails(prompts: Path) -> None:
    cfg = _cfg()
    ledger = CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), lambda: NOW)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    sleeps: list[float] = []
    gen = GeminiImageGenerator(
        cfg,
        KEY,
        ledger,
        prompts_dir=prompts,
        transport=httpx.MockTransport(boom),
        sleep=sleeps.append,
    )
    with pytest.raises(ImageUnavailable):
        gen.generate(_req())
    assert sleeps == [1.0, 2.0]
    gen.close()


def test_other_4xx_is_rejected_without_retry(prompts: Path) -> None:
    rig = Rig(prompts, [httpx.Response(400, json={"error": {"message": "bad size"}})])
    with pytest.raises(ImageRequestRejected, match="bad size"):
        rig.gen.generate(_req())
    assert len(rig.calls) == 1


def test_no_image_part_raises_and_records_estimate(prompts: Path) -> None:
    rig = Rig(prompts, [_ok(image=False)])
    with pytest.raises(ImageNotReturned) as exc:
        rig.gen.generate(_req())
    assert exc.value.finish_reason == "STOP"
    assert exc.value.text == "here you go"
    assert "data" not in exc.value.shape or "str" in exc.value.shape
    assert rig.ledger.month_summary(NOW).by_category["image"] > 0


def test_missing_usage_records_estimate(prompts: Path) -> None:
    rig = Rig(prompts, [_ok(prompt_tokens=None)])
    out = rig.gen.generate(_req())
    assert out.estimated_cost is True
    assert out.usd > Decimal("0.05")


def test_model_without_price_is_refused_before_any_call(prompts: Path) -> None:
    rig = Rig(prompts, [_ok()])
    with pytest.raises(ConfigInvalid, match=r"images\.prices\.other-model"):
        rig.gen.generate(_req(model="other-model"))
    assert rig.calls == []


def test_size_without_price_is_refused(prompts: Path) -> None:
    rig = Rig(prompts, [_ok()], cfg=_cfg(generate_size="4K"))
    with pytest.raises(ConfigInvalid, match=r"per_image_usd\.4K"):
        rig.gen.generate(_req())
    assert rig.calls == []


def test_wrong_provider_is_refused() -> None:
    cfg = _cfg(provider="other")
    ledger = CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), lambda: NOW)
    with pytest.raises(ConfigInvalid):
        GeminiImageGenerator(cfg, KEY, ledger)


def test_key_never_logged(prompts: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(prompts, [httpx.Response(400, json={"error": {"message": f"bad key {KEY}"}})])
    with pytest.raises(ImageRequestRejected) as exc:
        rig.gen.generate(_req())
    assert KEY not in caplog.text
    assert rig.gen.last_shape is None
    del exc


def test_shape_has_no_image_bytes(prompts: Path) -> None:
    rig = Rig(prompts, [_ok()])
    rig.gen.generate(_req())
    shape = json.dumps(rig.gen.last_shape)
    assert "inlineData" in shape
    assert base64.b64encode(_png(32, 32)).decode()[:20] not in shape
