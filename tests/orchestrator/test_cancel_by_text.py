"""Cancel in free wording, across the products of one owner email (DEV 22.11.2026)."""

from __future__ import annotations

from typing import Any

from autolist.core.models import JobState
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import Rig, classified, email, extraction, item, q

S = JobState
TWO = "1. כיסא משרדי, דגם C1, 450, 3 יחידות\n2. שולחן, דגם C1, 900, 2 יחידות"
SECOND = item(
    name=q("שולחן", "שולחן"),
    price=q("900", "900"),
    quantity=q(2, "2 יחידות"),
    photo_indexes=[2],
)


async def _two() -> Rig:
    rig = Rig(extraction([item(), SECOND]))
    rig.messages["g1"] = email(TWO, photos=2, subject="2 מוצרים")
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    for job_id in ("T-1", "T-2"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    return rig


def _state(rig: Rig, job_id: str) -> Any:
    job = rig.jobs.get(job_id)
    assert job is not None
    return job.state


async def _write(rig: Rig, text: str, thread: str = "t0", gid: str = "c1") -> Any:
    rig.messages[gid] = email(text, thread=thread, photos=0)
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gid})


async def test_cancel_both_products_in_free_wording() -> None:
    rig = await _two()
    rig.llm.cancel = {"cancel": True, "products": [1, 2]}
    out = await _write(rig, "בטל את שני המוצרים.")
    assert out["reply"] == "cancelled"
    assert (_state(rig, "T-1"), _state(rig, "T-2")) == (S.CANCELLED, S.CANCELLED)
    answer = rig.mail.sent[-1]
    assert answer.template == "cancelled_list" and answer.thread_id == "t0"
    assert "T-1" in answer.variables["jobs_lines"] and "T-2" in answer.variables["jobs_lines"]
    req = rig.llm.requests[-1]
    assert req.prompt == "resolve_cancel" and req.model_role == "classify"
    assert "(this email)" in req.variables["products"].splitlines()[0]  # T-1 owns t0


async def test_cancel_one_product_by_its_model_or_name() -> None:
    rig = await _two()
    rig.llm.cancel = {"cancel": True, "products": [2]}
    out = await _write(rig, "בטל את השולחן")
    assert out == {"job_id": "T-2", "reply": "cancelled"}
    assert (_state(rig, "T-1"), _state(rig, "T-2")) == (S.AWAITING_APPROVAL, S.CANCELLED)


async def test_a_cancel_from_the_second_product_thread_can_name_both() -> None:
    rig = await _two()
    t2 = rig.jobs.get("T-2").thread_id  # type: ignore[union-attr]
    rig.llm.cancel = {"cancel": True, "products": [1, 2]}
    out = await _write(rig, "תבטל את כולם", thread=t2)
    assert out["reply"] == "cancelled"
    assert rig.mail.sent[-1].thread_id == t2  # the answer stays in the thread he wrote in
    assert "(this email)" in rig.llm.requests[-1].variables["products"].splitlines()[1]


async def test_a_product_that_is_no_longer_open_is_listed_with_its_state() -> None:
    rig = await _two()
    rig.llm.cancel = {"cancel": True, "products": [1]}
    await _write(rig, "בטל את הכיסא", gid="c1")
    rig.llm.cancel = {"cancel": True, "products": [1, 2]}
    await _write(rig, "בטל את שניהם", gid="c2")
    lines = rig.mail.sent[-1].variables["jobs_lines"].splitlines()
    assert lines[0].startswith("T-1") and lines[0].endswith("s-cancelled")
    assert _state(rig, "T-2") == S.CANCELLED


async def test_another_meaning_of_the_word_cancels_nothing() -> None:
    rig = await _two()
    rig.llm.cancel = {"cancel": False, "products": []}
    rig.llm.classify = classified("wording_or_image")
    out = await _write(rig, "בטל את המשפט על הגלגלים")
    assert out["reply"] != "cancelled"
    assert (_state(rig, "T-1"), _state(rig, "T-2")) == (S.AWAITING_APPROVAL,) * 2
    assert "classify_reply" in rig.llm.prompts  # it goes on as an ordinary reply


async def test_without_a_cancel_word_the_resolver_is_never_asked() -> None:
    rig = await _two()
    rig.llm.classify = classified("status")
    await _write(rig, "מה המצב?")
    assert "resolve_cancel" not in rig.llm.prompts


async def test_unclear_products_ask_and_cancel_nothing() -> None:
    rig = await _two()
    rig.llm.cancel = {"cancel": True, "products": [7]}  # not in the list: dropped
    out = await _write(rig, "בטל אותו")
    assert out["reply"] == "cancel_which" and rig.mail.sent[-1].template == "cancel_which"
    assert (_state(rig, "T-1"), _state(rig, "T-2")) == (S.AWAITING_APPROVAL,) * 2


async def test_an_old_receipt_thread_with_several_jobs_cancels_too() -> None:
    """Jobs opened before 22.11 had the owner thread as a receipt thread with no job."""
    rig = await _two()
    del rig.jobs.threads["t0"]
    rig.llm.cancel = {"cancel": True, "products": [1, 2]}
    out = await _write(rig, "בטל את שני המוצרים")
    assert out == {"opened": []}
    assert (_state(rig, "T-1"), _state(rig, "T-2")) == (S.CANCELLED, S.CANCELLED)
    assert rig.mail.sent[-1].template == "cancelled_list"
