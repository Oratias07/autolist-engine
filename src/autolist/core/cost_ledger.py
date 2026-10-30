"""Cost ledger: refuse paid calls that would exceed a cap; emit monthly threshold events.

Months are calendar months in the store's timezone. Storage is behind ``LedgerStore``:
in-memory and a local JSON file in phase 0, Firestore from phase 1.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from autolist.core.errors import CostCapExceeded
from autolist.core.models import CostDecision, CostEntry, CostsConfig, CostThresholdCrossed


class LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month_totals: dict[str, Decimal] = Field(default_factory=dict)
    job_totals: dict[str, Decimal] = Field(default_factory=dict)
    alerts_sent: dict[str, list[Decimal]] = Field(default_factory=dict)
    pending_alerts: list[CostThresholdCrossed] = Field(default_factory=list)
    entries: list[CostEntry] = Field(default_factory=list)


class LedgerStore(Protocol):
    def load(self) -> LedgerState: ...
    def save(self, state: LedgerState) -> None: ...


class InMemoryLedgerStore:
    def __init__(self, state: LedgerState | None = None) -> None:
        self._state = state.model_copy(deep=True) if state else LedgerState()

    def load(self) -> LedgerState:
        return self._state.model_copy(deep=True)

    def save(self, state: LedgerState) -> None:
        self._state = state.model_copy(deep=True)


class JsonFileLedgerStore:
    """Phase-0 only. Not safe across processes."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> LedgerState:
        if not self._path.exists():
            return LedgerState()
        return LedgerState.model_validate_json(self._path.read_text(encoding="utf-8"))

    def save(self, state: LedgerState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.model_dump(mode="json"), indent=1), encoding="utf-8")
        tmp.replace(self._path)


class CostLedger:
    def __init__(
        self,
        costs: CostsConfig,
        timezone: str,
        store: LedgerStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._costs = costs
        self._tz = ZoneInfo(timezone)
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()

    def month_key(self, at: datetime) -> str:
        if at.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return at.astimezone(self._tz).strftime("%Y-%m")

    def month_spend(self) -> Decimal:
        return self._store.load().month_totals.get(self.month_key(self._clock()), Decimal(0))

    def job_spend(self, job_id: str) -> Decimal:
        return self._store.load().job_totals.get(job_id, Decimal(0))

    def authorize(
        self,
        job_id: str | None,
        provider: str,
        operation: str,
        est_usd: Decimal,
    ) -> CostDecision:
        """Decide before a paid call. Never makes the call itself."""
        if est_usd < 0:
            raise ValueError("est_usd must be >= 0")
        state = self._store.load()
        month = self.month_key(self._clock())
        month_total = state.month_totals.get(month, Decimal(0))
        if month_total + est_usd > self._costs.monthly_cap:
            return CostDecision(
                allowed=False,
                reason=(
                    f"monthly cap {self._costs.monthly_cap} USD: spent {month_total}, "
                    f"{provider}/{operation} estimate {est_usd}"
                ),
            )
        if job_id is not None:
            job_total = state.job_totals.get(job_id, Decimal(0))
            if job_total + est_usd > self._costs.per_job_cap:
                return CostDecision(
                    allowed=False,
                    reason=(
                        f"per-job cap {self._costs.per_job_cap} USD for {job_id}: spent "
                        f"{job_total}, {provider}/{operation} estimate {est_usd}"
                    ),
                )
        return CostDecision(allowed=True, reason="within caps")

    def require(
        self,
        job_id: str | None,
        provider: str,
        operation: str,
        est_usd: Decimal,
    ) -> None:
        """``authorize`` that raises ``CostCapExceeded`` when refused."""
        decision = self.authorize(job_id, provider, operation, est_usd)
        if not decision.allowed:
            raise CostCapExceeded(decision.reason)

    def record(self, entry: CostEntry) -> list[CostThresholdCrossed]:
        """Record a spend and return the threshold events it newly triggered."""
        with self._lock:
            state = self._store.load()
            month = self.month_key(entry.at)
            new_total = state.month_totals.get(month, Decimal(0)) + entry.usd
            state.month_totals[month] = new_total
            if entry.job_id is not None:
                state.job_totals[entry.job_id] = (
                    state.job_totals.get(entry.job_id, Decimal(0)) + entry.usd
                )
            state.entries.append(entry)
            sent = state.alerts_sent.setdefault(month, [])
            events: list[CostThresholdCrossed] = []
            for threshold in self._costs.alert_thresholds:
                if new_total >= threshold and threshold not in sent:
                    sent.append(threshold)
                    events.append(
                        CostThresholdCrossed(
                            month=month,
                            threshold=threshold,
                            month_spend=new_total,
                            monthly_cap=self._costs.monthly_cap,
                        )
                    )
            state.pending_alerts.extend(events)
            self._store.save(state)
            return events

    def drain_alerts(self) -> list[CostThresholdCrossed]:
        """Return and clear threshold events not yet delivered (the orchestrator's outbox).

        Callers deep inside modules (llm, images) record costs but do not send anything, so
        events are kept here until the orchestrator drains them and passes them to channels.
        """
        with self._lock:
            state = self._store.load()
            events = list(state.pending_alerts)
            state.pending_alerts.clear()
            self._store.save(state)
            return events
