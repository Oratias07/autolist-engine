"""One Cloud Tasks task = one job step (fast-track doc). Dispatch and failure handling only.

Events:
- ``email_received`` {gmail_id}: a reply in a job thread goes to ``reply_step``; anything else
  is a new owner email (``intake_step``).
- ``generate`` {job_id}: ``generate_step`` for a job in GENERATING.
- ``publish`` {job_id}: ``publish_step`` for a job in PUBLISHING (queued only by an approval).

A step that raises after the job exists fails the job: the owner gets ``failed`` (or
``failed_budget`` at the monthly cap) and the developer gets ``developer_alert``. Nothing is
retried (Cloud Tasks max attempts 1). An error before any job exists alerts the developer only.
"""

from __future__ import annotations

from typing import Any

from autolist.channels.email.thread_reader import agent_message_count
from autolist.core.errors import CostCapExceeded
from autolist.core.logging import get_logger
from autolist.core.models import JobState, OutboundMessage
from autolist.core.state_machine import FINAL
from autolist.orchestrator.cost_alerts import deliver_cost_alerts
from autolist.orchestrator.generate_step import generate
from autolist.orchestrator.intake_step import handle_new_email, read
from autolist.orchestrator.publish_step import publish
from autolist.orchestrator.reply_step import handle_reply
from autolist.orchestrator.runtime import Runtime, alert_developer
from autolist.orchestrator.watchdog import fail_job

log = get_logger(__name__)


def _monthly_cap(exc: Exception) -> bool:
    return isinstance(exc, CostCapExceeded) and exc.monthly


class _MailSender:
    def __init__(self, rt: Runtime) -> None:
        self._rt = rt

    def send(self, msg: OutboundMessage) -> str:
        return self._rt.mail.deliver(msg).gmail_id


class _OpenJobs:
    def __init__(self, rt: Runtime) -> None:
        self._rt = rt

    def count(self) -> int:
        return len(self._rt.jobs.open_jobs())


def send_cost_alerts(rt: Runtime) -> list[str]:
    """After every step: at most one owner email for thresholds or the cap crossed by it."""
    try:
        return deliver_cost_alerts(rt.ledger, _MailSender(rt), rt.owner, _OpenJobs(rt), rt.now())
    except Exception:
        # the ledger put the events back; they go out after the next step
        log.exception("cost alert not sent")
        return []


async def run_task(rt: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _run(rt, payload)
    finally:
        send_cost_alerts(rt)


async def _run(rt: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if event == "email_received":
        msg = read(rt, str(payload["gmail_id"]))
        cap: int = rt.config.require("agent.max_agent_emails_per_thread")
        if agent_message_count(rt.gmail(), msg.thread_id) >= cap:
            # a mail loop (an auto-responder the headers did not reveal) or a runaway thread:
            # no more answers here, the developer looks (security review 22.11.2026, M1)
            alert_developer(rt, "-", "thread_mail_cap", "-", f"thread {msg.thread_id}: {cap}")
            return {"skipped": True, "reason": "thread_mail_cap"}
        job = rt.jobs.by_thread(msg.thread_id)
        if job is not None:
            try:
                return dict(await handle_reply(rt, job, msg))
            except Exception as exc:
                current = rt.jobs.get(job.job_id) or job
                if current.state not in FINAL:
                    fail_job(rt, current, repr(exc), budget=_monthly_cap(exc))
                raise
        try:
            opened = await handle_new_email(rt, msg)
        except Exception as exc:
            jobs = rt.jobs.by_source_thread(msg.thread_id)
            for j in jobs:
                if j.state in (JobState.RECEIVED, JobState.GENERATING):
                    fail_job(rt, j, repr(exc), budget=_monthly_cap(exc))
            if not jobs:
                alert_developer(rt, "-", "intake_failed", "-", repr(exc))
            raise
        return {"opened": opened}

    if event == "generate":
        job = rt.jobs.get(str(payload["job_id"]))
        if job is None or job.state != JobState.GENERATING:
            log.warning("stale generate task", extra={"job_id": payload.get("job_id")})
            return {"skipped": True}
        try:
            job = await generate(rt, job)
        except Exception as exc:
            current = rt.jobs.get(job.job_id) or job
            if current.state == JobState.GENERATING:
                fail_job(rt, current, repr(exc), budget=_monthly_cap(exc))
            raise
        return {"job_id": job.job_id, "state": job.state}

    if event == "publish":
        job = rt.jobs.get(str(payload["job_id"]))
        if job is None or job.state != JobState.PUBLISHING:
            log.warning("stale publish task", extra={"job_id": payload.get("job_id")})
            return {"skipped": True}
        try:
            job = await publish(rt, job)
        except Exception as exc:
            current = rt.jobs.get(job.job_id) or job
            if current.state == JobState.PUBLISHING:
                fail_job(rt, current, repr(exc))
            raise
        return {"job_id": job.job_id, "state": job.state}

    raise ValueError(f"unknown job task event: {event!r}")
