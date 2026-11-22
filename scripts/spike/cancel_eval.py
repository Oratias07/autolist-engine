"""Live eval of resolve_cancel v1 (thin CLI over agent_runtime.cancel_resolver).

Runs the cases in the store repo's fixtures/samples/p1-11-cancel.yaml. Paid (Anthropic,
classify model); no store or mailbox access.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import yaml

from autolist.agent_runtime.cancel_resolver import CancelUnclear, resolve_cancel
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import ChannelMessage
from autolist.llm.client import AnthropicClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    data = yaml.safe_load(
        (store_dir() / "fixtures" / "samples" / "p1-11-cancel.yaml").read_text(encoding="utf-8")
    )
    ledger_path = args.out / "fixtures" / "ledger" / "p0-anthropic.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    llm = AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    before = ledger.month_spend()
    lines = [f"# resolve_cancel v1 eval - {stamp}", ""]
    passed = 0
    for n, case in enumerate(data["cases"], 1):
        msg = ChannelMessage(
            channel="email",
            message_id=f"e{n}",
            thread_id="t",
            sender="owner@example.com",
            authenticated=True,
            received_at=datetime.now(UTC),
            subject="s",
            text=case["reply"],
        )
        try:
            got: list[int] | str = [
                i + 1 for i in resolve_cancel(msg, case["products"], llm=llm, job_id=f"p1-11-{n}")
            ]
        except CancelUnclear:
            got = "unclear"
        ok = got == case["expect"]
        passed += ok
        lines.append(
            f"- {'PASS' if ok else 'FAIL'} {case['id']}: got {got}, expected {case['expect']}"
        )
    lines += [
        "",
        f"**{passed}/{len(data['cases'])} passed; cost {ledger.month_spend() - before:.4f} USD**",
    ]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-11-cancel.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
