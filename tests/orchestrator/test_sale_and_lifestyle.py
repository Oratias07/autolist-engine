"""Sale price and lifestyle image on request (DEV 15.11.2026): every case, end to end."""

from __future__ import annotations

from typing import Any

import pytest

from autolist.core.models import JobState
from autolist.images.errors import ImageRequestRejected
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import Rig, classified, email, extraction, item, q

S = JobState
SALE_TEXT = "כיסא משרדי, דגם C1, 450, 3 יחידות, מבצע 399 עד 30.11"
SALE = {"sale_price": q("399", "מבצע 399"), "sale_end": q("30.11", "עד 30.11")}
LIFE_TEXT = "כיסא משרדי, דגם C1, 450, 3 יחידות, תעשה גם תמונת אווירה"
PLAIN_TEXT = "כיסא משרדי, דגם C1, 450, 3 יחידות"


async def _open(text: str, **item_over: Any) -> Rig:
    rig = Rig(extraction([item(**item_over)]))
    rig.messages["g1"] = email(text)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    return rig


async def _ready(text: str = PLAIN_TEXT, **item_over: Any) -> Rig:
    rig = await _open(text, **item_over)
    assert _job(rig).state == S.GENERATING
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    return rig


def _job(rig: Rig) -> Any:
    return rig.jobs.get("T-1")


def _last_sent(rig: Rig) -> str:
    return f"<s{len(rig.mail.sent)}@example.com>"


async def _reply(rig: Rig, text: str, out: dict[str, Any] | None = None) -> Any:
    if out is not None:
        rig.llm.classify = out
    gid = f"r{len(rig.messages)}"
    rig.messages[gid] = email(text, thread=_job(rig).thread_id, in_reply_to=_last_sent(rig))
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gid})


async def _approve_and_publish(rig: Rig) -> dict[str, Any]:
    assert (await _reply(rig, "מאשר"))["reply"] == "approved"
    await run_task(rig.rt, {"event": "publish", "job_id": "T-1"})
    assert _job(rig).state == S.SAVED_HIDDEN
    assert rig.posted is not None
    assert rig.blobs.objects == {}  # every uploaded image is deleted after the create
    return rig.posted


def _preview(rig: Rig) -> Any:
    return next(m for m in reversed(rig.mail.sent) if m.template == "ready_for_approval")


def _names(msg: Any) -> list[str]:
    return [a.filename for a in msg.attachments]


# ------------------------------------------------------------------ sale price


async def test_sale_in_the_first_email_is_shown_and_sent_from_the_publish_day() -> None:
    rig = await _ready(SALE_TEXT, **SALE)
    assert _preview(rig).variables["sale"] == "<_sale_value sale_end=30.11.2026 sale_price=399>"
    posted = await _approve_and_publish(rig)
    # T0 is 15.11.2026 12:00 in Jerusalem: the sale starts on the publish day
    assert posted["product_special"] == [
        {"price": 399.0, "date_start": "2026-11-15", "date_end": "2026-11-30"}
    ]


async def test_no_sale_shows_none_and_sends_none() -> None:
    rig = await _ready()
    assert _preview(rig).variables["sale"] == "<_sale_none>"
    assert "product_special" not in await _approve_and_publish(rig)


async def test_sale_without_an_end_asks_for_it_then_goes_on() -> None:
    rig = await _open(SALE_TEXT, sale_price=SALE["sale_price"])
    assert _job(rig).state == S.NEEDS_INFO
    asked = rig.mail.sent[-1]
    assert asked.template == "needs_info" and "sale-end-label" in asked.variables["questions_list"]
    out = await _reply(rig, "עד 30.11", classified("facts", sale_end=q("30.11", "עד 30.11")))
    assert out["reply"] == "generating" and _job(rig).state == S.GENERATING


async def test_an_end_in_words_is_asked_again_never_worked_out() -> None:
    rig = await _open(SALE_TEXT, sale_price=SALE["sale_price"])
    out = await _reply(
        rig, "עד סוף החודש", classified("facts", sale_end=q("סוף החודש", "סוף החודש"))
    )
    assert out["reply"] == "needs_info" and _job(rig).state == S.NEEDS_INFO
    assert "sale-end-label" in rig.mail.sent[-1].variables["questions_list"]


async def test_an_end_in_the_past_is_asked_again() -> None:
    rig = await _open(SALE_TEXT, sale_price=SALE["sale_price"])
    await _reply(rig, "עד 1.11", classified("facts", sale_end=q("1.11", "עד 1.11")))
    assert _job(rig).state == S.NEEDS_INFO


async def test_sale_not_below_the_price_stops_before_generating() -> None:
    text = "כיסא משרדי, דגם C1, 450, 3 יחידות, מבצע 450 עד 30.11"
    rig = await _open(text, sale_price=q("450", "מבצע 450"), sale_end=SALE["sale_end"])
    job = _job(rig)
    assert job.state == S.NEEDS_INFO and rig.queue.tasks == []
    mail = rig.mail.sent[-1]
    assert mail.template == "sale_price_not_below"
    assert (mail.variables["price"], mail.variables["sale_price"]) == ("450", "450")
    assert job.thread_id == "t0"  # the owner's own thread (DEV 22.11.2026)


async def test_no_sale_command_while_waiting_for_info_drops_the_sale_and_goes_on() -> None:
    text = "כיסא משרדי, דגם C1, 450, 3 יחידות, מבצע 500"
    rig = await _open(text, sale_price=q("500", "מבצע 500"))
    out = await _reply(rig, "בלי מבצע")
    assert out["reply"] == "generating"
    assert all(f.key not in ("sale_price", "sale_end") for f in _job(rig).facts)
    assert "classify_reply" not in rig.llm.prompts  # an exact command, no model


async def test_a_corrected_sale_price_after_not_below_goes_on() -> None:
    text = "כיסא משרדי, דגם C1, 450, 3 יחידות, מבצע 450 עד 30.11"
    rig = await _open(text, sale_price=q("450", "מבצע 450"), sale_end=SALE["sale_end"])
    out = await _reply(rig, "מבצע 399", classified("facts", sale_price=q("399", "מבצע 399")))
    assert out["reply"] == "generating"


async def test_a_sale_in_a_reply_without_an_end_makes_no_version_and_blocks_approval() -> None:
    rig = await _ready()
    out = await _reply(rig, "מבצע 399", classified("facts", sale_price=q("399", "מבצע 399")))
    assert out["reply"] == "sale_pending"
    assert len(_job(rig).versions) == 1 and _job(rig).state == S.AWAITING_APPROVAL
    assert rig.mail.sent[-1].template == "needs_info"
    # "מאשר" now asks again instead of publishing a sale the preview never showed
    assert (await _reply(rig, "מאשר"))["reply"] == "sale_pending"
    assert _job(rig).state == S.AWAITING_APPROVAL
    assert "sale-end-label" in rig.mail.sent[-1].variables["questions_list"]
    # the end arrives: version 2 shows the sale, and it is sent
    out = await _reply(rig, "עד 30.11", classified("facts", sale_end=q("30.11", "עד 30.11")))
    assert out["reply"] == "new_version" and len(_job(rig).versions) == 2
    assert _preview(rig).variables["sale"].startswith("<_sale_value")
    assert (await _approve_and_publish(rig))["product_special"][0]["price"] == 399.0


async def test_a_sale_not_below_in_a_reply_blocks_approval_with_the_sale_mail() -> None:
    rig = await _ready()
    out = await _reply(
        rig,
        "מבצע 460 עד 30.11",
        classified("facts", sale_price=q("460", "מבצע 460"), sale_end=q("30.11", "עד 30.11")),
    )
    assert out["reply"] == "sale_pending" and rig.mail.sent[-1].template == "sale_price_not_below"
    assert (await _reply(rig, "מאשר"))["reply"] == "sale_pending"
    assert rig.mail.sent[-1].template == "sale_price_not_below"
    out = await _reply(rig, "בלי מבצע")
    assert out["reply"] == "no_sale" and len(_job(rig).versions) == 2
    assert "product_special" not in await _approve_and_publish(rig)


async def test_no_sale_removes_a_shown_sale_in_a_new_version() -> None:
    rig = await _ready(SALE_TEXT, **SALE)
    assert (await _reply(rig, "בלי מבצע"))["reply"] == "no_sale"
    assert _preview(rig).variables["sale"] == "<_sale_none>"
    assert "product_special" not in await _approve_and_publish(rig)


async def test_no_sale_without_a_sale_is_a_status_reply() -> None:
    rig = await _ready()
    assert (await _reply(rig, "בלי מבצע"))["reply"] == "status"
    assert len(_job(rig).versions) == 1


async def test_a_sale_that_ended_while_waiting_fails_the_publish_and_sends_nothing() -> None:
    rig = await _ready(SALE_TEXT, **SALE)
    assert (await _reply(rig, "מאשר"))["reply"] == "approved"
    rig.clock.advance(days=20)  # 5.12: the sale end (30.11) has passed
    with pytest.raises(RuntimeError, match="sale not valid at publish"):
        await run_task(rig.rt, {"event": "publish", "job_id": "T-1"})
    assert rig.posted is None and _job(rig).state == S.FAILED


async def test_a_sale_that_ended_before_approval_asks_for_a_new_end() -> None:
    rig = await _ready(SALE_TEXT, **SALE)
    rig.clock.advance(days=20)
    assert (await _reply(rig, "מאשר"))["reply"] == "sale_pending"
    assert "sale-end-label" in rig.mail.sent[-1].variables["questions_list"]
    assert _job(rig).state == S.AWAITING_APPROVAL


# ------------------------------------------------------------------ lifestyle image


async def test_lifestyle_requested_in_the_first_email_is_made_attached_and_sent() -> None:
    rig = await _ready(LIFE_TEXT, lifestyle_requested=True)
    roles = [(r.role, r.prompt_name) for r in rig.images.requests]
    assert roles == [("main", "image_main"), ("lifestyle", "image_lifestyle")]
    rules = rig.images.requests[1].variables["scene_rules"]
    assert "No people" in rules and "no text" in rules
    preview = _preview(rig)
    assert _names(preview) == ["image-2.jpg", "image-3.jpg"]
    assert preview.variables["lifestyle_note"] == "<_lifestyle_attached>"
    assert _job(rig).versions[-1].lifestyle
    posted = await _approve_and_publish(rig)
    [extra] = posted["images"]
    assert extra["sort_order"] == 1 and "lifestyle" in extra["image"]


async def test_no_request_makes_no_lifestyle_image() -> None:
    rig = await _ready()
    assert [r.role for r in rig.images.requests] == ["main"]
    assert _preview(rig).variables["lifestyle_note"] == "<_lifestyle_none>"
    assert "images" not in await _approve_and_publish(rig)


async def test_a_failed_lifestyle_image_still_sends_the_preview_then_says_so() -> None:
    rig = Rig(extraction([item(lifestyle_requested=True)]))
    rig.messages["g1"] = email(LIFE_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})

    real = rig.images.generate

    def fail_lifestyle(req: Any) -> Any:
        if req.role == "lifestyle":
            rig.images.requests.append(req)
            raise ImageRequestRejected("blocked")
        return real(req)

    rig.images.generate = fail_lifestyle  # type: ignore[method-assign]
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    templates = rig.mail.templates()
    assert templates[-2:] == ["ready_for_approval", "lifestyle_unavailable"]
    assert _preview(rig).variables["lifestyle_note"] == "<_lifestyle_none>"
    assert "image-3.jpg" not in _names(_preview(rig))
    assert _job(rig).state == S.AWAITING_APPROVAL


async def test_lifestyle_command_adds_it_and_the_remove_command_takes_it_away() -> None:
    rig = await _ready()
    assert (await _reply(rig, "תמונת אווירה"))["reply"] == "lifestyle_added"
    v2 = _job(rig).versions[-1]
    assert v2.number == 2 and v2.lifestyle and "image-3.jpg" in _names(_preview(rig))
    assert (await _reply(rig, "בלי תמונת אווירה"))["reply"] == "lifestyle_removed"
    v3 = _job(rig).versions[-1]
    assert v3.number == 3 and not v3.lifestyle and "image-3.jpg" not in _names(_preview(rig))
    assert "images" not in await _approve_and_publish(rig)
    assert "classify_reply" not in rig.llm.prompts


async def test_a_failed_lifestyle_command_makes_no_version() -> None:
    rig = await _ready()
    rig.images.error = ImageRequestRejected("blocked")
    assert (await _reply(rig, "תמונת אווירה"))["reply"] == "lifestyle_failed"
    assert len(_job(rig).versions) == 1
    assert rig.mail.sent[-1].template == "lifestyle_unavailable"


async def test_the_lifestyle_image_is_kept_in_later_versions() -> None:
    rig = await _ready(LIFE_TEXT, lifestyle_requested=True)
    made = len(rig.images.requests)
    await _reply(rig, "תמונה 1")
    assert len(rig.images.requests) == made  # not made again: taken from the last preview
    assert _names(_preview(rig)) == ["image-1.jpg", "image-2.jpg", "image-3.jpg"]
    assert _job(rig).versions[-1].lifestyle
    assert len((await _approve_and_publish(rig))["images"]) == 1


async def test_remove_without_a_lifestyle_image_is_a_status_reply() -> None:
    rig = await _ready()
    assert (await _reply(rig, "בלי תמונת אווירה"))["reply"] == "status"


async def test_lifestyle_command_while_waiting_for_info_is_noted_for_the_preview() -> None:
    rig = await _open(PLAIN_TEXT, quantity=q())
    assert _job(rig).state == S.NEEDS_INFO
    assert (await _reply(rig, "תמונת אווירה"))["reply"] == "lifestyle_noted"
    assert _job(rig).lifestyle_requested
    await _reply(rig, "3 יחידות", classified("facts", quantity=q(3, "3 יחידות")))
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    assert "image-3.jpg" in _names(_preview(rig))


async def test_a_free_reply_with_a_change_and_a_lifestyle_request_does_both() -> None:
    rig = await _ready()
    out = await _reply(
        rig,
        "המחיר 480 ותוסיף תמונת אווירה",
        classified("facts", lifestyle=True, price=q("480", "480")),
    )
    assert out["reply"] == "new_version"
    v2 = _job(rig).versions[-1]
    assert v2.lifestyle and _preview(rig).variables["price"] == "480"


async def test_a_free_lifestyle_request_alone_is_not_a_change_for_the_developer() -> None:
    rig = await _ready()
    out = await _reply(rig, "אפשר גם תמונת אווירה?", classified("wording_or_image", lifestyle=True))
    assert out["reply"] == "lifestyle_added"
    assert "change_not_supported" not in rig.mail.templates()


def test_scene_rules_need_the_playbook_value() -> None:
    from autolist.core.errors import ConfigMissing
    from autolist.orchestrator.generate_step import scene_rules

    with pytest.raises(ConfigMissing):
        scene_rules({"images": {}})
    assert "No people" not in scene_rules({"images": {"lifestyle_people": True}})
