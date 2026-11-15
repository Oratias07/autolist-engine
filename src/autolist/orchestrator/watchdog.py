"""Daily watchdog (infra SPEC, fast-track doc): no job is left hanging.

- An active job (the server was working on it) older than ``agent.job_step_timeout_s`` fails:
  the step died or overran. The owner and the developer are told.
- A waiting job with no change for ``agent.expire_after_days`` expires, and the owner is told.
"""

from __future__ import annotations

from datetime import timedelta

from autolist.core.logging import get_logger, redact_text
from autolist.core.models import Job, JobState
from autolist.core.state_machine import ACTIVE, WAITING, transition
from autolist.orchestrator.runtime import Runtime, alert_developer, product_name, send, to_owner

log = get_logger(__name__)


def fail_job(rt: Runtime, job: Job, reason: str, *, budget: bool = False) -> Job:
    """FAILED + the owner's failure email (budget variant at the cap) + a developer alert."""
    job = transition(job, JobState.FAILED, rt.now(), error=redact_text(reason)[:500])
    rt.jobs.save(job)
    variables = {"job_id": job.job_id, "product_name": product_name(job)}
    if budget:
        reset = rt.ledger.month_summary(rt.now()).reset_date
        msg = to_owner(
            "failed_budget",
            {**variables, "reset_date": reset.strftime("%d.%m.%Y")},
            to=rt.owner,
            thread_id=job.thread_id,
        )
    else:
        msg = to_owner(
            "failed",
            {**variables, "developer_contact": rt.developer},
            to=rt.owner,
            thread_id=job.thread_id,
        )
    _, saved = send(rt, msg, job)
    alert_developer(rt, job.job_id, "failed", JobState.FAILED, reason)
    return saved or job


def run_watchdog(rt: Runtime) -> dict[str, list[str]]:
    now = rt.now()
    timeout = timedelta(seconds=int(rt.config.require("agent.job_step_timeout_s")))
    days: int = rt.config.require("agent.expire_after_days")
    out: dict[str, list[str]] = {"failed": [], "expired": []}
    for job in rt.jobs.open_jobs():
        if job.state in ACTIVE and job.active_since and now - job.active_since > timeout:
            fail_job(rt, job, f"step did not finish within {timeout} (watchdog)")
            out["failed"].append(job.job_id)
        elif job.state in WAITING and now - job.updated_at > timedelta(days=days):
            job = transition(job, JobState.EXPIRED, now)
            rt.jobs.save(job)
            send(
                rt,
                to_owner(
                    "expired",
                    {"job_id": job.job_id, "product_name": product_name(job), "days": str(days)},
                    to=rt.owner,
                    thread_id=job.thread_id,
                ),
                job,
            )
            out["expired"].append(job.job_id)
    log.info("watchdog", extra=out)
    return out
