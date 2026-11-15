"""Job state transitions (fast-track design, docs/fast-track-pipeline.md).

Any transition not in the table raises ``IllegalTransition``. Final states have no exits.
"""

from __future__ import annotations

from datetime import datetime

from autolist.core.errors import IllegalTransition
from autolist.core.models import Job, JobState

S = JobState

TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    S.RECEIVED: frozenset({S.NEEDS_INFO, S.GENERATING, S.FAILED, S.CANCELLED}),
    S.NEEDS_INFO: frozenset({S.GENERATING, S.NEEDS_INFO, S.CANCELLED, S.EXPIRED, S.FAILED}),
    S.GENERATING: frozenset({S.AWAITING_APPROVAL, S.FAILED}),
    S.AWAITING_APPROVAL: frozenset(
        {S.PUBLISHING, S.GENERATING, S.AWAITING_APPROVAL, S.CANCELLED, S.EXPIRED, S.FAILED}
    ),
    S.PUBLISHING: frozenset({S.SAVED_HIDDEN, S.FAILED}),
    S.SAVED_HIDDEN: frozenset(),
    S.CANCELLED: frozenset(),
    S.FAILED: frozenset(),
    S.EXPIRED: frozenset(),
    # defined for later phases, unreachable in the fast track
    S.RESEARCHING: frozenset(),
    S.REVISING: frozenset(),
    S.APPROVED: frozenset(),
    S.PUBLISHED: frozenset(),
}

# States in which the server is doing work; the watchdog fails a job that stays in one too long.
ACTIVE: frozenset[JobState] = frozenset({S.RECEIVED, S.GENERATING, S.PUBLISHING})
WAITING: frozenset[JobState] = frozenset({S.NEEDS_INFO, S.AWAITING_APPROVAL})
FINAL: frozenset[JobState] = frozenset({S.SAVED_HIDDEN, S.CANCELLED, S.FAILED, S.EXPIRED})


def can(current: JobState, target: JobState) -> bool:
    return target in TRANSITIONS[current]


def transition(job: Job, target: JobState, now: datetime, error: str | None = None) -> Job:
    """Return a copy of ``job`` in ``target``. Raises ``IllegalTransition`` if not allowed."""
    if not can(job.state, target):
        raise IllegalTransition(f"{job.job_id}: {job.state} -> {target} is not allowed")
    if target == S.FAILED and not error:
        raise IllegalTransition(f"{job.job_id}: FAILED needs a reason")
    return job.model_copy(
        update={
            "state": target,
            "updated_at": now,
            "active_since": now if target in ACTIVE else None,
            "error": error if error is not None else job.error,  # FAILED or CANCELLED reason
        }
    )
