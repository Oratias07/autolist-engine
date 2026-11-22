"""Cancel in free wording (DEV 22.11.2026): "בטל את שני המוצרים", "בטל את Q9002".

The list is every job of the same owner email, so a cancel in any of its threads can name
any of them. Only jobs waiting for the owner (NEEDS_INFO, AWAITING_APPROVAL) are cancelled.
The others are listed with their state. One answer goes to the thread the owner wrote in.
"""

from __future__ import annotations

from autolist.agent_runtime.cancel_resolver import CancelUnclear, has_cancel_word, resolve_cancel
from autolist.core.facts import store_model
from autolist.core.models import ChannelMessage, Job, JobState
from autolist.core.state_machine import transition
from autolist.orchestrator.runtime import (
    Runtime,
    original_subject,
    product_name,
    send,
    to_owner,
)

CANCELLABLE = (JobState.NEEDS_INFO, JobState.AWAITING_APPROVAL)


def _line(rt: Runtime, job: Job) -> str:
    return f"{job.job_id} \N{EN DASH} {product_name(job)} \N{EN DASH} {rt.states[job.state.name]}"


def cancel_by_text(
    rt: Runtime, jobs: list[Job], msg: ChannelMessage, *, here: Job | None = None
) -> dict[str, str] | None:
    """None when the reply is not a cancel (the caller goes on as before).

    ``here``: the job whose thread the reply is in; its answer keeps that thread."""
    if not jobs or not has_cancel_word(msg.text):
        return None
    listing = [
        f"{j.job_id} \N{EN DASH} {store_model(j) or '-'} \N{EN DASH} {product_name(j)}"
        + (" (this email)" if here is not None and j.job_id == here.job_id else "")
        for j in jobs
    ]
    first = here or jobs[0]
    try:
        chosen = resolve_cancel(msg, listing, llm=rt.llm, job_id=first.job_id)
    except CancelUnclear:
        _answer(rt, msg, here, "cancel_which", [_line(rt, j) for j in jobs])
        return {"job_id": first.job_id, "reply": "cancel_which"}
    if not chosen:
        return None
    done: list[Job] = []
    for i in chosen:
        job = rt.jobs.get(jobs[i].job_id) or jobs[i]
        if job.state in CANCELLABLE:
            job = transition(job, JobState.CANCELLED, rt.now(), error="cancelled by the owner")
            rt.jobs.save(job)
        done.append(job)
    _answer(rt, msg, here, "cancelled_list", [_line(rt, j) for j in done])
    return {"job_id": ",".join(j.job_id for j in done), "reply": "cancelled"}


def _answer(
    rt: Runtime, msg: ChannelMessage, here: Job | None, template: str, lines: list[str]
) -> None:
    out = to_owner(
        template,
        {"original_subject": original_subject(msg.subject), "jobs_lines": "\n".join(lines)},
        to=rt.owner,
        thread_id=msg.thread_id,
        in_reply_to=msg.rfc_message_id,
    )
    if here is not None:
        fresh = rt.jobs.get(here.job_id) or here
        send(rt, out, fresh)  # the job's own thread and subject
    else:
        send(rt, out)
