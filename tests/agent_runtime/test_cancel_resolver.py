from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from autolist.agent_runtime.cancel_resolver import (
    CancelUnclear,
    ResolvedCancel,
    has_cancel_word,
    resolve_cancel,
)
from autolist.core.models import ChannelMessage
from autolist.llm.client import LLMRequest, LLMResult


class FakeLLM:
    def __init__(self, out: dict[str, Any]) -> None:
        self.out, self.requests = out, []  # type: list[LLMRequest]

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        return LLMResult(
            parsed=ResolvedCancel.model_validate(self.out),
            text=None,
            model="m",
            input_tokens=1,
            output_tokens=1,
            usd=Decimal(0),
        )


def _msg(text: str) -> ChannelMessage:
    return ChannelMessage.model_validate(
        {
            "channel": "email",
            "message_id": "g",
            "thread_id": "t",
            "sender": "o@example.com",
            "authenticated": True,
            "subject": "s",
            "text": text,
            "attachments": [],
            "received_at": "2026-11-22T10:00:00Z",
        }
    )


@pytest.mark.parametrize(
    ("text", "has"),
    [
        ("בטל את שני המוצרים", True),
        ("תבטל", True),
        ("לבטל", True),
        ("ביטול", True),
        ("מאשר", False),
        ("מבוטל?", False),
    ],
)
def test_cancel_word(text: str, has: bool) -> None:
    assert has_cancel_word(text) is has


def test_numbers_outside_the_list_are_dropped_and_none_left_is_unclear() -> None:
    llm = FakeLLM({"cancel": True, "products": [0, 3, 2]})
    assert resolve_cancel(_msg("בטל"), ["a", "b"], llm=llm, job_id="T-1") == [1]
    with pytest.raises(CancelUnclear):
        resolve_cancel(
            _msg("בטל"), ["a"], llm=FakeLLM({"cancel": True, "products": [5]}), job_id="T-1"
        )
    assert llm.requests[0].variables["products"] == "1. a\n2. b"


def test_not_a_cancel_returns_nothing() -> None:
    llm = FakeLLM({"cancel": False, "products": [1]})
    assert resolve_cancel(_msg("בטל את המבצע"), ["a"], llm=llm, job_id="T-1") == []
