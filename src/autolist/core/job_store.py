"""Persistent jobs: one document per job, a thread index, and the job-number counter.

The service runs one instance and one task at a time (infra/SPEC.md). A retried task can
still overlap the attempt it replaces (a request that timed out keeps running), so the two
writes that must happen once are claims made with a create-only document: a job number, and
the publish of a job (security review 22.11.2026, L3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from google.api_core.exceptions import AlreadyExists, Conflict

from autolist.core.models import Job, JobState
from autolist.core.state_machine import FINAL

MAX_NUMBER_TRIES = 100


def _create(doc: Any, data: dict[str, Any]) -> bool:
    try:
        doc.create(data)
    except (AlreadyExists, Conflict):
        return False
    return True


class JobStore(Protocol):
    def next_number(self, first: int) -> int: ...
    def save(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def by_thread(self, thread_id: str) -> Job | None: ...
    def open_jobs(self) -> list[Job]: ...
    def by_source_thread(self, thread_id: str) -> list[Job]: ...
    def all_jobs(self) -> list[Job]: ...
    def claim_publish(self, job_id: str, at: datetime) -> bool:
        """True once per job. False: a publish of this job already started (never POST twice)."""
        ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.threads: dict[str, str] = {}
        self._last: int | None = None
        self.publish_claims: dict[str, datetime] = {}

    def next_number(self, first: int) -> int:
        self._last = first if self._last is None else self._last + 1
        return self._last

    def save(self, job: Job) -> None:
        self.jobs[job.job_id] = job
        if job.thread_id:
            self.threads[job.thread_id] = job.job_id

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def by_thread(self, thread_id: str) -> Job | None:
        job_id = self.threads.get(thread_id)
        return self.jobs.get(job_id) if job_id else None

    def open_jobs(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state not in FINAL]

    def by_source_thread(self, thread_id: str) -> list[Job]:
        jobs = [j for j in self.jobs.values() if j.source_thread_id == thread_id]
        return sorted(jobs, key=lambda j: j.number)

    def all_jobs(self) -> list[Job]:
        return list(self.jobs.values())

    def claim_publish(self, job_id: str, at: datetime) -> bool:
        if job_id in self.publish_claims:
            return False
        self.publish_claims[job_id] = at
        return True


class FirestoreJobStore:
    def __init__(self, client: Any) -> None:
        self._jobs = client.collection("jobs")
        self._threads = client.collection("job_threads")
        self._counter = client.collection("counters").document("jobs")
        self._numbers = client.collection("job_numbers")
        self._publish_claims = client.collection("publish_claims")

    def next_number(self, first: int) -> int:
        """The counter says where to start; the number is taken by creating its document, so
        two overlapping calls never get the same number."""
        snap = self._counter.get()
        last = (snap.to_dict() or {}).get("last") if snap.exists else None
        number = first if last is None else int(last) + 1
        for _ in range(MAX_NUMBER_TRIES):
            if _create(self._numbers.document(str(number)), {"taken": True}):
                self._counter.set({"last": number})
                return number
            number += 1
        raise RuntimeError(f"no free job number after {MAX_NUMBER_TRIES} tries")

    def claim_publish(self, job_id: str, at: datetime) -> bool:
        return _create(self._publish_claims.document(job_id), {"at": at.isoformat()})

    def save(self, job: Job) -> None:
        self._jobs.document(job.job_id).set(job.model_dump(mode="json"))
        if job.thread_id:
            self._threads.document(job.thread_id).set({"job_id": job.job_id})

    def get(self, job_id: str) -> Job | None:
        snap = self._jobs.document(job_id).get()
        return Job.model_validate(snap.to_dict()) if snap.exists else None

    def by_thread(self, thread_id: str) -> Job | None:
        snap = self._threads.document(thread_id).get()
        if not snap.exists:
            return None
        return self.get(str((snap.to_dict() or {})["job_id"]))

    def open_jobs(self) -> list[Job]:
        return [j for j in self._all() if j.state not in FINAL]

    def by_source_thread(self, thread_id: str) -> list[Job]:
        jobs = [j for j in self._all() if j.source_thread_id == thread_id]
        return sorted(jobs, key=lambda j: j.number)

    def all_jobs(self) -> list[Job]:
        return self._all()

    def _all(self) -> list[Job]:
        # a full scan: the fast track has few jobs, and it needs no composite index
        return [Job.model_validate(s.to_dict()) for s in self._jobs.stream()]


def new_job(
    store: JobStore,
    *,
    prefix: str,
    first_number: int,
    source_gmail_id: str,
    source_thread_id: str,
    product_index: int,
    now: datetime,
) -> Job:
    number = store.next_number(first_number)
    job = Job(
        job_id=f"{prefix}{number}",
        number=number,
        state=JobState.RECEIVED,
        source_gmail_id=source_gmail_id,
        source_thread_id=source_thread_id,
        product_index=product_index,
        thread_id=None,
        created_at=now,
        updated_at=now,
        active_since=now,
    )
    store.save(job)
    return job
