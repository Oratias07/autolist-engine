"""The Firestore ledger store behaves like the in-memory one (infra SPEC acceptance 7)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from autolist.core.config import parse_store_config
from autolist.core.cost_ledger import CostLedger, InMemoryLedgerStore
from autolist.core.firestore_ledger import FirestoreLedgerStore
from autolist.core.models import CostEntry
from tests.core.conftest import VALID_STORE
from tests.fakes.firestore import FakeFirestore

NOW = datetime(2026, 11, 15, 10, tzinfo=UTC)


def _entry(usd: str, at: datetime, job: str | None, category: str = "llm") -> CostEntry:
    return CostEntry.model_validate(
        {
            "at": at,
            "job_id": job,
            "provider": "p",
            "category": category,
            "operation": "op",
            "units": 1,
            "unit": "call",
            "usd": usd,
            "estimated": False,
        }
    )


def _ledgers() -> tuple[CostLedger, CostLedger, FakeFirestore]:
    cfg = parse_store_config(copy.deepcopy(VALID_STORE))
    fake = FakeFirestore()
    fs = CostLedger(
        cfg.costs,
        cfg.store.timezone,
        FirestoreLedgerStore(fake, cfg.store.timezone),
        lambda: NOW,
    )
    mem = CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), lambda: NOW)
    return fs, mem, fake


def test_same_results_as_in_memory_across_months_and_alerts() -> None:
    fs, mem, fake = _ledgers()
    steps: list[tuple[str, datetime, str | None, str]] = [
        ("5.00", datetime(2026, 10, 20, tzinfo=UTC), "job-0", "llm"),
        ("1.50", NOW, "job-1", "llm"),
        ("0.40", NOW, "job-1", "image"),
        ("29.00", NOW, None, "search"),
        ("30.00", NOW, "job-2", "image"),
    ]
    for usd, at, job, cat in steps:
        assert fs.record(_entry(usd, at, job, cat)) == mem.record(_entry(usd, at, job, cat))
    assert fs.month_summary(NOW) == mem.month_summary(NOW)
    assert fs.month_spend() == mem.month_spend()
    a = fs.authorize("job-1", "p", "op", Decimal("0.2"))
    b = mem.authorize("job-1", "p", "op", Decimal("0.2"))
    assert (a.allowed, a.reason) == (b.allowed, b.reason)
    assert fs.drain_alerts() == mem.drain_alerts()
    assert fs.drain_alerts() == []
    assert set(fake.data["cost_ledger"]) == {"2026-10", "2026-11", "_outbox"}


def test_state_survives_a_new_store_instance() -> None:
    fs, _, fake = _ledgers()
    fs.record(_entry("1.90", NOW, "job-1"))
    cfg = parse_store_config(copy.deepcopy(VALID_STORE))
    again = CostLedger(
        cfg.costs,
        cfg.store.timezone,
        FirestoreLedgerStore(fake, cfg.store.timezone),
        lambda: NOW,
    )
    assert again.month_spend() == Decimal("1.90")
    # per-job cap is rebuilt from entries: 1.90 + 0.20 > 2
    assert not again.authorize("job-1", "p", "op", Decimal("0.20")).allowed


def test_month_boundary_uses_store_timezone() -> None:
    fs, _, fake = _ledgers()
    # 22:30 UTC on 31.12 is already January in Israel
    fs.record(_entry("1", datetime(2026, 12, 31, 22, 30, tzinfo=UTC), None))
    docs: dict[str, Any] = fake.data["cost_ledger"]
    assert "2027-01" in docs
    assert len(docs["2027-01"]["entries"]) == 1
