"""Live eval of choose_category v1 (thin CLI over content.categories).

Reads the store's real category list (`GET /categories`, read-only), then runs the cases in the
store repo's fixtures/samples/p1-09-categories.yaml. Paid (Anthropic, classify model); no
store writes. A case passes when the primary is one of the accepted ids and every chosen id
is in the accepted or the allowed-secondary list.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from autolist.content.categories import choose_categories, parse_store_categories
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import JobFact
from autolist.core.playbook import load_playbook
from autolist.llm.client import AnthropicClient
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import JsonFileRegistry


def _facts(case: dict[str, Any]) -> list[JobFact]:
    out = [
        JobFact(key="name", value=case["name"], quote=case["name"], source="email", gmail_id="e")
    ]
    out += [
        JobFact(key="detail", value=d, quote=d, source="email", gmail_id="e")
        for d in case.get("details", [])
    ]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    data = yaml.safe_load(
        (store_dir() / "fixtures" / "samples" / "p1-09-categories.yaml").read_text(encoding="utf-8")
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    registry = JsonFileRegistry(args.out / "fixtures" / "istores" / "registry-unused.json")
    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        categories = parse_store_categories(await client.get_categories())
    ledger_path = args.out / "fixtures" / "ledger" / "p0-anthropic.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    llm = AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger)
    playbook = load_playbook()
    names = {c.category_id: c.name for c in categories}
    before = ledger.month_spend()
    lines = [f"# choose_category v1 eval - {stamp}", "", f"categories read: {len(categories)}", ""]
    passed = 0
    for n, case in enumerate(data["cases"], 1):
        res = choose_categories(
            _facts(case),
            owner_hint=case.get("hint"),
            categories=categories,
            llm=llm,
            playbook=playbook,
            job_id=f"p1-09-eval-{n}",
        )
        primary_ok: list[int] = case["primary_any_of"]
        allowed = set(primary_ok) | set(case.get("secondary_any_of", []))
        problems = []
        if case.get("expect_none"):
            if res.category_ids:
                problems.append(f"expected none, got {res.category_ids}")
        elif not res.category_ids or res.category_ids[0] not in primary_ok:
            problems.append(f"primary {res.category_ids[:1]} not in {primary_ok}")
        elif set(res.category_ids) - allowed:
            problems.append(f"unexpected {sorted(set(res.category_ids) - allowed)}")
        passed += not problems
        chosen = ", ".join(f"{i} {names.get(i, '?')}" for i in res.category_ids) or "-"
        lines.append(
            f"- {'PASS' if not problems else 'FAIL'} {case['id']}: {chosen}; question {res.question!r}"
        )
        lines += [f"  - {p}" for p in problems]
    lines += [
        "",
        f"**{passed}/{len(data['cases'])} passed; cost {ledger.month_spend() - before:.4f} USD**",
    ]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-09-categories.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
