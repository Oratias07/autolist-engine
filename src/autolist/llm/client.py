"""One interface for text-model calls, with an Anthropic adapter.

Every call: render the prompt from the store repo -> authorize the estimated cost ->
call the model (no tools) -> validate JSON when a schema is given (one repair attempt)
-> record the real cost. Model ids and prices come only from ``store.yaml``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autolist.core.cost_ledger import CostLedger
from autolist.core.errors import ConfigInvalid, CostCapExceeded
from autolist.core.logging import get_logger, register_secret
from autolist.core.models import CostEntry, StoreConfig
from autolist.llm.errors import LLMOutputInvalid, LLMUnavailable
from autolist.llm.prompt_loader import load_prompt, render

log = get_logger(__name__)

MAX_TRIES = 3
BACKOFF_START_S = 1.0
_RETRY_STATUS = {429, 529}

ModelRole = Literal["extract", "write", "classify"]


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    prompt: str
    variables: dict[str, str]
    output_schema: type[BaseModel] | None = None
    model_role: ModelRole
    job_id: str | None
    max_output_tokens: int = Field(gt=0)


class LLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    parsed: BaseModel | None
    text: str | None
    model: str
    input_tokens: int
    output_tokens: int
    usd: Decimal


class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResult: ...


class _Prices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)


def _prices_for(config: StoreConfig, model: str) -> _Prices:
    table: dict[str, Any] = config.require("llm.prices")
    if model not in table:
        raise ConfigInvalid(f"llm.prices.{model}", "no price for the configured model")
    try:
        return _Prices.model_validate(table[model])
    except ValidationError as exc:
        raise ConfigInvalid(f"llm.prices.{model}", str(exc.errors()[0]["msg"])) from None


def _usd(prices: _Prices, input_tokens: int, output_tokens: int) -> Decimal:
    mtok = Decimal(1_000_000)
    return (
        prices.input_usd_per_mtok * input_tokens + prices.output_usd_per_mtok * output_tokens
    ) / mtok


JSON_OUTPUT_PROMPT = "system_json_output"
JSON_REPAIR_PROMPT = "system_json_repair"


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


class AnthropicClient:
    def __init__(
        self,
        config: StoreConfig,
        api_key: str,
        ledger: CostLedger,
        *,
        prompts_dir: Path | None = None,
        http_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if config.llm.provider != "anthropic":
            raise ConfigInvalid("llm.provider", "AnthropicClient needs provider 'anthropic'")
        register_secret(api_key)
        self._config = config
        self._ledger = ledger
        self._prompts_dir = prompts_dir
        self._sleep = sleep
        self._now = now
        self._api = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client)

    # ----------------------------------------------------------------- public

    def complete(self, req: LLMRequest) -> LLMResult:
        model: str = self._config.require(f"llm.models.{req.model_role}")
        prices = _prices_for(self._config, model)
        prompt = load_prompt(req.prompt, self._prompts_dir)
        text = render(prompt, req.variables)
        if req.output_schema is not None:
            schema_json = json.dumps(req.output_schema.model_json_schema(), ensure_ascii=False)
            text += render(
                load_prompt(JSON_OUTPUT_PROMPT, self._prompts_dir), {"schema": schema_json}
            )

        messages: list[dict[str, str]] = [{"role": "user", "content": text}]
        reply, used_in, used_out = self._call(req, model, prices, messages)
        if req.output_schema is None:
            return LLMResult(
                parsed=None,
                text=reply,
                model=model,
                input_tokens=used_in,
                output_tokens=used_out,
                usd=_usd(prices, used_in, used_out),
            )

        parsed, error = self._validate(req.output_schema, reply)
        if parsed is None:
            messages += [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": render(
                        load_prompt(JSON_REPAIR_PROMPT, self._prompts_dir), {"error": error}
                    ),
                },
            ]
            reply, extra_in, extra_out = self._call(req, model, prices, messages)
            used_in, used_out = used_in + extra_in, used_out + extra_out
            parsed, error = self._validate(req.output_schema, reply)
            if parsed is None:
                raise LLMOutputInvalid(f"output failed validation twice: {error}", raw_text=reply)
        return LLMResult(
            parsed=parsed,
            text=None,
            model=model,
            input_tokens=used_in,
            output_tokens=used_out,
            usd=_usd(prices, used_in, used_out),
        )

    # ----------------------------------------------------------------- internals

    @staticmethod
    def _validate(schema: type[BaseModel], reply: str) -> tuple[BaseModel | None, str]:
        try:
            return schema.model_validate_json(_strip_fences(reply)), ""
        except ValidationError as exc:
            return None, "; ".join(
                f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()
            )

    def _call(
        self,
        req: LLMRequest,
        model: str,
        prices: _Prices,
        messages: list[dict[str, str]],
    ) -> tuple[str, int, int]:
        # Conservative estimate: UTF-8 bytes >= tokens for any text, plus the full output budget.
        est_in = sum(len(m["content"].encode("utf-8")) for m in messages)
        estimate = _usd(prices, est_in, req.max_output_tokens)
        decision = self._ledger.authorize(req.job_id, "anthropic", req.prompt, estimate)
        if not decision.allowed:
            raise CostCapExceeded(decision.reason)

        backoff = BACKOFF_START_S
        for attempt in range(1, MAX_TRIES + 1):
            try:
                response = self._api.messages.create(
                    model=model,
                    max_tokens=req.max_output_tokens,
                    messages=messages,  # type: ignore[arg-type]
                )
            except anthropic.APIStatusError as exc:
                retryable = exc.status_code in _RETRY_STATUS or exc.status_code >= 500
                if not retryable or attempt == MAX_TRIES:
                    raise LLMUnavailable(f"anthropic {exc.status_code}") from exc
                self._sleep(self._retry_after(exc) or backoff)
                backoff *= 2
                continue
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                if attempt == MAX_TRIES:
                    raise LLMUnavailable(f"anthropic: {type(exc).__name__}") from exc
                self._sleep(backoff)
                backoff *= 2
                continue

            used_in = response.usage.input_tokens
            used_out = response.usage.output_tokens
            self._ledger.record(
                CostEntry(
                    at=self._now(),
                    job_id=req.job_id,
                    provider="anthropic",
                    operation=req.prompt,
                    units=float(used_in + used_out),
                    unit="tokens",
                    usd=_usd(prices, used_in, used_out),
                    estimated=False,
                )
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            log.info(
                "llm call",
                extra={"prompt_name": req.prompt, "model": model, "in": used_in, "out": used_out},
            )
            return text, used_in, used_out
        raise LLMUnavailable("unreachable")  # pragma: no cover

    @staticmethod
    def _retry_after(exc: anthropic.APIStatusError) -> float | None:
        value = exc.response.headers.get("retry-after")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None
