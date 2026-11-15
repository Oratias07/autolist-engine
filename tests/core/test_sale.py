"""Sale price rules (DEV 15.11.2026)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from autolist.core.models import Job, JobFact, JobState
from autolist.core.sale import parse_sale_end, sale_spec, sale_state, without_sale

TODAY = date(2026, 11, 15)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("עד 30.11", date(2026, 11, 30)),
        ("30.11.26", date(2026, 11, 30)),
        ("עד ה-30/11/2026", date(2026, 11, 30)),
        ("5-1-2027", date(2027, 1, 5)),
        ("15.11", TODAY),  # today is allowed: the sale runs the whole publish day
        ("14.11", None),  # past
        ("31.11", None),  # no such day
        ("סוף החודש", None),  # words: asked, never worked out
        ("מ-20.11 עד 30.11", None),  # two dates: asked
        ("", None),
        ("עד 130.11", None),
    ],
)
def test_parse_sale_end(text: str, want: date | None) -> None:
    assert parse_sale_end(text, TODAY) == want


def _job(**facts: str) -> Job:
    return Job(
        job_id="T-1",
        number=1,
        state=JobState.AWAITING_APPROVAL,
        source_gmail_id="g1",
        source_thread_id="t1",
        product_index=0,
        thread_id=None,
        created_at="2026-11-15T10:00:00Z",  # type: ignore[arg-type]
        updated_at="2026-11-15T10:00:00Z",  # type: ignore[arg-type]
        facts=[
            JobFact(key=k, value=v, quote=v, source="email", gmail_id="g1")
            for k, v in facts.items()
        ],
    )


@pytest.mark.parametrize(
    ("facts", "state"),
    [
        ({"price": "450"}, "none"),
        ({"price": "450", "sale_price": "399", "sale_end": "30.11"}, "ok"),
        ({"price": "450", "sale_price": "399"}, "missing_end"),
        ({"price": "450", "sale_price": "399", "sale_end": "סוף החודש"}, "missing_end"),
        ({"price": "450", "sale_price": "450", "sale_end": "30.11"}, "not_below"),
        ({"price": "450", "sale_price": "500"}, "not_below"),  # checked before the end
        ({"price": "7.90", "sale_price": "5.90", "sale_end": "30.11.2026"}, "ok"),
    ],
)
def test_sale_state(facts: dict[str, str], state: str) -> None:
    assert sale_state(_job(**facts), TODAY) == state


def test_sale_spec_starts_on_the_publish_day() -> None:
    spec = sale_spec(_job(price="450", sale_price="399", sale_end="30.11"), TODAY)
    assert spec is not None
    assert (spec.price, spec.date_start, spec.date_end) == (
        Decimal("399"),
        TODAY,
        date(2026, 11, 30),
    )
    assert sale_spec(_job(price="450"), TODAY) is None


def test_a_sale_that_is_not_valid_is_never_sent() -> None:
    with pytest.raises(ValueError, match="missing_end"):
        sale_spec(_job(price="450", sale_price="399"), TODAY)
    # the end passed while the job waited for approval
    with pytest.raises(ValueError, match="missing_end"):
        sale_spec(_job(price="450", sale_price="399", sale_end="20.11"), date(2026, 11, 21))


def test_without_sale_removes_every_sale_fact() -> None:
    job = without_sale(_job(price="450", sale_price="399", sale_end="30.11"))
    assert [f.key for f in job.facts] == ["price"]
