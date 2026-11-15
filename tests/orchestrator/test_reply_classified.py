"""Replies that are not exact commands (classify_reply v1; P1-08 cases 4 and 5)."""

from __future__ import annotations

from typing import Any

from autolist.core.models import JobState
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import EMAIL_TEXT, Rig, classified, email, extraction, item, q

S = JobState


async def _open(rig: Rig) -> None:
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})


async def _ready(**kw: Any) -> Rig:
    rig = Rig(extraction([item(**kw)]))
    await _open(rig)
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    return rig


async def _reply(rig: Rig, text: str, out: dict[str, Any]) -> Any:
    rig.llm.classify = out
    gid = f"r{len(rig.messages)}"
    thread = rig.jobs.get("T-1").thread_id  # type: ignore[union-attr]
    rig.messages[gid] = email(text, thread=thread, in_reply_to="<s2@example.com>")
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gid})


def _job(rig: Rig) -> Any:
    return rig.jobs.get("T-1")


async def test_price_correction_makes_version_2_with_only_the_price_changed() -> None:
    rig = await _ready()
    out = await _reply(rig, "המחיר 480", classified("facts", price=q("480", "480")))
    assert out == {"job_id": "T-1", "reply": "new_version"}
    job = _job(rig)
    assert job.fact("price").value == "480" and job.fact("price").source == "reply"
    v1, v2 = job.versions
    assert (v2.number, v2.title, v2.description_html) == (2, v1.title, v1.description_html)
    preview = rig.mail.sent[-1]
    assert preview.template == "ready_for_approval"
    assert (preview.variables["price"], preview.variables["version"]) == ("480", "2")
    # the price never reaches a text prompt, so the text is not written again
    assert rig.llm.prompts.count("write_title") == 1
    assert len(rig.images.requests) == 1
    assert job.state == S.AWAITING_APPROVAL


async def test_model_correction_writes_the_text_again() -> None:
    rig = await _ready()
    await _reply(rig, "הדגם C1X", classified("facts", model=q("C1X", "C1X")))
    assert rig.llm.prompts.count("write_title") == 2
    assert _job(rig).versions[-1].number == 2


async def test_owner_image_choice_is_kept_in_the_next_version() -> None:
    rig = await _ready()
    rig.llm.classify = classified("unclear")
    thread = _job(rig).thread_id
    rig.messages["c1"] = email("תמונה 1", thread=thread)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "c1"})
    await _reply(rig, "המחיר 480", classified("facts", price=q("480", "480")))
    v3 = _job(rig).versions[-1]
    assert (v3.number, v3.image_choice, v3.owner_choice) == (3, 1, 1)


async def test_wording_request_goes_to_the_developer() -> None:
    rig = await _ready()
    out = await _reply(rig, "תקצר את התיאור", classified("wording_or_image"))
    assert out["reply"] == "change_not_supported"
    assert rig.mail.templates()[-2:] == ["developer_alert", "change_not_supported"]
    assert rig.mail.sent[-2].variables["detail"] == "תקצר את התיאור"
    assert _job(rig).state == S.AWAITING_APPROVAL and len(_job(rig).versions) == 1


async def test_status_other_and_unclear() -> None:
    rig = await _ready()
    assert (await _reply(rig, "מה קורה?", classified("status")))["reply"] == "status"
    assert (await _reply(rig, "יש לך גם שולחנות?", classified("other")))["reply"] == (
        "out_of_scope"
    )
    out = await _reply(rig, "נו?", classified("unclear", question="למה התכוונת?"))
    assert out["reply"] == "unclear"
    msg = rig.mail.sent[-1]
    assert msg.template == "reply_unclear" and msg.variables["question"] == "למה התכוונת?"
    assert _job(rig).state == S.AWAITING_APPROVAL and len(_job(rig).versions) == 1


async def test_classifier_never_sees_owner_text_in_its_context() -> None:
    rig = await _ready()
    await _reply(rig, "מה קורה?", classified("status"))
    # the context lists field names only; the product name (owner text) is not in it
    from autolist.orchestrator.reply_step import _context

    ctx = _context(rig.rt, _job(rig))
    assert "כיסא" not in ctx and "name" in ctx and "AWAITING_APPROVAL" in ctx


async def test_value_without_quote_is_asked_and_not_used() -> None:
    rig = await _ready()
    out = await _reply(rig, "המחיר ארבע מאות", classified("facts", price=q("400", "400")))
    assert out["reply"] == "needs_info"
    assert rig.mail.sent[-1].variables["questions_list"] == "1. מחיר"
    assert _job(rig).fact("price").value == "450"


async def test_needs_info_answer_moves_to_generating() -> None:
    rig = Rig(extraction([item(price=q())]))
    await _open(rig)
    assert _job(rig).state == S.NEEDS_INFO
    out = await _reply(rig, "450 שקל", classified("facts", price=q("450", "450")))
    assert out["reply"] == "generating"
    assert _job(rig).state == S.GENERATING
    assert rig.queue.tasks[-1] == ("/tasks/job", {"event": "generate", "job_id": "T-1"})


async def test_needs_info_partial_answer_asks_again() -> None:
    rig = Rig(extraction([item(price=q(), quantity=q())]))
    await _open(rig)
    out = await _reply(rig, "450", classified("facts", price=q("450", "450")))
    assert out["reply"] == "needs_info"
    assert rig.mail.sent[-1].variables["questions_list"] == "1. כמות במלאי"
    assert _job(rig).state == S.NEEDS_INFO


async def test_needs_info_answer_with_duplicate_model_cancels() -> None:
    rig = Rig(extraction([item(model=q())]), catalog=[{"sku": "", "model": "C9", "product_id": 5}])
    await _open(rig)
    out = await _reply(rig, "דגם C9", classified("facts", model=q("C9", "C9")))
    assert out["reply"] == "duplicate"
    assert _job(rig).state == S.CANCELLED
    assert rig.queue.tasks == []


async def test_facts_while_generating_get_status() -> None:
    rig = Rig(extraction([item()]))
    await _open(rig)  # GENERATING, no own thread yet: replies go to the receipt thread
    job = _job(rig).model_copy(update={"thread_id": "jt-x"})
    rig.jobs.save(job)
    out = await _reply(rig, "המחיר 480", classified("facts", price=q("480", "480")))
    assert out["reply"] == "status"


async def test_owner_details_in_a_reply_rewrite_the_text_not_the_images() -> None:
    rig = await _ready()
    detail = {"value": "לכל סוגי המשטחים", "quote": "לכל סוגי המשטחים"}
    out = await _reply(rig, "מיועדת לכל סוגי המשטחים", classified("facts", details=[detail]))
    assert out["reply"] == "new_version"
    job = _job(rig)
    assert [f.value for f in job.facts if f.key == "detail"] == ["לכל סוגי המשטחים"]
    assert rig.llm.prompts.count("write_description") == 2
    assert len(rig.images.requests) == 1
