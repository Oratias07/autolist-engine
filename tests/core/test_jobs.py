"""State machine and job store (fast-track design)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autolist.core.errors import IllegalTransition
from autolist.core.job_store import FirestoreJobStore, InMemoryJobStore, new_job
from autolist.core.models import JobFact, JobState
from autolist.core.state_machine import ACTIVE, FINAL, TRANSITIONS, transition
from tests.fakes.firestore import FakeFirestore

T0 = datetime(2026, 11, 15, 10, tzinfo=UTC)
S = JobState


@pytest.fixture(params=["memory", "firestore"])
def store(request: pytest.FixtureRequest) -> Any:
    return InMemoryJobStore() if request.param == "memory" else FirestoreJobStore(FakeFirestore())


def _job(store: Any, index: int = 0) -> Any:
    return new_job(
        store,
        prefix="JOB-",
        first_number=1001,
        source_gmail_id="g1",
        source_thread_id="t0",
        product_index=index,
        now=T0,
    )


def test_numbers_start_at_first_and_increase(store: Any) -> None:
    a, b = _job(store), _job(store, 1)
    assert (a.job_id, b.job_id) == ("JOB-1001", "JOB-1002")
    assert a.state == S.RECEIVED
    assert a.active_since == T0


def test_save_get_and_thread_index(store: Any) -> None:
    job = _job(store)
    assert store.by_thread("t9") is None
    job = job.model_copy(update={"thread_id": "t9"})
    store.save(job)
    assert store.by_thread("t9") == job
    assert store.get("JOB-1001") == job
    assert store.get("JOB-9") is None


def test_open_jobs_excludes_final(store: Any) -> None:
    a, b = _job(store), _job(store, 1)
    store.save(transition(a, S.FAILED, T0, error="x"))
    assert [j.job_id for j in store.open_jobs()] == [b.job_id]


def test_by_source_thread(store: Any) -> None:
    a, b = _job(store), _job(store, 1)
    assert [j.job_id for j in store.by_source_thread("t0")] == [a.job_id, b.job_id]
    assert store.by_source_thread("t9") == []


def test_happy_path_transitions() -> None:
    job = _job(InMemoryJobStore())
    for target in (S.GENERATING, S.AWAITING_APPROVAL, S.PUBLISHING, S.SAVED_HIDDEN):
        job = transition(job, target, T0 + timedelta(minutes=1))
    assert job.state == S.SAVED_HIDDEN
    assert job.active_since is None


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (S.RECEIVED, S.PUBLISHING),
        (S.NEEDS_INFO, S.PUBLISHING),
        (S.GENERATING, S.SAVED_HIDDEN),
        (S.AWAITING_APPROVAL, S.SAVED_HIDDEN),
        (S.SAVED_HIDDEN, S.PUBLISHING),
        (S.CANCELLED, S.GENERATING),
        (S.FAILED, S.RECEIVED),
    ],
)
def test_illegal_transitions_raise(start: JobState, target: JobState) -> None:
    job = _job(InMemoryJobStore()).model_copy(update={"state": start})
    with pytest.raises(IllegalTransition):
        transition(job, target, T0)


def test_failed_needs_a_reason() -> None:
    job = _job(InMemoryJobStore())
    with pytest.raises(IllegalTransition, match="reason"):
        transition(job, S.FAILED, T0)
    assert transition(job, S.FAILED, T0, error="timeout").error == "timeout"


def test_final_states_have_no_exits_and_publishing_never_skips_verification() -> None:
    for state in FINAL:
        assert TRANSITIONS[state] == frozenset()
    # only PUBLISHING reaches SAVED_HIDDEN
    assert [s for s, t in TRANSITIONS.items() if S.SAVED_HIDDEN in t] == [S.PUBLISHING]
    assert ACTIVE.isdisjoint(FINAL)


def test_waiting_state_clears_active_since() -> None:
    job = transition(_job(InMemoryJobStore()), S.NEEDS_INFO, T0)
    assert job.active_since is None


def test_latest_fact_wins() -> None:
    job = _job(InMemoryJobStore())
    job.facts.append(JobFact(key="price", value="499", quote="499", source="email", gmail_id="g1"))
    job.facts.append(
        JobFact(key="price", value="450", quote="המחיר 450", source="reply", gmail_id="g2")
    )
    fact = job.fact("price")
    assert fact is not None
    assert fact.value == "450"
    assert job.fact("barcode") is None


def test_publish_is_claimed_once(store: Any) -> None:
    assert store.claim_publish("JOB-1001", T0)
    assert not store.claim_publish("JOB-1001", T0 + timedelta(minutes=5))
    assert store.claim_publish("JOB-1002", T0)


def test_a_taken_number_is_skipped_by_an_overlapping_call() -> None:
    """Two calls that both read the same counter never get the same number."""
    db = FakeFirestore()
    store = FirestoreJobStore(db)
    db.collection("job_numbers").document("1001").create({"taken": True})  # the other call
    assert store.next_number(1001) == 1002
    assert store.next_number(1001) == 1003
