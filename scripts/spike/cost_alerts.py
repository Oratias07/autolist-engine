"""P0-09 live check: cost alerts reach the owner (thin CLI over core + orchestrator + channels).

In-memory ledger seeded with synthetic entries. No paid calls, no store writes.
1. Seed 29.90, record 0.20 -> deliver: exactly one `cost_alert`.
2. Record 0.20 more -> deliver: nothing.
3. Record up to 60.00 -> deliver: exactly one `budget_cap_reached`; authorize() is refused.
Sends real emails from the agent mailbox to the owner. Writes a report to OUT.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from autolist.channels.email.gmail_client import build_service
from autolist.channels.email.mail_sender import GmailSender
from autolist.core.config import get_secret, load_store_config
from autolist.core.cost_ledger import CostLedger, InMemoryLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import CostEntry, OutboundMessage
from autolist.orchestrator.cost_alerts import NoJobsYet, deliver_cost_alerts


class RecordingSender:
    """Sends through Gmail and keeps what was sent, for the report."""

    def __init__(self, inner: GmailSender) -> None:
        self.inner = inner
        self.sent: list[tuple[str, OutboundMessage]] = []

    def send(self, msg: OutboundMessage) -> str:
        gid = self.inner.send(msg)
        self.sent.append((gid, msg))
        return gid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    email_cfg = cfg.channels.email
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    ledger = CostLedger(cfg.costs, cfg.store.timezone, InMemoryLedgerStore(), lambda: now)

    def rec(usd: str, category: str, job_id: str | None) -> None:
        ledger.record(
            CostEntry.model_validate(
                {
                    "at": now,
                    "job_id": job_id,
                    "provider": "p0-09-synthetic",
                    "category": category,
                    "operation": "seed",
                    "units": 0,
                    "unit": "usd",
                    "usd": usd,
                    "estimated": True,
                }
            )
        )

    service = build_service(
        get_secret(cfg, "gmail_oauth_client"), get_secret(cfg, "gmail_refresh_token")
    )
    sender = RecordingSender(GmailSender(service, email_cfg))

    def deliver() -> list[str]:
        return deliver_cost_alerts(ledger, sender, email_cfg.owner_address, NoJobsYet(), now)

    lines = [f"# P0-09 cost alerts - {stamp}", "", "Synthetic in-memory ledger; no paid calls.", ""]
    rec("20.00", "llm", "synthetic-1")
    rec("9.70", "image", "synthetic-2")
    rec("0.20", "search", None)
    lines.append(f"- seeded {ledger.month_spend()} USD; deliver -> {deliver()} (expected [])")
    rec("0.20", "llm", "synthetic-3")
    step1 = deliver()
    lines.append(f"- +0.20 -> {ledger.month_spend()} USD; deliver -> {len(step1)} email(s)")
    rec("0.20", "llm", "synthetic-3")
    step2 = deliver()
    lines.append(f"- +0.20 -> {ledger.month_spend()} USD; deliver -> {len(step2)} email(s)")
    rec(str(cfg.costs.monthly_cap - ledger.month_spend()), "llm", None)
    step3 = deliver()
    decision = ledger.authorize(None, "anthropic", "extract", Decimal("0.01"))
    lines.append(f"- to cap -> {ledger.month_spend()} USD; deliver -> {len(step3)} email(s)")
    lines.append(f"- authorize after cap: allowed={decision.allowed} ({decision.reason})")
    lines += ["", "## Sent", ""]
    for gid, msg in sender.sent:
        lines.append(f"- {msg.template} (gmail id {gid}): {msg.variables}")
    ok = len(step1) == 1 and not step2 and len(step3) == 1 and not decision.allowed
    templates = [m.template for _, m in sender.sent]
    ok = ok and templates == ["cost_alert", "budget_cap_reached"]
    lines += ["", f"**Result: {'PASS' if ok else 'FAIL'}**"]

    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-09-cost-alerts.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
