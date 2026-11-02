"""P0-09 acceptance: cost alerts reach the owner exactly once, with the right numbers."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autolist.core.config import parse_store_config
from autolist.core.cost_ledger import CostLedger, InMemoryLedgerStore
from autolist.core.models import CostEntry, OutboundMessage
from autolist.orchestrator.cost_alerts import NoJobsYet, deliver_cost_alerts
from tests.core.conftest import VALID_STORE

OWNER = "owner@example.com"
NOW = datetime(2026, 11, 15, 10, tzinfo=UTC)


class FakeChannel:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[OutboundMessage] = []
        self.fail = fail

    def send(self, msg: OutboundMessage) -> str:
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(msg)
        return f"id-{len(self.sent)}"


class FixedJobs:
    def __init__(self, n: int) -> None:
        self.n = n

    def count(self) -> int:
        return self.n


def _ledger() -> CostLedger:
    cfg = parse_store_config(copy.deepcopy(VALID_STORE))
    return CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), lambda: NOW)


def _rec(ledger: CostLedger, usd: str, category: str = "llm", job_id: str | None = "job-1") -> None:
    ledger.record(
        CostEntry.model_validate(
            {
                "at": NOW,
                "job_id": job_id,
                "provider": "p",
                "category": category,
                "operation": "op",
                "units": 1,
                "unit": "call",
                "usd": usd,
                "estimated": False,
            }
        )
    )


def _seed_29_90(ledger: CostLedger) -> None:
    _rec(ledger, "20.00", "llm", "job-1")
    _rec(ledger, "9.70", "image", "job-2")
    _rec(ledger, "0.20", "search", None)


def test_crossing_30_sends_exactly_one_cost_alert_with_correct_numbers() -> None:
    ledger, ch = _ledger(), FakeChannel()
    _seed_29_90(ledger)
    assert deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW) == []
    _rec(ledger, "0.20", "llm", "job-3")
    assert deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW) == ["id-1"]
    assert len(ch.sent) == 1
    msg = ch.sent[0]
    assert msg.to == OWNER
    assert msg.template == "cost_alert"
    assert msg.variables == {
        "month_spend": "30.10",
        "monthly_cap": "60.00",
        "llm_spend": "20.20",
        "image_spend": "9.70",
        "search_spend": "0.20",
        "jobs_count": "3",
        "next_threshold": "40.00",
    }


def test_second_entry_sends_nothing() -> None:
    ledger, ch = _ledger(), FakeChannel()
    _seed_29_90(ledger)
    _rec(ledger, "0.20")
    deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW)
    _rec(ledger, "0.20")
    assert deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW) == []
    assert len(ch.sent) == 1


def test_jump_over_two_thresholds_sends_one_email_for_the_highest() -> None:
    ledger, ch = _ledger(), FakeChannel()
    _rec(ledger, "41.00", job_id=None)
    deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW)
    assert len(ch.sent) == 1
    assert ch.sent[0].variables["next_threshold"] == "50.00"


def test_after_last_threshold_next_is_the_cap() -> None:
    ledger, ch = _ledger(), FakeChannel()
    _rec(ledger, "50.10", job_id=None)
    deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW)
    assert ch.sent[0].variables["next_threshold"] == "60.00"


def test_reaching_cap_sends_budget_cap_reached_only_and_blocks_paid_calls() -> None:
    ledger, ch = _ledger(), FakeChannel()
    _seed_29_90(ledger)
    _rec(ledger, "30.10", job_id=None)  # month = 60.00
    assert deliver_cost_alerts(ledger, ch, OWNER, FixedJobs(2), NOW) == ["id-1"]
    assert [m.template for m in ch.sent] == ["budget_cap_reached"]
    assert ch.sent[0].variables == {
        "monthly_cap": "60.00",
        "reset_date": "01.12.2026",
        "open_jobs_count": "2",
    }
    assert not ledger.authorize(None, "anthropic", "extract", Decimal("0.01")).allowed
    assert not ledger.authorize(None, "anthropic", "extract", Decimal(0)).allowed
    _rec(ledger, "0.10", job_id=None)
    assert deliver_cost_alerts(ledger, ch, OWNER, FixedJobs(2), NOW) == []


def test_send_failure_keeps_events_for_the_next_call() -> None:
    ledger = _ledger()
    _rec(ledger, "30.10", job_id=None)
    with pytest.raises(RuntimeError):
        deliver_cost_alerts(ledger, FakeChannel(fail=True), OWNER, NoJobsYet(), NOW)
    ch = FakeChannel()
    assert deliver_cost_alerts(ledger, ch, OWNER, NoJobsYet(), NOW) == ["id-1"]
    assert ch.sent[0].template == "cost_alert"


def test_no_jobs_yet_counts_zero() -> None:
    assert NoJobsYet().count() == 0
