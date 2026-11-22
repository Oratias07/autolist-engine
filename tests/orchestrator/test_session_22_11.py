# ruff: noqa: RUF001
"""The owner session of 22.11.2026, replayed end to end on the fixed behavior (DEV 22.11.2026).

Two products in one email, photos by order, a sale on product 1, a lifestyle image on product
2, "מאשר" with a change, two renames, then approval of both. It must take exactly two threads,
use the owner's names exactly, and pay for each image once.
"""

from __future__ import annotations

from typing import Any

from autolist.core.models import JobState
from autolist.orchestrator.job_runner import run_task
from tests.orchestrator.rig import Rig, classified, email, extraction, item, q

S = JobState
TEXT = (
    '1. שם המוצר: כיסא מנהלים גב גבוה עם משענת ראש\nדגם: C1\nמחיר: 899 ש"ח\n'
    'מבצע: 749 ש"ח עד 30.11.2026\nמלאי: 12\n\n'
    '2. שם המוצר: כיסא משרדי גב בינוני\nדגם: C1\nמחיר: 1,190 ש"ח\nמלאי: 17\n'
    "בבקשה תעשה גם תמונת אווירה."
)
CHAIR_1 = item(
    name=q("כיסא מנהלים גב גבוה עם משענת ראש", "כיסא מנהלים גב גבוה עם משענת ראש"),
    price=q("899", "899"),
    sale_price=q("749", "749"),
    sale_end=q("30.11.2026", "עד 30.11.2026"),
    quantity=q(12, "12"),
    photo_indexes=[1],
)
CHAIR_2 = item(
    name=q("כיסא משרדי גב בינוני", "כיסא משרדי גב בינוני"),
    price=q("1190", "1,190"),
    quantity=q(17, "17"),
    lifestyle_requested=True,
    photo_indexes=[2],
)
NAME = "[בדיקה! - לא למכירה] כיסא משרדי גב בינוני"
NAME_2 = "בדיקה! - לא למכירה - כיסא משרדי גב בינוני"


def _job(rig: Rig, job_id: str) -> Any:
    return rig.jobs.get(job_id)


async def _reply(rig: Rig, job_id: str, text: str, out: dict[str, Any] | None = None) -> Any:
    if out is not None:
        rig.llm.classify = out
    job = _job(rig, job_id)
    gid = f"r{len(rig.messages)}"
    rig.messages[gid] = email(
        text, thread=job.thread_id, in_reply_to=job.last_rfc_message_id, photos=0
    )
    return await run_task(rig.rt, {"event": "email_received", "gmail_id": gid})


async def test_the_22_11_session_in_two_threads() -> None:
    rig = Rig(extraction([CHAIR_1, CHAIR_2]))
    rig.messages["g1"] = email(TEXT, photos=2, subject="2 מוצרים")
    assert await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"}) == {
        "opened": ["T-1", "T-2"]
    }  # photos by order: no question
    for job_id in ("T-1", "T-2"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    paid = len(rig.images.requests)
    assert [r.role for r in rig.images.requests] == ["main", "main", "lifestyle"]

    # "מאשר" with a change: nothing saved, and the answer stays in the product's thread
    out = await _reply(rig, "T-2", f'מאשר, רק תשני את השם ל: "{NAME}"')
    assert out["reply"] == "approve_with_text" and _job(rig, "T-2").state == S.AWAITING_APPROVAL
    # two renames: each one is used exactly as written, without rewriting
    for name in (NAME, NAME_2):
        out = await _reply(
            rig,
            "T-2",
            f'תשני את השם ל: "{name}"',
            classified("facts", name=q(f'"{name}"', f'"{name}"')),
        )
        assert out["reply"] == "new_version"
        assert _job(rig, "T-2").versions[-1].title == name
    assert [v.number for v in _job(rig, "T-2").versions] == [1, 2, 3]
    assert rig.llm.prompts.count("write_title") == 2  # once per product, never again

    # approve both: product 2 with its name and lifestyle image, product 1 with its sale
    assert (await _reply(rig, "T-2", "מאשר"))["reply"] == "approved"
    await run_task(rig.rt, {"event": "publish", "job_id": "T-2"})
    posted_2 = rig.posted
    assert posted_2 is not None
    [desc] = posted_2["product_description"].values()
    assert desc["name"] == NAME_2 and len(posted_2["images"]) == 1
    assert (await _reply(rig, "T-1", "מאשר"))["reply"] == "approved"
    await run_task(rig.rt, {"event": "publish", "job_id": "T-1"})
    assert rig.posted is not None
    assert rig.posted["product_special"] == [
        {"price": 749.0, "date_start": "2026-11-15", "date_end": "2026-11-30"}
    ]
    assert {_job(rig, j).state for j in ("T-1", "T-2")} == {S.SAVED_HIDDEN}

    # exactly two threads, one subject each, and no image paid twice
    t2 = _job(rig, "T-2").thread_id
    seen: dict[str, set[str]] = {}
    for m in rig.mail.sent:
        thread = m.thread_id or t2  # the first email of product 2 opens its thread
        # an email without a job ("received") keeps its template subject, "Re: <subject>"
        seen.setdefault(thread, set()).add(m.subject or f"Re: {m.variables['original_subject']}")
    assert set(seen) == {"t0", t2}
    assert seen["t0"] == {"Re: 2 מוצרים"} and len(seen[t2]) == 1
    assert len(rig.images.requests) == paid


async def test_cancelled_products_sent_again_reuse_their_images() -> None:
    """DEV 22.11.2026: cancel both, send the same email again, pay no image twice."""
    rig = Rig(extraction([CHAIR_1, CHAIR_2]))
    rig.messages["g1"] = email(TEXT, photos=2, subject="2 מוצרים")
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    for job_id in ("T-1", "T-2"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    paid = len(rig.images.requests)
    # images made before reuse existed: only the previews have them
    for name in [n for n in rig.blobs.objects if n.startswith("image-reuse/")]:
        del rig.blobs.objects[name]
    for job_id in ("T-1", "T-2"):
        assert (await _reply(rig, job_id, "בטל"))["reply"] == "cancelled"
    assert len([n for n in rig.blobs.objects if n.startswith("image-reuse/")]) == 3

    rig.messages["g9"] = email(TEXT, photos=2, subject="2 מוצרים שוב", thread="t9")
    out = await run_task(rig.rt, {"event": "email_received", "gmail_id": "g9"})
    assert out == {"opened": ["T-3", "T-4"]}
    for job_id in ("T-3", "T-4"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    assert len(rig.images.requests) == paid  # all three images reused
    assert "image-3.jpg" in [a.filename for a in rig.mail.sent[-1].attachments]


async def test_different_photos_are_never_given_a_reused_image() -> None:
    rig = Rig(extraction([CHAIR_1]))
    rig.messages["g1"] = email(TEXT, photos=1)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-1"})
    rig.messages["g2"] = email(TEXT, photos=2, thread="t2")  # another photo set
    rig.llm.extract = extraction([{**CHAIR_1, "photo_indexes": [1, 2]}])
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g2"})
    await run_task(rig.rt, {"event": "generate", "job_id": "T-2"})
    assert len(rig.images.requests) == 2
