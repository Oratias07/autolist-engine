"""Step 3: the owner's reply in a job's own thread (decision DEV 15.11.2026).

Exact commands first, without a model: "מאשר", "בטל", "תמונה 1", "תמונה 2" (the words are in
the store repo). Approval binds to the latest preview: the reply's In-Reply-To must be that
email's Message-ID. "מאשר" with other text never approves. Any other reply goes to
``classify_reply`` (no approve or cancel intent exists there): facts, a wording or image
request (the developer), a status question, something else (out of scope), or unclear (one
question, nothing changes).
"""

from __future__ import annotations

from typing import Literal

from autolist.agent_runtime.classifier import classify_reply
from autolist.agent_runtime.commands import mentions, parse_command
from autolist.core.models import ChannelMessage, Job, JobState, ReplyIntent
from autolist.core.state_machine import FINAL, transition
from autolist.orchestrator.new_version import limit_reached, new_version
from autolist.orchestrator.reply_facts import apply_facts
from autolist.orchestrator.runtime import (
    JOB_TASK_PATH,
    Runtime,
    alert_developer,
    original_subject,
    product_name,
    send,
    to_owner,
)


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
    latest = job.versions[-1]
    if msg.in_reply_to is None or msg.in_reply_to != latest.preview_rfc_message_id:
        old = next((v for v in job.versions if v.preview_rfc_message_id == msg.in_reply_to), None)
        if old is not None and msg.in_reply_to is not None:
            _job_mail(
                rt,
                job,
                msg,
                "approve_old_version",
                old_version=str(old.number),
                version=str(latest.number),
            )
            return {"job_id": job.job_id, "reply": "old_version"}
        _job_mail(rt, job, msg, "approve_reply_to_preview", version=str(latest.number))
        return {"job_id": job.job_id, "reply": "not_a_preview_reply"}
    job = transition(job, JobState.PUBLISHING, rt.now())
    rt.jobs.save(job)
    rt.queue.enqueue(JOB_TASK_PATH, {"event": "publish", "job_id": job.job_id})
    return {"job_id": job.job_id, "reply": "approved"}


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
    if waiting and mentions(msg.text, rt.commands["approve"]):
        _job_mail(rt, job, msg, "approve_with_text", version=str(job.versions[-1].number))
        return {"job_id": job.job_id, "reply": "approve_with_text"}
    c = classify_reply(msg, llm=rt.llm, job_context=_context(rt, job), job_id=job.job_id)
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
    _job_mail(rt, job, msg, "reply_unclear", question=c.question)
    return {"job_id": job.job_id, "reply": "unclear"}
