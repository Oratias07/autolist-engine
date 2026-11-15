from __future__ import annotations

from autolist.core.models import JobState
from autolist.orchestrator.job_runner import run_task
from autolist.orchestrator.watchdog import run_watchdog
from tests.orchestrator.rig import EMAIL_TEXT, Rig, email, extraction, item, q

S = JobState


async def _open(rig: Rig) -> None:
    rig.messages["g1"] = email(EMAIL_TEXT)
    await run_task(rig.rt, {"event": "email_received", "gmail_id": "g1"})


async def test_active_job_past_the_limit_fails() -> None:
    rig = Rig(extraction([item()]))
    await _open(rig)  # GENERATING, its task never ran
    rig.clock.advance(seconds=599)
    assert run_watchdog(rig.rt) == {"failed": [], "expired": []}
    rig.clock.advance(seconds=2)
    assert run_watchdog(rig.rt) == {"failed": ["T-1"], "expired": []}
    job = rig.jobs.get("T-1")
    assert job is not None and job.state == S.FAILED and "watchdog" in (job.error or "")
    assert rig.mail.templates()[-2:] == ["failed", "developer_alert"]


async def test_waiting_job_expires_after_the_limit() -> None:
    rig = Rig(extraction([item(price=q())]))
    await _open(rig)  # NEEDS_INFO
    rig.clock.advance(days=14)
    assert run_watchdog(rig.rt)["expired"] == []
    rig.clock.advance(minutes=1)
    assert run_watchdog(rig.rt)["expired"] == ["T-1"]
    expired = rig.mail.sent[-1]
    assert expired.template == "expired" and expired.variables["days"] == "14"
    assert expired.thread_id == rig.jobs.get("T-1").thread_id  # type: ignore[union-attr]
    assert run_watchdog(rig.rt) == {"failed": [], "expired": []}  # final: not again
