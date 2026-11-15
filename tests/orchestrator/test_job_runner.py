"""Job runner, fast-track acceptance cases that need no reply handling (P1-04/P1-06/P1-07)."""

from __future__ import annotations

from typing import Any

import pytest

from autolist.core.errors import CostCapExceeded
from autolist.core.models import JobState
from autolist.images.errors import BillingRequired, ImageNotReturned
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import EMAIL_TEXT, Rig, email, extraction, item, q

S = JobState


def _rig(extract: dict[str, Any], **kw: Any) -> Rig:
    rig = Rig(extract, **kw)
    rig.messages["g1"] = email(EMAIL_TEXT)
    return rig


async def _email(rig: Rig, gmail_id: str = "g1") -> dict[str, Any]:
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gmail_id})


async def _generate(rig: Rig, job_id: str = "T-1") -> dict[str, Any]:
    return await run_task(rig.rt, {"event": "generate", "job_id": job_id})


async def test_full_email_opens_job_sends_receipt_and_queues_generation() -> None:
    rig = _rig(extraction([item()]))
    assert await _email(rig) == {"opened": ["T-1"]}
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.GENERATING
    assert [f.key for f in job.facts] == ["name", "price", "quantity", "model"]
    assert job.photo_indexes == [1] and job.printed_text is False
    assert (job.source_subject, job.source_thread_id) == ("מוצר חדש", "t0")
    assert rig.mail.templates() == ["received"]
    receipt = rig.mail.sent[0]
    assert receipt.thread_id == "t0" and receipt.in_reply_to is not None
    assert receipt.variables["jobs_lines"] == "T-1 \N{EN DASH} כיסא משרדי"
    assert receipt.variables["wait_minutes"] == "10"
    assert receipt.variables["developer_contact"] == "dev@example.com"
    assert receipt.variables["rejected_attachments"] == "אין"
    assert rig.queue.tasks == [("/tasks/job", {"event": "generate", "job_id": "T-1"})]


async def test_generate_sends_preview_with_generated_image() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    out = await _generate(rig)
    assert out == {"job_id": "T-1", "state": S.AWAITING_APPROVAL}
    job = rig.jobs.get("T-1")
    assert job is not None
    preview = rig.mail.sent[-1]
    assert preview.template == "ready_for_approval"
    assert preview.thread_id is None  # opens the job's own thread
    assert preview.variables["image_note"] == "<image_note_generated>"
    assert preview.variables["price"] == "450"
    assert [a.filename for a in preview.attachments] == ["image-2.jpg"]
    assert job.thread_id == "jt2" and rig.jobs.by_thread("jt2") == job
    v = job.versions[0]
    assert (v.number, v.image_choice, v.image_options) == (1, 2, [2])
    assert v.preview_gmail_id == "s2" and v.preview_rfc_message_id == "<s2@example.com>"
    assert "<p><strong>כיסא משרדי לעבודה</strong></p>" in v.description_html
    assert rig.llm.prompts == ["extract_request", "write_title", "write_description"]
    req = rig.images.requests[0]
    assert (req.job_id, req.model, req.prompt_name) == ("T-1", "image-model-a", "image_main")
    assert "450" not in req.variables["product_facts"]  # the price is never in a prompt


async def test_printed_text_preselects_own_photo() -> None:
    rig = _rig(extraction([item(printed_text=True)]))
    await _email(rig)
    await _generate(rig)
    preview = rig.mail.sent[-1]
    assert preview.variables["image_note"] == "<image_note_printed_text>"
    assert [a.filename for a in preview.attachments] == ["image-1.jpg", "image-2.jpg"]


@pytest.mark.parametrize(
    "error",
    [
        ImageNotReturned("no image", finish_reason="SAFETY", text="", shape=None),
        BillingRequired("402"),
    ],
)
async def test_image_failure_falls_back_to_own_photo(error: Exception) -> None:
    rig = _rig(extraction([item()]))
    rig.images.error = error
    await _email(rig)
    out = await _generate(rig)
    assert out["state"] == S.AWAITING_APPROVAL
    preview = next(m for m in rig.mail.sent if m.template == "ready_for_approval")
    assert preview.variables["image_note"] == "<image_note_fallback>"
    assert [a.filename for a in preview.attachments] == ["image-1.jpg"]
    alerts = [m for m in rig.mail.sent if m.template == "developer_alert"]
    assert len(alerts) == 1 and alerts[0].to == "dev@example.com"


async def test_missing_price_asks_and_generates_nothing() -> None:
    rig = _rig(extraction([item(price=q())]))
    await _email(rig)
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.NEEDS_INFO
    assert rig.mail.templates() == ["received", "needs_info"]
    ask = rig.mail.sent[1]
    assert ask.thread_id is None and ask.variables["questions_list"] == "1. מחיר"
    assert job.thread_id == "jt2"
    assert rig.queue.tasks == []
    assert rig.llm.prompts == ["extract_request"]
    assert rig.images.requests == []


async def test_value_without_exact_quote_is_asked_not_used() -> None:
    rig = _rig(extraction([item(quantity=q(5, "חמש"))]))
    await _email(rig)
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.NEEDS_INFO
    assert job.fact("quantity") is None
    assert rig.mail.sent[1].variables["questions_list"] == "1. כמות במלאי"


async def test_model_question_on_single_product_is_asked() -> None:
    rig = _rig(extraction([item()], questions=["בתמונה הכיסא כחול. מה הצבע?"]))
    await _email(rig)
    assert rig.mail.sent[1].variables["questions_list"] == "1. בתמונה הכיסא כחול. מה הצבע?"


async def test_two_products_open_two_jobs_with_their_own_photos() -> None:
    a = item(photo_indexes=[1])
    b = item(name=q("שולחן", "שולחן"), model=q("D2", "D2"), photo_indexes=[2])
    rig = Rig(extraction([a, b]))
    rig.messages["g1"] = email("כיסא משרדי C1 450 3 יחידות, שולחן D2", photos=2)
    assert await _email(rig) == {"opened": ["T-1", "T-2"]}
    assert len(rig.queue.tasks) == 2
    assert rig.mail.sent[0].variables["jobs_lines"].splitlines()[1] == "T-2 \N{EN DASH} שולחן"
    assert rig.jobs.get("T-2").photo_indexes == [2]  # type: ignore[union-attr]


async def test_unclear_products_open_no_job() -> None:
    rig = _rig(
        extraction(
            [item(), item(model=q("D2", "D2"))],
            products_clearly_separated=False,
            questions=["לאיזה מוצר שייך המחיר 450?"],
        )
    )
    assert await _email(rig) == {"opened": []}
    assert rig.mail.templates() == ["clarify_products"]
    assert rig.mail.sent[0].variables["questions_list"] == "1. לאיזה מוצר שייך המחיר 450?"
    assert rig.jobs.jobs == {}


async def test_unclear_without_a_question_alerts_developer_and_fails_closed() -> None:
    rig = _rig(extraction([item()], products_clearly_separated=False))
    with pytest.raises(RuntimeError):
        await _email(rig)
    assert rig.mail.templates() == ["developer_alert"]
    assert rig.jobs.jobs == {}


async def test_no_product_and_too_many() -> None:
    rig = _rig(extraction([]))
    assert await _email(rig) == {"opened": []}
    assert rig.mail.templates() == ["no_product_found"]

    rig = _rig(extraction([item() for _ in range(11)]))
    assert await _email(rig) == {"opened": []}
    assert rig.mail.templates() == ["too_many_products"]
    assert rig.mail.sent[0].variables == {
        "original_subject": "מוצר חדש",
        "products_found": "11",
        "max_products": "10",
    }


async def test_duplicate_model_cancels_before_any_paid_text_or_image() -> None:
    rig = _rig(extraction([item()]), catalog=[{"sku": "", "model": "C1", "product_id": 77}])
    await _email(rig)
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.CANCELLED and job.error == "duplicate model"
    assert rig.mail.templates() == ["received", "duplicate_found"]
    dup = rig.mail.sent[1]
    assert dup.thread_id == "t0"
    assert dup.variables["match_field"] == "דגם" and dup.variables["existing_product_id"] == "77"
    assert rig.queue.tasks == []
    assert rig.llm.prompts == ["extract_request"]


async def test_reply_in_receipt_thread_points_to_product_threads() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    rig.messages["g2"] = email("מאשר", thread="t0")
    assert await _email(rig, "g2") == {"opened": []}
    assert rig.mail.templates()[-1] == "reply_in_product_thread"
    assert rig.llm.prompts == ["extract_request"]  # not extracted again


async def test_text_check_failure_fails_job_and_tells_both() -> None:
    rig = _rig(extraction([item()]))
    rig.llm.title = rig.llm.title.model_copy(update={"title": "כיסא משרדי C1!"})
    await _email(rig)
    with pytest.raises(RuntimeError, match="text checks failed"):
        await _generate(rig)
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.FAILED
    assert rig.mail.templates()[-2:] == ["failed", "developer_alert"]
    assert rig.mail.sent[-2].variables["developer_contact"] == "dev@example.com"
    assert rig.images.requests == []


async def test_monthly_cap_sends_budget_failure() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    rig.llm.error = CostCapExceeded("monthly cap", monthly=True)
    with pytest.raises(CostCapExceeded):
        await _generate(rig)
    failed = [m for m in rig.mail.sent if m.template == "failed_budget"]
    assert len(failed) == 1 and failed[0].variables["reset_date"] == "01.12.2026"


async def test_per_job_cap_is_an_ordinary_failure() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    rig.llm.error = CostCapExceeded("per-job cap", monthly=False)
    with pytest.raises(CostCapExceeded):
        await _generate(rig)
    assert "failed" in rig.mail.templates() and "failed_budget" not in rig.mail.templates()


async def test_stale_generate_task_is_skipped() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    await _generate(rig)
    assert await _generate(rig) == {"skipped": True}
    assert await _generate(rig, "T-404") == {"skipped": True}


async def test_unknown_event_refused() -> None:
    rig = _rig(extraction([item()]))
    with pytest.raises(ValueError, match="unknown job task event"):
        await run_task(rig.rt, {"event": "publish_everything"})


async def test_developer_alert_and_job_error_are_redacted() -> None:
    from autolist.core.logging import register_secret

    marker = "REDACT-ME-MARKER"
    register_secret(marker)
    rig = _rig(extraction([item()]))
    await _email(rig)
    rig.llm.error = RuntimeError(f"upstream said {marker}")
    with pytest.raises(RuntimeError):
        await _generate(rig)
    alert = rig.mail.sent[-1]
    assert alert.template == "developer_alert" and marker not in alert.variables["detail"]
    assert marker not in (rig.jobs.get("T-1").error or "")  # type: ignore[union-attr]


def test_original_subject_drops_reply_prefixes() -> None:
    from autolist.orchestrator.runtime import original_subject

    assert original_subject("Re: RE: Fwd: [T-1] כיסא") == "[T-1] כיסא"
    assert original_subject("מוצר חדש") == "מוצר חדש"
    assert original_subject(None) == ""


def _spend(rig: Rig, usd: str) -> None:
    from decimal import Decimal

    from autolist.core.models import CostEntry

    rig.ledger.record(
        CostEntry(
            at=rig.clock.now,
            job_id=None,
            provider="anthropic",
            category="llm",
            operation="extract",
            units=1,
            unit="tokens",
            usd=Decimal(usd),
            estimated=False,
        )
    )


async def test_threshold_crossed_in_a_step_sends_one_cost_alert() -> None:
    rig = _rig(extraction([item()]))
    _spend(rig, "30.50")
    await _email(rig)
    alerts = [m for m in rig.mail.sent if m.template == "cost_alert"]
    assert len(alerts) == 1 and alerts[0].to == "owner@example.com"
    await _generate(rig)
    assert sum(1 for m in rig.mail.sent if m.template == "cost_alert") == 1  # not again


async def test_cap_reached_sends_budget_email_even_when_the_step_fails() -> None:
    rig = _rig(extraction([item()]))
    await _email(rig)
    _spend(rig, "60")
    rig.llm.error = CostCapExceeded("monthly cap", monthly=True)
    with pytest.raises(CostCapExceeded):
        await _generate(rig)
    templates = rig.mail.templates()
    assert "failed_budget" in templates and templates.count("budget_cap_reached") == 1


async def test_cost_alert_send_failure_does_not_fail_the_step() -> None:
    rig = _rig(extraction([item()]))
    _spend(rig, "30.50")
    rig.mail.fail_on = "cost_alert"
    assert await _email(rig) == {"opened": ["T-1"]}
    rig.mail.fail_on = None
    await _generate(rig)  # the alert was put back and goes out after the next step
    assert sum(1 for m in rig.mail.sent if m.template == "cost_alert") == 1


async def test_item_code_without_a_model_is_enough_and_fills_the_model_field() -> None:
    """DEV 15.11.2026 (YTS-1002): a model or an item code is required, not both."""
    rig = Rig(extraction([item(model=q(), item_code=q("C1", "C1"))]))
    rig.messages["g1"] = email(EMAIL_TEXT)
    assert await _email(rig) == {"opened": ["T-1"]}
    assert rig.jobs.get("T-1").state == S.GENERATING  # type: ignore[union-attr]
    await _generate(rig)
    preview = rig.mail.sent[-1]
    assert preview.template == "ready_for_approval" and preview.variables["model"] == "C1"


async def test_neither_model_nor_item_code_asks_once_for_either() -> None:
    rig = Rig(extraction([item(model=q())]))
    rig.messages["g1"] = email(EMAIL_TEXT)
    await _email(rig)
    assert rig.mail.sent[1].variables["questions_list"] == "1. model-or-item-code-label"
