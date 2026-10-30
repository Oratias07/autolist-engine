from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from autolist.core.config import parse_store_config
from autolist.core.cost_ledger import (
    CostLedger,
    InMemoryLedgerStore,
    JsonFileLedgerStore,
    LedgerState,
)
from autolist.core.errors import CostCapExceeded
from autolist.core.models import CostEntry


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _entry(usd: str, at: datetime, job_id: str | None = "job-1") -> CostEntry:
    return CostEntry(
        at=at,
        job_id=job_id,
        provider="anthropic",
        operation="extract",
        units=1000,
        unit="tokens",
        usd=Decimal(usd),
        estimated=False,
    )


@pytest.fixture
def ledger_parts(store_data: dict[str, Any]) -> tuple[CostLedger, Clock]:
    cfg = parse_store_config(store_data)
    clock = Clock(datetime(2026, 10, 30, 12, 0, tzinfo=UTC))
    return CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), clock), clock


def test_monthly_cap_refuses_before_the_call(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, clock = ledger_parts
    for _ in range(119):  # 119 * 0.50 = 59.50, spread over jobs to stay under the job cap
        ledger.record(_entry("0.50", clock.now, job_id=None))
    assert ledger.month_spend() == Decimal("59.50")
    decision = ledger.authorize(None, "gemini", "image", Decimal("0.67"))
    assert decision.allowed is False
    assert "monthly cap" in decision.reason
    with pytest.raises(CostCapExceeded):
        ledger.require(None, "gemini", "image", Decimal("0.67"))
    assert ledger.authorize(None, "gemini", "image", Decimal("0.50")).allowed is True


def test_per_job_cap(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, clock = ledger_parts
    ledger.record(_entry("1.80", clock.now, job_id="job-7"))
    assert ledger.job_spend("job-7") == Decimal("1.80")
    refused = ledger.authorize("job-7", "gemini", "image", Decimal("0.30"))
    assert refused.allowed is False and "per-job cap" in refused.reason
    assert ledger.authorize("job-8", "gemini", "image", Decimal("0.30")).allowed is True
    ledger.require("job-8", "gemini", "image", Decimal("0.30"))


def test_thresholds_emit_once_each(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, clock = ledger_parts
    events: list[Any] = []
    for _ in range(29):
        events += ledger.record(_entry("1.00", clock.now, job_id=None))
    assert events == []
    events += ledger.record(_entry("1.00", clock.now, job_id=None))  # 30.00
    events += ledger.record(_entry("0.50", clock.now, job_id=None))  # repeat: no re-emit
    events += ledger.record(_entry("9.50", clock.now, job_id=None))  # 40.00
    events += ledger.record(_entry("1.00", clock.now, job_id=None))
    assert [e.threshold for e in events] == [Decimal(30), Decimal(40)]
    assert events[0].month == "2026-10" and events[0].monthly_cap == Decimal(60)


def test_one_record_crossing_two_thresholds(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, clock = ledger_parts
    ledger.record(_entry("29.00", clock.now, job_id=None))
    events = ledger.record(_entry("12.00", clock.now, job_id=None))
    assert [e.threshold for e in events] == [Decimal(30), Decimal(40)]


def test_new_month_resets(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, clock = ledger_parts
    assert ledger.record(_entry("31.00", clock.now, job_id=None))
    clock.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    assert ledger.month_spend() == Decimal(0)
    assert ledger.authorize(None, "anthropic", "write", Decimal("40")).allowed is True
    events = ledger.record(_entry("30.00", clock.now, job_id=None))
    assert [(e.month, e.threshold) for e in events] == [("2026-11", Decimal(30))]


def test_month_follows_store_timezone(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, _ = ledger_parts
    # 22:30 UTC on Oct 31 is already Nov 1 in Asia/Jerusalem (UTC+2 after DST ends).
    assert ledger.month_key(datetime(2026, 10, 31, 22, 30, tzinfo=UTC)) == "2026-11"
    with pytest.raises(ValueError):
        ledger.month_key(datetime(2026, 10, 31, 22, 30))


def test_negative_estimate_rejected(ledger_parts: tuple[CostLedger, Clock]) -> None:
    ledger, _ = ledger_parts
    with pytest.raises(ValueError):
        ledger.authorize(None, "x", "y", Decimal("-1"))


def test_entry_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError):
        _entry("1", datetime(2026, 10, 30))


def test_json_file_store_roundtrip(tmp_path: Path, store_data: dict[str, Any]) -> None:
    cfg = parse_store_config(store_data)
    path = tmp_path / "ledger" / "costs.json"
    store = JsonFileLedgerStore(path)
    assert store.load() == LedgerState()
    clock = Clock(datetime(2026, 10, 30, tzinfo=UTC))
    CostLedger(cfg.costs, cfg.store.timezone, store, clock).record(_entry("30", clock.now))
    reopened = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(path), clock)
    assert reopened.month_spend() == Decimal(30)
    assert reopened.record(_entry("1", clock.now)) == []  # 30 was already sent
