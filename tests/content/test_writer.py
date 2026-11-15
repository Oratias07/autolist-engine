from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from autolist.content.writer import (
    DescriptionOut,
    TitleOut,
    check,
    render,
    write_listing,
    writer_facts,
)
from autolist.core.models import JobFact
from autolist.llm.client import LLMRequest, LLMResult

SECTIONS: list[dict[str, Any]] = [
    {"id": "headline", "render": "<p><strong>{headline}</strong></p>"},
    {"id": "intro", "sentences": 2},
    {"id": "specs", "heading": "מפרט טכני:"},
    {"id": "benefits", "heading": "יתרונות עיקריים:"},
]
PLAYBOOK: dict[str, Any] = {"title": {"pattern": "x"}, "description": {"sections": SECTIONS}}
GLOSSARY: dict[str, Any] = {"terms": [{"term": "כיסא"}]}


def _f(key: str, value: str) -> JobFact:
    return JobFact(key=key, value=value, quote=value, source="email", gmail_id="g1")


FACTS = [
    _f("name", "כיסא משרדי"),
    _f("price", "199"),
    _f("quantity", "4"),
    _f("model", "CH-200"),
]


def _title(**over: Any) -> TitleOut:
    data: dict[str, Any] = {
        "title": "כיסא משרדי CH-200",
        "fact_ids": ["F1", "F2"],
        "open_points": [],
    }
    return TitleOut.model_validate({**data, **over})


def _desc(**over: Any) -> DescriptionOut:
    data: dict[str, Any] = {
        "headline": "כיסא משרדי לעבודה יומיומית",
        "intro": ["משפט ראשון.", "משפט שני."],
        "specs": [{"label": "דגם", "value": "CH-200", "fact_ids": ["F2"]}],
        "benefits": [
            {"title": "נוחות", "sentence": "מתאים לעבודה", "fact_ids": ["F1"]},
            {"title": "סדר", "sentence": "שומר על סביבת עבודה מסודרת.", "fact_ids": ["F1"]},
            {"title": "<b>פשטות</b>", "sentence": "קל לשימוש", "fact_ids": ["F1"]},
        ],
        "open_points": [],
    }
    return DescriptionOut.model_validate({**data, **over})


class FakeLLM:
    def __init__(self, title: TitleOut, desc: DescriptionOut) -> None:
        self.out = {"write_title": title, "write_description": desc}
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        return LLMResult(
            parsed=self.out[req.prompt],
            text=None,
            model="m",
            input_tokens=1,
            output_tokens=1,
            usd=Decimal(0),
        )


def test_writer_never_sees_price_or_quantity() -> None:
    numbered = writer_facts(FACTS)
    assert numbered == [
        {"id": "F1", "text": "name: כיסא משרדי"},
        {"id": "F2", "text": "model: CH-200"},
    ]


def test_latest_fact_per_key_wins() -> None:
    numbered = writer_facts([*FACTS, _f("model", "CH-300")])
    assert numbered[1]["text"] == "model: CH-300"


def test_clean_output_has_no_problems() -> None:
    facts = {f["id"]: f["text"] for f in writer_facts(FACTS)}
    assert check(facts, _title(), _desc()) == []


@pytest.mark.parametrize(
    ("title", "desc", "problem"),
    [
        (_title(fact_ids=["F9"]), _desc(), "unknown fact id F9"),
        (_title(title="כיסא 5 גלגלים"), _desc(), "title numbers"),
        (
            _title(),
            _desc(specs=[{"label": "גובה", "value": "120 cm", "fact_ids": ["F2"]}]),
            "spec 'גובה'",
        ),
        (_title(), _desc(headline="כיסא עם 5 גלגלים"), "free text"),
        (_title(title="כיסא משרדי CH-200!"), _desc(), "exclamation mark"),
        (_title(), _desc(headline="כיסא \U0001f600"), "emoji"),
        (_title(), _desc(benefits=[]), "fewer than 3 benefits"),
    ],
)
def test_checks(title: TitleOut, desc: DescriptionOut, problem: str) -> None:
    facts = {f["id"]: f["text"] for f in writer_facts(FACTS)}
    assert any(problem in p for p in check(facts, title, desc))


def test_fewer_benefits_with_open_point_is_allowed() -> None:
    facts = {f["id"]: f["text"] for f in writer_facts(FACTS)}
    assert check(facts, _title(), _desc(benefits=[], open_points=["חסר מידע"])) == []


def test_render_escapes_model_strings_and_follows_sections() -> None:
    body_html, body_text = render(_desc(), SECTIONS)
    assert body_html.startswith("<p><strong>כיסא משרדי לעבודה יומיומית</strong></p>")
    assert "&lt;b&gt;פשטות&lt;/b&gt;" in body_html
    assert "<b>" not in body_html
    assert "<li><strong>נוחות:</strong> מתאים לעבודה.</li>" in body_html
    assert "- דגם: CH-200" in body_text.splitlines()


def test_unknown_section_refused() -> None:
    with pytest.raises(ValueError, match="unknown description section"):
        render(_desc(), [{"id": "faq"}])


def test_write_listing_runs_both_prompts_on_write_model() -> None:
    llm = FakeLLM(_title(open_points=["צבע"]), _desc(open_points=["צבע", "חומר"]))
    out = write_listing(
        FACTS, llm=llm, playbook=PLAYBOOK, glossary=GLOSSARY, voice="ענייני", job_id="YTS-1001"
    )
    assert [r.prompt for r in llm.requests] == ["write_title", "write_description"]
    assert all(r.model_role == "write" and r.job_id == "YTS-1001" for r in llm.requests)
    assert all("199" not in r.variables["facts_json"] for r in llm.requests)
    assert all(not r.images for r in llm.requests)
    assert out.title == "כיסא משרדי CH-200"
    assert out.open_points == ["צבע", "חומר"]
    assert out.problems == []


def test_details_reach_the_writer_and_the_item_code_does_not() -> None:
    facts = [
        *FACTS,
        _f("item_code", "1571"),
        _f("detail", 'אורך 40 מ"מ'),
        _f("detail", "לכל סוגי המשטחים"),
        _f("detail", 'אורך 40 מ"מ'),
    ]
    texts = [f["text"] for f in writer_facts(facts)]
    assert texts == [
        "name: כיסא משרדי",
        "model: CH-200",
        'detail: אורך 40 מ"מ',
        "detail: לכל סוגי המשטחים",
    ]


def test_item_code_stands_in_for_a_missing_model() -> None:
    facts = [_f("name", "שקיות אשפה"), _f("item_code", "301498")]
    assert [f["text"] for f in writer_facts(facts)] == ["name: שקיות אשפה", "model: 301498"]
