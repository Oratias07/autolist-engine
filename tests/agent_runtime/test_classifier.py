from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from autolist.agent_runtime.classifier import ClassifiedReply, classify_reply
from autolist.core.models import ChannelMessage, ReplyIntent
from autolist.llm.client import LLMRequest, LLMResult


def _q(value: Any = None, quote: str | None = None) -> dict[str, Any]:
    return {"value": value, "quote": quote}


def _out(intent: str = "facts", **facts: Any) -> dict[str, Any]:
    base = {k: _q() for k in ("name", "price", "sale_price", "quantity", "model", "barcode")}
    base["keywords"] = _q()
    base["item_code"] = _q()
    base["details"] = []
    base.update(facts)
    return {"intent": intent, "facts": base, "also_wording_or_image": False, "question": ""}


class FakeLLM:
    def __init__(self, out: dict[str, Any]) -> None:
        self.out = out
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        parsed = ClassifiedReply.model_validate_json(json.dumps(self.out))
        return LLMResult(
            parsed=parsed, text=None, model="m", input_tokens=1, output_tokens=1, usd=Decimal(0)
        )


def _msg(text: str) -> ChannelMessage:
    return ChannelMessage(
        channel="email",
        message_id="r1",
        thread_id="jt",
        sender="owner@example.com",
        authenticated=True,
        received_at=datetime(2026, 11, 15, tzinfo=UTC),
        subject=None,
        text=text,
    )


def _run(text: str, out: dict[str, Any]) -> Any:
    llm = FakeLLM(out)
    res = classify_reply(_msg(text), llm=llm, job_context="ctx", job_id="T-1")
    return res, llm


def test_price_correction_with_quote() -> None:
    res, llm = _run("המחיר 480", _out(price=_q("480", "480")))
    assert res.intent == ReplyIntent.FACTS
    assert [(f.key, f.value, f.source, f.gmail_id) for f in res.facts] == [
        ("price", "480", "reply", "r1")
    ]
    req = llm.requests[0]
    assert (req.prompt, req.model_role, req.job_id) == ("classify_reply", "classify", "T-1")
    assert req.variables == {"reply_text": "המחיר 480", "job_context": "ctx"}


def test_value_without_exact_quote_is_dropped() -> None:
    res, _ = _run("המחיר ארבע מאות שמונים", _out(price=_q("480", "480")))
    assert res.facts == [] and res.unverified_fields == ["price"]
    assert res.intent == ReplyIntent.FACTS


def test_facts_intent_with_nothing_stated_becomes_unclear() -> None:
    res, _ = _run("תעשה מה שצריך", _out())
    assert res.intent == ReplyIntent.UNCLEAR


def test_other_intents_pass_through() -> None:
    for intent in ("status", "wording_or_image", "other", "unclear"):
        res, _ = _run("x", _out(intent))
        assert res.intent == intent


def test_details_from_an_answer_to_copied_questions() -> None:
    text = 'מהן מידות המברשת? - אורך 40 מ"מ\nלאילו משטחים? - מיועדת לכל סוגי המשטחים'  # noqa: RUF001
    res, _ = _run(
        text,
        _out(
            details=[
                _q('אורך 40 מ"מ', 'אורך 40 מ"מ'),
                _q("מיועדת לכל סוגי המשטחים", "מיועדת לכל סוגי המשטחים"),
            ]
        ),
    )
    assert res.intent == ReplyIntent.FACTS
    assert [(f.key, f.value, f.source) for f in res.facts] == [
        ("detail", 'אורך 40 מ"מ', "reply"),
        ("detail", "מיועדת לכל סוגי המשטחים", "reply"),
    ]
