from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pytest

from autolist.core.models import Job, JobFact, JobState, ListingText
from autolist.review.preview import PreviewError, build_preview

NOW = datetime(2026, 11, 15, tzinfo=UTC)
LABELS = {"barcode": "ברקוד", "keywords": "מילות חיפוש", "_none": "אין"}


def _f(key: str, value: str) -> JobFact:
    return JobFact(key=key, value=value, quote=value, source="email", gmail_id="g1")


def _job(printed_text: bool | None = False, **over: Any) -> Job:
    data: dict[str, Any] = {
        "job_id": "YTS-1001",
        "number": 1001,
        "state": JobState.GENERATING,
        "source_gmail_id": "g1",
        "source_thread_id": "t0",
        "product_index": 0,
        "thread_id": "t1",
        "facts": [_f("name", "כיסא"), _f("price", "199"), _f("quantity", "4"), _f("model", "C1")],
        "printed_text": printed_text,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return Job.model_validate({**data, **over})


def _text(**over: Any) -> ListingText:
    data: dict[str, Any] = {
        "title": "כיסא משרדי C1",
        "description_html": "<p>x</p>",
        "description_text": "x",
        "open_points": [],
        "problems": [],
    }
    return ListingText.model_validate({**data, **over})


def _build(job: Job, image_2: bytes | None = b"two", choice: Literal[1, 2] | None = None) -> Any:
    return build_preview(
        job,
        version=1,
        text=_text(),
        image_1=b"one",
        image_2=image_2,
        owner_choice=choice,
        labels=LABELS,
        to="owner@example.com",
        in_reply_to=None,
        category="cat",
    )


def test_no_printed_text_shows_only_generated() -> None:
    p = _build(_job(False))
    assert (p.image_choice, p.image_options, p.image_note_template) == (
        2,
        [2],
        "image_note_generated",
    )
    assert [a.filename for a in p.message.attachments] == ["image-2.jpg"]
    assert p.message.attachments[0].data == b"two"


def test_printed_text_shows_both_and_preselects_own_photo() -> None:
    p = _build(_job(True))
    assert (p.image_choice, p.image_options, p.image_note_template) == (
        1,
        [1, 2],
        "image_note_printed_text",
    )
    assert [a.data for a in p.message.attachments] == [b"one", b"two"]


def test_generation_failed_falls_back_to_own_photo() -> None:
    p = _build(_job(False), image_2=None)
    assert (p.image_choice, p.image_options, p.image_note_template) == (
        1,
        [1],
        "image_note_fallback",
    )


@pytest.mark.parametrize("choice", [1, 2])
def test_owner_choice_shows_both(choice: Literal[1, 2]) -> None:
    p = _build(_job(False), choice=choice)
    assert (p.image_choice, p.image_options, p.image_note_template) == (
        choice,
        [1, 2],
        "image_note_owner_choice",
    )


def test_image_2_chosen_without_generated_image_refused() -> None:
    with pytest.raises(PreviewError):
        _build(_job(False), image_2=None, choice=2)


def test_unknown_printed_text_refused() -> None:
    with pytest.raises(PreviewError):
        _build(_job(None))


def test_text_with_problems_refused() -> None:
    with pytest.raises(PreviewError):
        build_preview(
            _job(),
            version=1,
            text=_text(problems=["emoji"]),
            image_1=b"one",
            image_2=None,
            owner_choice=None,
            labels=LABELS,
            to="o@example.com",
            in_reply_to=None,
            category="cat",
        )


def test_missing_required_fact_refused() -> None:
    job = _job(facts=[_f("name", "כיסא"), _f("price", "1"), _f("quantity", "1")])
    with pytest.raises(PreviewError, match="model"):
        _build(job)


def test_variables_use_latest_facts_and_list_missing_optionals() -> None:
    job = _job(facts=[*_job().facts, _f("price", "450"), _f("barcode", "729")])
    p = build_preview(
        job,
        version=2,
        text=_text(open_points=["צבע"]),
        image_1=b"one",
        image_2=b"two",
        owner_choice=None,
        labels=LABELS,
        to="owner@example.com",
        in_reply_to="<m1@example.com>",
        category="cat",
    )
    v = p.message.variables
    assert v["price"] == "450"
    assert v["version"] == "2"
    assert v["optional_missing"] == "מילות חיפוש"  # open points are not shown (P1-08)
    assert p.message.template == "ready_for_approval"
    assert (p.message.thread_id, p.message.in_reply_to) == ("t1", "<m1@example.com>")


def test_nothing_missing_uses_none_label() -> None:
    job = _job(facts=[*_job().facts, _f("barcode", "729"), _f("keywords", "כיסא")])
    assert _build(job).message.variables["optional_missing"] == "אין"
