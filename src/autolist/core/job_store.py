"""Persistent jobs: one document per job, a thread index, and the job-number counter.

The service runs one instance and one task at a time (infra/SPEC.md), so the counter is
read and written without a transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from autolist.core.models import Job, JobState
from autolist.core.state_machine import FINAL


class JobStore(Protocol):
    def next_number(self, first: int) -> int: ...
    def save(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def by_thread(self, thread_id: str) -> Job | None: ...
    def open_jobs(self) -> list[Job]: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.threads: dict[str, str] = {}
        self._last: int | None = None

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


class FirestoreJobStore:
    def __init__(self, client: Any) -> None:
        self._jobs = client.collection("jobs")
        self._threads = client.collection("job_threads")
        self._counter = client.collection("counters").document("jobs")

    def next_number(self, first: int) -> int:
        snap = self._counter.get()
        last = (snap.to_dict() or {}).get("last") if snap.exists else None
        number = first if last is None else int(last) + 1
        self._counter.set({"last": number})
        return number

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
        jobs = [Job.model_validate(s.to_dict()) for s in self._jobs.stream()]
        return [j for j in jobs if j.state not in FINAL]


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
