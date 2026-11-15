from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from autolist.content.categories import (
    ChosenCategories,
    choose_categories,
    parse_store_categories,
)
from autolist.core.models import JobFact
from autolist.llm.client import LLMRequest, LLMResult

RAW = {
    "response": [
        {"category_id": 1, "name": "ציוד משרדי  >  עטים", "status": 1, "hidden": 0},
        {"category_id": 2, "name": "מפעלים ומסעדות", "status": 1, "hidden": 0},
        {"category_id": 3, "name": "מבצעים", "status": 1, "hidden": 0},
        {"category_id": 4, "name": "כבויה", "status": 0, "hidden": 0},
        {"category_id": 5, "name": "מוסתרת", "status": 1, "hidden": 1},
    ]
}
PLAYBOOK: dict[str, Any] = {"categories": {"never_auto": [3], "max_per_product": 2}}
FACTS = [JobFact(key="name", value="עט כדורי", quote="עט כדורי", source="email", gmail_id="g")]


class FakeLLM:
    def __init__(self, out: dict[str, Any]) -> None:
        self.out = out
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        return LLMResult(
            parsed=ChosenCategories.model_validate(self.out),
            text=None,
            model="m",
            input_tokens=1,
            output_tokens=1,
            usd=Decimal(0),
        )


def _choose(out: dict[str, Any], hint: str | None = None) -> Any:
    llm = FakeLLM({"secondary": None, "clear": True, "question": "", **out})
    res = choose_categories(
        FACTS,
        owner_hint=hint,
        categories=parse_store_categories(RAW),
        llm=llm,
        playbook=PLAYBOOK,
        job_id="T-1",
    )
    return res, llm


def test_names_are_normalized_and_only_allowed_categories_are_offered() -> None:
    res, llm = _choose({"primary": 1})
    assert (res.category_ids, res.names) == ([1], ["ציוד משרדי > עטים"])
    offered = llm.requests[0].variables["categories"]
    assert offered == "1: ציוד משרדי > עטים\n2: מפעלים ומסעדות"
    assert llm.requests[0].model_role == "classify"


@pytest.mark.parametrize("bad", [3, 4, 5, 999])
def test_a_forbidden_or_unknown_id_is_dropped(bad: int) -> None:
    res, _ = _choose({"primary": bad, "secondary": 2})
    assert res.category_ids == [2]


def test_secondary_and_duplicates() -> None:
    assert _choose({"primary": 1, "secondary": 2})[0].category_ids == [1, 2]
    assert _choose({"primary": 1, "secondary": 1})[0].category_ids == [1]


def test_unclear_means_no_category() -> None:
    res, _ = _choose({"primary": 1, "clear": False, "question": "לאיזו קטגוריה?"})
    assert (res.category_ids, res.question) == ([], "לאיזו קטגוריה?")


def test_owner_hint_is_passed() -> None:
    _, llm = _choose({"primary": 2}, hint="ציוד למסעדות")
    assert llm.requests[0].variables["owner_category"] == "ציוד למסעדות"
