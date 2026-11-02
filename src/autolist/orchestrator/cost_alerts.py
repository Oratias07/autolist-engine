"""Deliver cost alerts: drain the ledger's outbox and send the owner one email per drain.

Wiring only. The numbers come from ``CostLedger.month_summary``; the wording comes from the
store repo's reply templates. See ``orchestrator/SPEC.md`` (P0-09).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from autolist.core.cost_ledger import CostLedger
from autolist.core.models import (
    CostCapReached,
    CostSummary,
    CostThresholdCrossed,
    OutboundMessage,
)


class Sender(Protocol):
    def send(self, msg: OutboundMessage) -> str: ...


class OpenJobsCounter(Protocol):
    def count(self) -> int: ...


class NoJobsYet:
    """Phase 0: no job runner exists, so no job can be open. Phase 1 uses the job store."""

    def count(self) -> int:
        return 0


def _usd(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _cost_alert_vars(s: CostSummary) -> dict[str, str]:
    return {
        "month_spend": _usd(s.month_spend),
        "monthly_cap": _usd(s.monthly_cap),
        "llm_spend": _usd(s.by_category["llm"]),
        "image_spend": _usd(s.by_category["image"]),
        "search_spend": _usd(s.by_category["search"]),
        "jobs_count": str(s.jobs_count),
        "next_threshold": _usd(s.next_threshold),
    }


def _cap_vars(s: CostSummary, open_jobs: int) -> dict[str, str]:
    return {
        "monthly_cap": _usd(s.monthly_cap),
        "reset_date": _date(s.reset_date),
        "open_jobs_count": str(open_jobs),
    }


def deliver_cost_alerts(
    ledger: CostLedger,
    channel: Sender,
    owner: str,
    open_jobs: OpenJobsCounter,
    now: datetime,
) -> list[str]:
    """Send at most one owner email for the pending alerts. Returns the channel message ids."""
    events = ledger.drain_alerts()
    if not events:
        return []
    try:
        summary = ledger.month_summary(now)
        if any(isinstance(e, CostCapReached) for e in events):
            msg = OutboundMessage(
                channel="email",
                to=owner,
                template="budget_cap_reached",
                variables=_cap_vars(summary, open_jobs.count()),
            )
        else:
            # one email for the highest threshold crossed since the last drain
            if not all(isinstance(e, CostThresholdCrossed) for e in events):
                raise TypeError(f"unknown cost alert kinds: {[e.kind for e in events]}")
            msg = OutboundMessage(
                channel="email",
                to=owner,
                template="cost_alert",
                variables=_cost_alert_vars(summary),
            )
        return [channel.send(msg)]
    except Exception:
        ledger.requeue_alerts(events)
        raise
