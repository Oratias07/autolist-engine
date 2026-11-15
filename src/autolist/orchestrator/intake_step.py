"""Step 1: an accepted owner email that is not a reply in a job thread (fast-track doc).

Wiring only: every decision below is a case of docs/fast-track-pipeline.md, and every word the
owner reads comes from a reply template.
"""

from __future__ import annotations

from autolist.channels.email.reader import fetch_message
from autolist.core.job_store import new_job
from autolist.core.logging import get_logger
from autolist.core.models import ChannelMessage, Job, JobState, ParsedRequest, ProductRequest
from autolist.core.playbook import require
from autolist.core.state_machine import transition
from autolist.intake.request_parser import parse_request
from autolist.orchestrator.duplicate_check import cancel_if_duplicate
from autolist.orchestrator.runtime import (
    JOB_TASK_PATH,
    Runtime,
    jobs_lines,
    numbered,
    original_subject,
    product_name,
    send,
    to_owner,
)

log = get_logger(__name__)


def read(rt: Runtime, gmail_id: str) -> ChannelMessage:
    return fetch_message(
        rt.gmail(),
        gmail_id,
        config=rt.config.channels.email,
        max_bytes=rt.config.intake.max_attachment_bytes,
    )


def _reply(rt: Runtime, msg: ChannelMessage, template: str, variables: dict[str, str]) -> None:
    send(
        rt,
        to_owner(
            template,
            {"original_subject": original_subject(msg.subject), **variables},
            to=rt.owner,
            thread_id=msg.thread_id,
            in_reply_to=msg.rfc_message_id,
        ),
    )


def _questions(rt: Runtime, item: ProductRequest, extra: list[str]) -> list[str]:
    missing = [rt.labels[k] for k in item.missing_required]
    unverified = [rt.labels[k] for k in item.unverified_fields if k not in item.missing_required]
    return [*missing, *unverified, *extra]


async def handle_new_email(rt: Runtime, msg: ChannelMessage) -> list[str]:
    """Returns the job ids opened (none for the no-job cases)."""
    receipt_jobs = rt.jobs.by_source_thread(msg.thread_id)
    if receipt_jobs:
        _reply(rt, msg, "reply_in_product_thread", {"jobs_lines": jobs_lines(receipt_jobs)})
        return []

    required: list[str] = require(rt.playbook, "listing.required")
    parsed: ParsedRequest = parse_request(
        msg, llm=rt.llm, intake=rt.config.intake, required=required, job_id=None
    )
    limit: int = rt.config.require("agent.max_products_per_email")
    items = parsed.items
    if not items:
        _reply(rt, msg, "no_product_found", {})
        return []
    if len(items) > limit:
        _reply(
            rt,
            msg,
            "too_many_products",
            {"products_found": str(len(items)), "max_products": str(limit)},
        )
        return []
    unclear = not parsed.clearly_separated or bool(parsed.unassigned_photos)
    if unclear or (len(items) > 1 and parsed.questions):
        if not parsed.questions:
            # the model flagged the email as unclear but asked nothing: never guess a question
            raise RuntimeError("email is not clearly separated, and the extraction asked nothing")
        _reply(rt, msg, "clarify_products", {"questions_list": numbered(parsed.questions)})
        return []

    now = rt.now()
    prefix: str = rt.config.require("agent.job_id_prefix")
    first: int = rt.config.require("agent.first_job_number")
    jobs: list[Job] = []
    for item in items:
        job = new_job(
            rt.jobs,
            prefix=prefix,
            first_number=first,
            source_gmail_id=msg.message_id,
            source_thread_id=msg.thread_id,
            product_index=item.index,
            now=now,
        )
        job = job.model_copy(
            update={
                "facts": item.facts,
                "photo_indexes": item.photo_indexes,
                "printed_text": item.printed_text,
                "source_subject": msg.subject,
                "source_rfc_message_id": msg.rfc_message_id,
            }
        )
        rt.jobs.save(job)
        jobs.append(job)

    timeout_s: int = rt.config.require("agent.job_step_timeout_s")
    _reply(
        rt,
        msg,
        "received",
        {
            "jobs_lines": jobs_lines(jobs),
            "wait_minutes": str(max(1, timeout_s // 60)),
            "developer_contact": rt.developer,
            "rejected_attachments": ", ".join(parsed.rejected_attachments) or rt.none_label(),
        },
    )

    for job, item in zip(jobs, items, strict=True):
        questions = _questions(rt, item, list(parsed.questions))
        if questions:
            job = transition(job, JobState.NEEDS_INFO, rt.now())
            rt.jobs.save(job)
            send(
                rt,
                to_owner(
                    "needs_info",
                    {
                        "job_id": job.job_id,
                        "product_name": product_name(job),
                        "questions_list": numbered(questions),
                    },
                    to=rt.owner,
                ),
                job,
            )
        elif await cancel_if_duplicate(rt, job) is None:
            job = transition(job, JobState.GENERATING, rt.now())
            rt.jobs.save(job)
            rt.queue.enqueue(JOB_TASK_PATH, {"event": "generate", "job_id": job.job_id})
    log.info("jobs opened", extra={"jobs": [j.job_id for j in jobs]})
    return [j.job_id for j in jobs]
