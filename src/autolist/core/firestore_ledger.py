"""Cost ledger state in Firestore: one document per month, plus the alert outbox.

A month document holds that month's entries, total, alerts sent and cap flag. With the monthly
cap in place a month stays far below Firestore's 1 MiB document limit. Job totals are not
stored: they are the sum of a job's entries and are rebuilt on load.
The service runs one instance and one job at a time, so writes do not race.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from autolist.core.cost_ledger import LedgerState
from autolist.core.models import CostEntry

OUTBOX = "_outbox"


class FirestoreLedgerStore:
    def __init__(self, client: Any, timezone: str, collection: str = "cost_ledger") -> None:
        self._col = client.collection(collection)
        self._tz = ZoneInfo(timezone)  # the same month boundaries as CostLedger

    def _month(self, entry: CostEntry) -> str:
        return entry.at.astimezone(self._tz).strftime("%Y-%m")

    def load(self) -> LedgerState:
        raw: dict[str, Any] = {
            "month_totals": {},
            "entries": [],
            "alerts_sent": {},
            "cap_reached": [],
            "pending_alerts": [],
        }
        for snap in self._col.stream():
            data = snap.to_dict() or {}
            if snap.id == OUTBOX:
                raw["pending_alerts"] = data.get("pending_alerts", [])
                continue
            raw["month_totals"][snap.id] = data["total"]
            raw["entries"].extend(data.get("entries", []))
            if data.get("alerts_sent"):
                raw["alerts_sent"][snap.id] = data["alerts_sent"]
            if data.get("cap_reached"):
                raw["cap_reached"].append(snap.id)
        state = LedgerState.model_validate(raw)
        state.entries.sort(key=lambda e: e.at)
        for entry in state.entries:
            if entry.job_id is not None:
                state.job_totals[entry.job_id] = (
                    state.job_totals.get(entry.job_id, Decimal(0)) + entry.usd
                )
        return state

    def save(self, state: LedgerState) -> None:
        dumped = state.model_dump(mode="json")
        by_month: dict[str, list[Any]] = {}
        for raw_entry, entry in zip(dumped["entries"], state.entries, strict=True):
            by_month.setdefault(self._month(entry), []).append(raw_entry)
        for month, total in dumped["month_totals"].items():
            self._col.document(month).set(
                {
                    "total": total,
                    "entries": by_month.get(month, []),
                    "alerts_sent": dumped["alerts_sent"].get(month, []),
                    "cap_reached": month in dumped["cap_reached"],
                }
            )
        self._col.document(OUTBOX).set({"pending_alerts": dumped["pending_alerts"]})
