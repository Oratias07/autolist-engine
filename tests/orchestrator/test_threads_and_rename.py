"""One thread per product, and the owner's exact rename (DEV 22.11.2026)."""

from __future__ import annotations

from typing import Any

from autolist.core.facts import owner_title
from autolist.core.models import JobState
from autolist.orchestrator.job_runner import run_task
from autolist.orchestrator.runtime import one_line, reply_subject
from tests.orchestrator.rig import Rig, classified, email, extraction, item, q

S = JobState
TWO = "1. כיסא משרדי, דגם C1, 450, 3 יחידות\n2. שולחן, דגם C1, 900, 2 יחידות"
SECOND = item(
    name=q("שולחן", "שולחן"),
    price=q("900", "900"),
    quantity=q(2, "2 יחידות"),
    model=q("C1", "C1"),  # the fake writer's title cites C1
    photo_indexes=[2],
)


async def _two(rig: Rig | None = None) -> Rig:
    rig = rig or Rig(extraction([item(), SECOND]))
    rig.messages["g1"] = email(TWO, photos=2, subject="2 מוצרים")
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    for job_id in ("T-1", "T-2"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    return rig


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


def _job_mails(rig: Rig, job_id: str) -> list[Any]:
    return [m for m in rig.mail.sent if m.variables.get("job_id") == job_id]


async def test_two_products_make_exactly_two_threads_with_one_subject_each() -> None:
    rig = await _two()
    await _reply(rig, "T-1", "תמונה 1")  # version 2
    await _reply(rig, "T-1", "מאשר ושנה משהו")  # "not saved"
    await _reply(rig, "T-2", "תמונה 1")
    one, two = _job_mails(rig, "T-1"), _job_mails(rig, "T-2")
    assert len(one) >= 3 and len(two) >= 2
    # product 1: the owner's own thread, "Re:" his subject, every time
    assert {(m.thread_id, m.subject) for m in one} == {("t0", "Re: 2 מוצרים")}
    # product 2: one thread of its own, opened by its first email, one fixed subject
    first = two[0]
    assert first.thread_id is None
    subject = "<_thread_subject job_id=T-2 product_name=שולחן>"
    assert first.subject == subject
    thread = _job(rig, "T-2").thread_id
    assert {(m.thread_id, m.subject) for m in two[1:]} == {(thread, subject)}
    threads = {m.thread_id or thread for m in [*one, *two]}
    assert threads == {"t0", thread}


async def test_every_email_answers_the_newest_message_of_its_thread() -> None:
    rig = await _two()
    preview = _job_mails(rig, "T-1")[-1]
    first = next(
        h["value"] for h in rig.messages["g1"]["payload"]["headers"] if h["name"] == "Message-ID"
    )
    assert preview.in_reply_to == first  # the owner's own email
    out = await _reply(rig, "T-1", "תמונה 1")
    assert out["reply"] == "image_1"
    owner_reply = rig.messages[f"r{len(rig.messages) - 1}"]
    rfc = next(h["value"] for h in owner_reply["payload"]["headers"] if h["name"] == "Message-ID")
    assert _job_mails(rig, "T-1")[-1].in_reply_to == rfc
    # product 2's first email opens its thread; the next one answers the owner's reply there
    assert _job_mails(rig, "T-2")[0].in_reply_to is None
    await _reply(rig, "T-2", "תמונה 1")
    reply = rig.messages[f"r{len(rig.messages) - 1}"]
    rfc2 = next(h["value"] for h in reply["payload"]["headers"] if h["name"] == "Message-ID")
    assert _job_mails(rig, "T-2")[-1].in_reply_to == rfc2


async def test_after_a_clarifying_question_the_answer_keeps_two_threads() -> None:
    rig = Rig(
        extraction(
            [item(photo_indexes=[]), {**SECOND, "photo_indexes": []}],
            unassigned_photos=[1, 2],
            questions=["איזו תמונה לכל מוצר?"],
        )
    )
    rig.messages["g1"] = email(TWO, photos=2, subject="2 מוצרים")
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})
    assert rig.mail.sent[-1].template == "clarify_products"
    assert rig.mail.sent[-1].thread_id == "t0"
    # the owner answers in the same thread: the products open, product 1 stays in it
    rig.llm.extract = extraction([item(), SECOND])
    rig.messages["g2"] = email("כן, התמונה הראשונה למוצר 1", thread="t0", photos=0)
    out = await run_task(rig.rt, {"event": "email_received", "gmail_id": "g2"})
    assert out == {"opened": ["T-1", "T-2"]}
    for job_id in ("T-1", "T-2"):
        await run_task(rig.rt, {"event": "generate", "job_id": job_id})
    threads = {m.thread_id or _job(rig, "T-2").thread_id for m in rig.mail.sent}
    assert threads == {"t0", _job(rig, "T-2").thread_id}


async def test_a_name_in_a_reply_is_used_exactly_and_nothing_is_rewritten() -> None:
    rig = await _two()
    written = rig.llm.prompts.count("write_title")
    name = "[בדיקה! - לא למכירה] כיסא משרדי גב בינוני"
    out = await _reply(
        rig,
        "T-1",
        f'תשני את השם ל: "{name}"',
        classified("facts", name=q(f'"{name}"', f'"{name}"')),
    )
    assert out["reply"] == "new_version"
    v2 = _job(rig, "T-1").versions[-1]
    assert v2.number == 2 and v2.title == name  # "!" and the test marking stay: his choice
    assert rig.llm.prompts.count("write_title") == written  # no rewrite
    assert _job_mails(rig, "T-1")[-1].variables["title"] == name


async def test_the_owner_name_survives_a_later_rewrite_and_is_published() -> None:
    rig = await _two()
    name = "כיסא גב בינוני - בדיקה!"
    await _reply(rig, "T-1", f"שם: {name}", classified("facts", name=q(name, name)))
    out = await _reply(
        rig,
        "T-1",
        "יש לו גלגלים שקטים",
        classified("facts", details=[q("גלגלים שקטים", "גלגלים שקטים")]),
    )
    assert out["reply"] == "new_version"
    assert _job(rig, "T-1").versions[-1].title == name
    await _reply(rig, "T-1", "מאשר")
    await run_task(rig.rt, {"event": "publish", "job_id": "T-1"})
    assert rig.posted is not None
    [desc] = rig.posted["product_description"].values()
    assert desc["name"] == name


async def test_the_name_in_the_first_email_is_still_written() -> None:
    rig = await _two()
    assert _job(rig, "T-1").versions[0].title == rig.llm.title.title


def test_owner_title_and_header_lines() -> None:
    assert owner_title(' " כיסא  משרדי\nגב " ') == "כיסא משרדי גב"
    assert owner_title("״כיסא״") == "כיסא"
    assert one_line("a\r\nb\tc") == "a b c"
    assert reply_subject("Re: x") == "Re: x" and reply_subject("x") == "Re: x"


async def test_the_live_22_11_rename_uses_the_owner_text_not_the_model_value() -> None:
    """Live 22.11.2026: the model returned the name without brackets and commas, and also
    read a model and a detail out of the name. The title is his own text, and nothing else
    from inside the name becomes a fact."""
    rig = await _two()
    written = rig.llm.prompts.count("write_title")
    name = "[לא למכירה] -בדיקה- כיסא גב בינוני, גב ומושב דמוי עור שחור, C1"
    out = await _reply(
        rig,
        "T-1",
        f'שני שם של המוצר ל"{name}"',
        classified(
            "facts",
            name=q("כיסא גב בינוני גב ומושב דמוי עור שחור C1", name),
            model=q("C1", "C1"),
            details=[q("גב ומושב דמוי עור שחור", "גב ומושב דמוי עור שחור")],
        ),
    )
    assert out["reply"] == "new_version"
    job = _job(rig, "T-1")
    assert job.versions[-1].title == name
    assert rig.llm.prompts.count("write_title") == written  # nothing new: no rewrite
    assert [f.key for f in job.facts].count("detail") == 0


async def test_a_name_without_quotation_marks_runs_to_the_end_of_its_line() -> None:
    rig = await _two()
    await _reply(
        rig,
        "T-1",
        "שם: כיסא מנהלים יוקרתי דגם C1\nתודה",  # noqa: RUF001
        classified(
            "facts", name=q("כיסא מנהלים יוקרתי", "כיסא מנהלים יוקרתי"), model=q("C1", "C1")
        ),
    )
    assert _job(rig, "T-1").versions[-1].title == "כיסא מנהלים יוקרתי דגם C1"
