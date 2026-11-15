"""Step 3: the owner's reply in a job's own thread (decision DEV 15.11.2026).

Exact commands first, without a model: "מאשר", "בטל", "תמונה 1", "תמונה 2" (the words are in
the store repo). Approval binds to the latest preview: the reply's In-Reply-To must be that
email's Message-ID or any agent email sent after it in the thread (DEV 15.11.2026). "מאשר"
with other text never approves. Any other reply goes to
``classify_reply`` (no approve or cancel intent exists there): facts, a wording or image
request (the developer), a status question, something else (out of scope), or unclear (one
question, nothing changes).
"""

from __future__ import annotations

from typing import Literal

from autolist.agent_runtime.classifier import classify_reply
from autolist.agent_runtime.commands import mentions, parse_command
from autolist.channels.email.own_sent import sent_by_gmail_id, sent_by_rfc_id
from autolist.core.models import (
    ChannelMessage,
    Job,
    JobState,
    ReplyClassification,
    ReplyIntent,
)
from autolist.core.sale import without_sale
from autolist.core.state_machine import FINAL, transition
from autolist.orchestrator.generate_step import job_photos, lifestyle_failed, lifestyle_image
from autolist.orchestrator.new_version import limit_reached, new_version
from autolist.orchestrator.reply_facts import apply_facts, missing_required
from autolist.orchestrator.runtime import (
    JOB_TASK_PATH,
    Runtime,
    alert_developer,
    numbered,
    original_subject,
    product_name,
    send,
    to_owner,
)
from autolist.orchestrator.sale_step import sale_blocked, sale_not_below, sale_questions


def status_line(rt: Runtime, job: Job) -> str:
    line = f"{job.job_id} \N{EN DASH} {product_name(job)}: {rt.states[job.state]}"
    if job.istores_product_id is not None:
        line += f", {rt.states['_product_id']} {job.istores_product_id}"
    return line


def _job_mail(rt: Runtime, job: Job, msg: ChannelMessage, template: str, **extra: str) -> Job:
    _, saved = send(
        rt,
        to_owner(
            template,
            {"job_id": job.job_id, "product_name": product_name(job), **extra},
            to=rt.owner,
            thread_id=job.thread_id,
            in_reply_to=msg.rfc_message_id,
        ),
        job,
    )
    return saved or job


def _status(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    send(
        rt,
        to_owner(
            "status_reply",
            {
                "original_subject": original_subject(msg.subject),
                "status_lines": status_line(rt, job),
            },
            to=rt.owner,
            thread_id=job.thread_id,
            in_reply_to=msg.rfc_message_id,
        ),
        job,
    )
    return {"job_id": job.job_id, "reply": "status"}


def _approve(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    """Approve the latest version when the reply answers an agent email of this thread that was
    sent at or after the latest preview (DEV 15.11.2026). Earlier → which version it saw."""
    latest = job.versions[-1]
    email = rt.config.channels.email
    service = rt.gmail()
    answered = sent_by_rfc_id(service, msg.in_reply_to, config=email) if msg.in_reply_to else None
    if answered is None or answered.thread_id != msg.thread_id or latest.preview_gmail_id is None:
        _job_mail(rt, job, msg, "approve_reply_to_preview", version=str(latest.number))
        return {"job_id": job.job_id, "reply": "not_a_preview_reply"}
    seen = None
    for version in reversed(job.versions):
        if version.preview_gmail_id is None:
            continue
        preview = sent_by_gmail_id(service, version.preview_gmail_id, config=email)
        if preview is not None and preview.sent_ms <= answered.sent_ms:
            seen = version
            break
    if seen is None:
        _job_mail(rt, job, msg, "approve_reply_to_preview", version=str(latest.number))
        return {"job_id": job.job_id, "reply": "not_a_preview_reply"}
    if seen.number != latest.number:
        _job_mail(
            rt,
            job,
            msg,
            "approve_old_version",
            old_version=str(seen.number),
            version=str(latest.number),
        )
        return {"job_id": job.job_id, "reply": "old_version"}
    if sale_blocked(rt, job):  # a sale the latest version does not show is waiting for a fix
        if not sale_not_below(rt, job, msg.rfc_message_id):
            _job_mail(rt, job, msg, "needs_info", questions_list=numbered(sale_questions(rt, job)))
        return {"job_id": job.job_id, "reply": "sale_pending"}
    job = transition(job, JobState.PUBLISHING, rt.now())
    rt.jobs.save(job)
    rt.queue.enqueue(JOB_TASK_PATH, {"event": "publish", "job_id": job.job_id})
    return {"job_id": job.job_id, "reply": "approved"}


def _lifestyle_add(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    """ "תמונת אווירה": waiting for info, it is noted for the preview; waiting for approval, a
    lifestyle image is made now (a new one, also when one is attached) and sent in a version."""
    if job.state == JobState.NEEDS_INFO:
        rt.jobs.save(job.model_copy(update={"lifestyle_requested": True}))
        return {"job_id": job.job_id, "reply": "lifestyle_noted"}
    if limit_reached(rt, job, msg):
        return {"job_id": job.job_id, "reply": "revision_limit"}
    job = job.model_copy(update={"lifestyle_requested": True})
    rt.jobs.save(job)
    image = lifestyle_image(rt, job, job_photos(rt, job))
    if image is None:
        lifestyle_failed(rt, job, msg.rfc_message_id)
        return {"job_id": job.job_id, "reply": "lifestyle_failed"}
    new_version(rt, job, msg, owner_choice=job.versions[-1].owner_choice, lifestyle=image)
    return {"job_id": job.job_id, "reply": "lifestyle_added"}


def _lifestyle_remove(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    job = job.model_copy(update={"lifestyle_requested": False})
    rt.jobs.save(job)
    if job.state != JobState.AWAITING_APPROVAL or not job.versions[-1].lifestyle:
        return _status(rt, job, msg)
    if limit_reached(rt, job, msg):
        return {"job_id": job.job_id, "reply": "revision_limit"}
    new_version(rt, job, msg, owner_choice=job.versions[-1].owner_choice, drop_lifestyle=True)
    return {"job_id": job.job_id, "reply": "lifestyle_removed"}


async def _no_sale(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    """ "בלי מבצע": every sale fact is removed, then the job goes on as after any answer."""
    had_sale = job.fact("sale_price") is not None
    job = without_sale(job)
    rt.jobs.save(job)
    nothing = ReplyClassification(
        intent=ReplyIntent.FACTS,
        facts=[],
        unverified_fields=[],
        also_wording_or_image=False,
        question="",
    )
    if job.state == JobState.NEEDS_INFO:
        return await apply_facts(rt, job, msg, nothing)
    if job.state != JobState.AWAITING_APPROVAL or not had_sale:
        return _status(rt, job, msg)
    if limit_reached(rt, job, msg):
        return {"job_id": job.job_id, "reply": "revision_limit"}
    new_version(rt, job, msg, owner_choice=job.versions[-1].owner_choice)
    return {"job_id": job.job_id, "reply": "no_sale"}


def _choose_image(
    rt: Runtime, job: Job, msg: ChannelMessage, choice: Literal[1, 2]
) -> dict[str, str]:
    latest = job.versions[-1]
    if choice == 2 and 2 not in latest.image_options:
        _job_mail(rt, job, msg, "image_2_unavailable")
        return {"job_id": job.job_id, "reply": "no_image_2"}
    if limit_reached(rt, job, msg):
        return {"job_id": job.job_id, "reply": "revision_limit"}
    new_version(rt, job, msg, owner_choice=choice)
    return {"job_id": job.job_id, "reply": f"image_{choice}"}


def _context(rt: Runtime, job: Job) -> str:
    """Trusted context only: no owner text, so a product name cannot carry an instruction."""
    known = ", ".join(sorted({f.key for f in job.facts})) or "-"
    version = str(job.versions[-1].number) if job.versions else "-"
    return f"state: {job.state}\nlatest preview version: {version}\nknown details: {known}"


async def handle_reply(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
    if job.state in FINAL:
        return _status(rt, job, msg)
    command = parse_command(msg.text, rt.commands)
    waiting = job.state == JobState.AWAITING_APPROVAL
    if command == "approve":
        return _approve(rt, job, msg) if waiting else _status(rt, job, msg)
    if command == "cancel":
        if job.state not in (JobState.NEEDS_INFO, JobState.AWAITING_APPROVAL):
            return _status(rt, job, msg)
        job = transition(job, JobState.CANCELLED, rt.now(), error="cancelled by the owner")
        rt.jobs.save(job)
        _job_mail(rt, job, msg, "cancelled")
        return {"job_id": job.job_id, "reply": "cancelled"}
    if command in ("image_1", "image_2"):
        if not waiting:
            return _status(rt, job, msg)
        return _choose_image(rt, job, msg, 1 if command == "image_1" else 2)
    if command == "lifestyle_add":
        if job.state not in (JobState.NEEDS_INFO, JobState.AWAITING_APPROVAL):
            return _status(rt, job, msg)
        return _lifestyle_add(rt, job, msg)
    if command == "lifestyle_remove":
        return _lifestyle_remove(rt, job, msg)
    if command == "no_sale":
        if job.state not in (JobState.NEEDS_INFO, JobState.AWAITING_APPROVAL):
            return _status(rt, job, msg)
        return await _no_sale(rt, job, msg)
    if waiting and mentions(msg.text, rt.commands["approve"]):
        _job_mail(rt, job, msg, "approve_with_text", version=str(job.versions[-1].number))
        return {"job_id": job.job_id, "reply": "approve_with_text"}
    c = classify_reply(msg, llm=rt.llm, job_context=_context(rt, job), job_id=job.job_id)
    if c.lifestyle_requested and c.intent in (ReplyIntent.FACTS, ReplyIntent.WORDING_OR_IMAGE):
        if c.also_wording_or_image:
            alert_developer(rt, job.job_id, "change_request", job.state, msg.text[:1000])
        if job.state == JobState.NEEDS_INFO:
            job = job.model_copy(update={"lifestyle_requested": True})
            rt.jobs.save(job)
            return await apply_facts(rt, job, msg, c)
        if not waiting:
            return _status(rt, job, msg)
        if limit_reached(rt, job, msg):
            return {"job_id": job.job_id, "reply": "revision_limit"}
        job = job.model_copy(update={"lifestyle_requested": True})
        rt.jobs.save(job)
        image = lifestyle_image(rt, job, job_photos(rt, job))
        if image is None:
            lifestyle_failed(rt, job, msg.rfc_message_id)
            if not c.facts:
                return {"job_id": job.job_id, "reply": "lifestyle_failed"}
        if not c.facts:
            new_version(rt, job, msg, owner_choice=job.versions[-1].owner_choice, lifestyle=image)
            return {"job_id": job.job_id, "reply": "lifestyle_added"}
        return await apply_facts(rt, job, msg, c, lifestyle=image)
    if c.intent == ReplyIntent.STATUS:
        return _status(rt, job, msg)
    if c.intent == ReplyIntent.OTHER:
        send(
            rt,
            to_owner(
                "out_of_scope",
                {"original_subject": original_subject(msg.subject)},
                to=rt.owner,
                thread_id=job.thread_id,
                in_reply_to=msg.rfc_message_id,
            ),
            job,
        )
        return {"job_id": job.job_id, "reply": "out_of_scope"}
    if c.intent == ReplyIntent.WORDING_OR_IMAGE:
        alert_developer(rt, job.job_id, "change_request", job.state, msg.text[:1000])
        _job_mail(rt, job, msg, "change_not_supported")
        return {"job_id": job.job_id, "reply": "change_not_supported"}
    if c.intent == ReplyIntent.FACTS and job.state in (
        JobState.NEEDS_INFO,
        JobState.AWAITING_APPROVAL,
    ):
        if c.also_wording_or_image:
            alert_developer(rt, job.job_id, "change_request", job.state, msg.text[:1000])
        return await apply_facts(rt, job, msg, c)
    if c.intent == ReplyIntent.FACTS:
        return _status(rt, job, msg)  # working on it now (RECEIVED, GENERATING, PUBLISHING)
    if job.state == JobState.NEEDS_INFO and not missing_required(rt, job):
        # every required detail is there: the open question was the model's own (for
        # example the photo check), and this reply answers it. Go on; the preview shows the
        # photo, and the owner can still cancel (P1-08: no loop on a plain "כן").
        answered = c.model_copy(update={"facts": [], "unverified_fields": [], "question": ""})
        return await apply_facts(rt, job, msg, answered)
    _job_mail(rt, job, msg, "reply_unclear", question=c.question)
    return {"job_id": job.job_id, "reply": "unclear"}
