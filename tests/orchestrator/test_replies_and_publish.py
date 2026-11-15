"""Replies in a job thread and the publish step (P1-07, P1-08 cases 1, 3; decision 15.11)."""

from __future__ import annotations

from typing import Any

import pytest

from autolist.core.models import JobState
from autolist.images.errors import ImageRequestRejected
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import (
    EMAIL_TEXT,
    Rig,
    _MessagesById,
    classified,
    email,
    extraction,
    item,
    q,
)

S = JobState


async def _ready(printed_text: bool = False, **kw: Any) -> Rig:
    """A job with version 1 sent (preview is gmail id s2, thread jt2)."""
    rig = Rig(extraction([item(printed_text=printed_text)]), **kw)
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    return rig


async def _reply(rig: Rig, text: str, in_reply_to: str | None = "<s2@example.com>") -> Any:
    gid = f"r{len(rig.messages)}"
    rig.messages[gid] = email(text, thread=_job(rig).thread_id, in_reply_to=in_reply_to)
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gid})


async def _publish(rig: Rig) -> Any:
    return await run_task(rig.rt, {"event": "publish", "job_id": "T-1"})


def _job(rig: Rig) -> Any:
    return rig.jobs.get("T-1")


async def test_approve_then_publish_hidden_disabled_with_generated_image() -> None:
    rig = await _ready()
    assert await _reply(rig, "מאשר") == {"job_id": "T-1", "reply": "approved"}
    assert _job(rig).state == S.PUBLISHING
    assert rig.queue.tasks[-1] == ("/tasks/job", {"event": "publish", "job_id": "T-1"})

    out = await _publish(rig)
    assert out == {"job_id": "T-1", "state": S.SAVED_HIDDEN}
    posted = rig.posted
    assert posted is not None
    assert (posted["hidden"], posted["status"]) == (1, 0)
    assert posted["price"] == 450 and posted["quantity"] == 3 and posted["model"] == "C1"
    assert "sku" not in posted and "upc" not in posted
    assert posted["product_description"]["3"]["name"] == "כיסא משרדי C1"
    job = _job(rig)
    assert job.istores_product_id == 970
    done = rig.mail.sent[-1]
    assert done.template == "saved_hidden" and done.thread_id == "jt2"
    assert done.variables["istores_product_id"] == "970" and done.variables["version"] == "1"
    assert rig.blobs.objects == {}  # the uploaded image is deleted after the create


async def test_approve_of_an_older_version_does_not_publish() -> None:
    rig = await _ready()
    await _reply(rig, "תמונה 1")  # version 2, preview s3
    assert await _reply(rig, "מאשר", in_reply_to="<s2@example.com>") == {
        "job_id": "T-1",
        "reply": "old_version",
    }
    msg = rig.mail.sent[-1]
    assert msg.template == "approve_old_version"
    assert (msg.variables["old_version"], msg.variables["version"]) == ("1", "2")
    assert _job(rig).state == S.AWAITING_APPROVAL
    assert all(t[1]["event"] != "publish" for t in rig.queue.tasks)


@pytest.mark.parametrize("in_reply_to", [None, "<other@example.com>"])
async def test_approve_not_answering_a_preview_does_not_publish(in_reply_to: str | None) -> None:
    rig = await _ready()
    out = await _reply(rig, "מאשר", in_reply_to=in_reply_to)
    assert out["reply"] == "not_a_preview_reply"
    assert rig.mail.sent[-1].template == "approve_reply_to_preview"
    assert _job(rig).state == S.AWAITING_APPROVAL


@pytest.mark.parametrize("text", ["מאשר אבל המחיר 480", "לא מאשר"])
async def test_approve_with_other_text_never_approves(text: str) -> None:
    rig = await _ready()
    assert (await _reply(rig, text))["reply"] == "approve_with_text"
    assert rig.mail.sent[-1].template == "approve_with_text"
    assert _job(rig).state == S.AWAITING_APPROVAL


async def test_cancel() -> None:
    rig = await _ready()
    assert (await _reply(rig, "בטל"))["reply"] == "cancelled"
    job = _job(rig)
    assert job.state == S.CANCELLED and job.error == "cancelled by the owner"
    assert rig.mail.sent[-1].template == "cancelled"
    # a later reply in a final job gets its status, nothing else
    assert (await _reply(rig, "מאשר"))["reply"] == "status"
    assert (
        rig.mail.sent[-1].variables["status_lines"] == "T-1 \N{EN DASH} כיסא משרדי C1: s-cancelled"
    )


async def test_choose_image_1_makes_version_2_with_both_images() -> None:
    rig = await _ready()
    assert (await _reply(rig, "תמונה 1"))["reply"] == "image_1"
    job = _job(rig)
    assert [v.number for v in job.versions] == [1, 2]
    v2 = job.versions[1]
    assert (v2.image_choice, v2.image_options) == (1, [1, 2])
    assert v2.title == job.versions[0].title and job.revision_count == 1
    preview = rig.mail.sent[-1]
    assert preview.variables["image_note"] == "<image_note_owner_choice>"
    assert preview.variables["version"] == "2" and preview.thread_id == "jt2"
    assert [a.filename for a in preview.attachments] == ["image-1.jpg", "image-2.jpg"]
    # image 2 was read back from the first preview, not generated again
    assert len(rig.images.requests) == 1
    assert preview.attachments[1].data == rig.mail.sent[1].attachments[0].data


async def test_approve_image_1_uploads_the_owner_photo() -> None:
    rig = await _ready()
    await _reply(rig, "תמונה 1")
    await _reply(rig, "מאשר", in_reply_to="<s3@example.com>")
    assert (await _publish(rig))["state"] == S.SAVED_HIDDEN
    assert rig.mail.sent[-1].variables["version"] == "2"


async def test_image_2_without_a_generated_image() -> None:
    rig = Rig(extraction([item()]))
    rig.images.error = ImageRequestRejected("blocked")
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    assert (await _reply(rig, "תמונה 2", in_reply_to=None))["reply"] == "no_image_2"
    assert rig.mail.sent[-1].template == "image_2_unavailable"


async def test_revision_limit_goes_to_the_developer() -> None:
    rig = await _ready()
    for n in range(3):
        await _reply(rig, "תמונה 1" if n % 2 == 0 else "תמונה 2")
    assert len(_job(rig).versions) == 4
    assert (await _reply(rig, "תמונה 1"))["reply"] == "revision_limit"
    assert rig.mail.templates()[-2:] == ["developer_alert", "change_not_supported"]


async def test_approve_while_not_waiting_gets_status() -> None:
    rig = await _ready()
    await _reply(rig, "מאשר")
    assert (await _reply(rig, "מאשר"))["reply"] == "status"
    assert rig.mail.sent[-1].variables["status_lines"].endswith(": s-publishing")
    assert sum(1 for t in rig.queue.tasks if t[1]["event"] == "publish") == 1


async def test_verify_mismatch_fails_job_and_keeps_product_hidden() -> None:
    rig = await _ready()
    rig.store_overrides = {"price": "1"}
    await _reply(rig, "מאשר")
    with pytest.raises(Exception, match="price"):
        await _publish(rig)
    job = _job(rig)
    assert job.state == S.FAILED and "970" in (job.error or "")
    assert rig.mail.templates()[-2:] == ["failed", "developer_alert"]
    assert rig.blobs.objects == {}


async def test_publish_refuses_a_preview_not_sent_by_the_agent() -> None:
    rig = await _ready()
    await _reply(rig, "מאשר")
    forged = {**rig.messages, "s2": email("forged", thread="jt2")}  # inbound, not SENT
    rig.rt.gmail = lambda: _MessagesById(forged)
    with pytest.raises(Exception, match="not a message the agent sent"):
        await _publish(rig)
    assert _job(rig).state == S.FAILED
    assert rig.posted is None


async def test_stale_publish_task_is_skipped() -> None:
    rig = await _ready()
    assert await _publish(rig) == {"skipped": True}
    assert rig.posted is None


async def test_approve_answering_a_later_agent_email_approves_the_latest() -> None:
    """P1-08: the owner answered the latest agent email ("לא נשמר"), not the preview."""
    rig = await _ready()
    await _reply(rig, "מאשר אבל המחיר 480")  # approve_with_text, sent after the preview
    later = rig.mail.meta[-1][0].rfc_message_id
    assert (await _reply(rig, "מאשר", in_reply_to=later))["reply"] == "approved"
    assert _job(rig).state == S.PUBLISHING


async def test_approve_answering_an_email_sent_before_the_preview_does_not_approve() -> None:
    rig = Rig(extraction([item(price=q())]))
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    ask = rig.mail.meta[-1][0].rfc_message_id  # needs_info, before any preview
    job = _job(rig)
    rig.llm.classify = classified("facts", price=q("450", "450"))
    rig.messages["a1"] = email("450", thread=job.thread_id, in_reply_to=ask)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "a1"})
    rig.clock.advance(seconds=5)
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    assert (await _reply(rig, "מאשר", in_reply_to=ask))["reply"] == "not_a_preview_reply"
    assert _job(rig).state == S.AWAITING_APPROVAL


async def test_a_non_agent_message_with_a_known_id_does_not_count() -> None:
    rig = await _ready()
    forged = email("x", thread=_job(rig).thread_id)
    forged.update({"id": "f1", "threadId": _job(rig).thread_id, "internalDate": "9" * 13})
    forged["rfc"] = "<forged@example.com>"
    rig.messages["f1"] = forged  # inbound, not SENT
    assert (await _reply(rig, "מאשר", in_reply_to="<forged@example.com>"))["reply"] == (
        "not_a_preview_reply"
    )


async def test_item_code_is_sent_as_the_sku() -> None:
    rig = Rig(extraction([item(item_code=q("C1", "C1"))]))
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    await _reply(rig, "מאשר")
    await _publish(rig)
    assert rig.posted is not None and rig.posted["sku"] == "C1"


async def test_chosen_category_is_in_the_preview_and_sent_on_create() -> None:
    rig = await _ready()
    preview = rig.mail.sent[1]
    assert preview.variables["category"] == "ריהוט > כיסאות"
    await _reply(rig, "מאשר")
    await _publish(rig)
    assert rig.posted is not None and rig.posted["product_category"] == [11]


async def test_no_category_shows_the_none_fragment_and_sends_none() -> None:
    rig = Rig(extraction([item()]))
    rig.llm.category = {"primary": None, "secondary": None, "clear": False, "question": "?"}
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    assert rig.mail.sent[-1].variables["category"] == "<_category_none>"
    await _reply(rig, "מאשר")
    await _publish(rig)
    assert rig.posted is not None and "product_category" not in rig.posted
