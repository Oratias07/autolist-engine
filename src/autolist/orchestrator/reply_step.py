"""Step 3: the owner's reply in a job's own thread (decision DEV 15.11.2026).

Exact commands first, without a model: "מאשר", "בטל", "תמונה 1", "תמונה 2" (the words are in
the store repo). Approval binds to the latest preview: the reply's In-Reply-To must be that
email's Message-ID. "מאשר" with other text never approves. Any other reply goes to the
classifier, which arrives in the next PR; until then the developer is alerted.
"""

from __future__ import annotations

from typing import Literal

from autolist.agent_runtime.commands import mentions, parse_command
from autolist.channels.email.mail_sender import MAX_OUT_ATTACHMENT_BYTES
from autolist.channels.email.own_attachment import fetch_own_attachment
from autolist.core.models import ChannelMessage, Job, JobState, JobVersion, ListingText
from autolist.core.state_machine import FINAL, transition
from autolist.images.postprocess import postprocess
from autolist.orchestrator.generate_step import job_photos
from autolist.orchestrator.runtime import (
    JOB_TASK_PATH,
    Runtime,
    alert_developer,
    product_name,
    send,
    to_owner,
)
from autolist.review.preview import IMAGE_NAMES, build_preview


class ReplyHandlingNotReady(NotImplementedError):
    """A reply that is not an exact command: the classifier arrives in the next PR."""


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
            {"original_subject": msg.subject or "", "status_lines": status_line(rt, job)},
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
    limit: int = rt.config.require("agent.max_revision_rounds")
    if len(job.versions) > limit:
        alert_developer(rt, job.job_id, "revision_limit", job.state, f"{len(job.versions)}")
        _job_mail(rt, job, msg, "change_not_supported")
        return {"job_id": job.job_id, "reply": "revision_limit"}

    px: int = rt.config.require("images.output_px")
    image_1 = postprocess(job_photos(rt, job)[0].data, px)
    image_2 = None
    if 2 in latest.image_options:
        if latest.preview_gmail_id is None:
            raise RuntimeError(
                f"{job.job_id}: the preview id of version {latest.number} is missing"
            )
        image_2 = fetch_own_attachment(
            rt.gmail(),
            latest.preview_gmail_id,
            IMAGE_NAMES[2],
            config=rt.config.channels.email,
            max_bytes=MAX_OUT_ATTACHMENT_BYTES,
        )
    text = ListingText(
        title=latest.title,
        description_html=latest.description_html,
        description_text=latest.description_text,
        open_points=latest.open_points,
        problems=[],
    )
    number = latest.number + 1
    preview = build_preview(
        job,
        version=number,
        text=text,
        image_1=image_1,
        image_2=image_2,
        owner_choice=choice,
        labels=rt.labels,
        to=rt.owner,
        in_reply_to=msg.rfc_message_id,
    )
    message = preview.message.model_copy(
        update={
            "variables": {
                **preview.message.variables,
                "image_note": rt.fragment(preview.image_note_template, {}),
            }
        }
    )
    sent, saved = send(rt, message, job)
    job = saved or job
    version = latest.model_copy(
        update={
            "number": number,
            "created_at": rt.now(),
            "image_choice": preview.image_choice,
            "image_options": preview.image_options,
            "preview_gmail_id": sent.gmail_id,
            "preview_rfc_message_id": sent.rfc_message_id,
        }
    )
    job = job.model_copy(
        update={
            "versions": [*job.versions, JobVersion.model_validate(version.model_dump())],
            "revision_count": job.revision_count + 1,
        }
    )
    job = transition(job, JobState.AWAITING_APPROVAL, rt.now())
    rt.jobs.save(job)
    return {"job_id": job.job_id, "reply": f"image_{choice}"}


def handle_reply(rt: Runtime, job: Job, msg: ChannelMessage) -> dict[str, str]:
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
    alert_developer(rt, job.job_id, "reply_not_handled", job.state, msg.message_id)
    raise ReplyHandlingNotReady(job.job_id)
