"""write_title on the write model (Sonnet) and on the classify model (Haiku), side by side.

Thin CLI over content.writer: the same prompt, facts and checks as the service. Reads the
store repo's fixtures/samples/p1-13-title.yaml. Paid (Anthropic), a few cents; no store or
mailbox access. A title fails a check when a number is not in its cited facts, or it has "!"
or an emoji.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from autolist.content.writer import TITLE_TOKENS, TitleOut, _numbers, writer_facts
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import JobFact
from autolist.core.playbook import load_glossary, load_playbook
from autolist.llm.client import AnthropicClient, LLMRequest, ModelRole


def _facts(p: dict[str, object]) -> list[JobFact]:
    out = [
        JobFact(key=k, value=str(v), quote=str(v), source="email", gmail_id="e")
        for k, v in dict(p["facts"]).items()  # type: ignore[call-overload]
    ]
    out += [
        JobFact(key="detail", value=d, quote=d, source="email", gmail_id="e")
        for d in list(p.get("details") or [])  # type: ignore[call-overload]
    ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    playbook, glossary = load_playbook(), load_glossary()
    data = yaml.safe_load(
        (store_dir() / "fixtures" / "samples" / "p1-13-title.yaml").read_text(encoding="utf-8")
    )
    ledger_path = args.out / "fixtures" / "ledger" / "p0-anthropic.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    llm = AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger)
    roles: dict[str, ModelRole] = {"sonnet (write)": "write", "haiku (classify)": "classify"}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# write_title: Sonnet vs Haiku - {stamp}", ""]
    spent: dict[str, Decimal] = {r: Decimal(0) for r in roles}
    failed: dict[str, int] = {r: 0 for r in roles}
    for p in data["products"]:
        numbered = writer_facts(_facts(p))
        by_id = {f["id"]: f["text"] for f in numbered}
        lines.append(f"## {p['id']}")
        for label, role in roles.items():
            before = ledger.month_spend()
            res = llm.complete(
                LLMRequest(
                    prompt="write_title",
                    variables={
                        "facts_json": json.dumps(numbered, ensure_ascii=False, indent=1),
                        "title_rules": yaml.safe_dump(playbook["title"], allow_unicode=True),
                        "glossary": yaml.safe_dump(
                            glossary["terms"], allow_unicode=True, sort_keys=False
                        ),
                    },
                    output_schema=TitleOut,
                    model_role=role,
                    job_id=f"p1-13-{role}",
                    max_output_tokens=TITLE_TOKENS,
                )
            )
            spent[label] += ledger.month_spend() - before
            t = res.parsed
            if not isinstance(t, TitleOut):
                raise TypeError("write_title returned no parsed result")
            cited = " ".join(by_id.get(i, "") for i in t.fact_ids)
            problems = []
            if bad := _numbers(t.title) - _numbers(cited):
                problems.append(f"numbers not in cited facts {sorted(bad)}")
            if "!" in t.title:
                problems.append("!")
            failed[label] += bool(problems)
            mark = "FAIL " + "; ".join(problems) if problems else "ok"
            lines.append(f"- {label}: {t.title}  [{mark}]")
            if t.open_points:
                lines.append(f"  - open points: {t.open_points}")
        lines.append("")
    n = len(data["products"])
    lines.append("## Summary")
    for label in roles:
        lines.append(
            f"- {label}: {n - failed[label]}/{n} pass the checks; "
            f"{spent[label]:.4f} USD ({spent[label] / n:.4f} per title)"
        )
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-13-title.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
