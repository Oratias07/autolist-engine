"""P1-07 live eval of classify_reply v1 through agent_runtime.classify_reply (thin CLI).

Runs the cases in the store repo's fixtures/samples/p1-07-classify.yaml with the configured
classify model. Paid (Anthropic), through the cost ledger; no store writes. A case passes when
the intent matches, every expected fact matches, and no unexpected fact was kept.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from autolist.agent_runtime.classifier import classify_reply
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import ChannelMessage, ReplyClassification
from autolist.llm.client import AnthropicClient


def _norm(v: Any) -> Any:
    try:
        return Decimal(str(v)).normalize()
    except (InvalidOperation, ValueError):
        return str(v)


def _check(res: ReplyClassification, expect: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if res.intent != expect["intent"]:
        problems.append(f"intent {res.intent} != {expect['intent']}")
    got = {f.key: f.value for f in res.facts}
    want = {k: str(v) for k, v in (expect.get("facts") or {}).items()}
    for key, value in want.items():
        if _norm(got.get(key)) != _norm(value):
            problems.append(f"{key} {got.get(key)!r} != {value!r}")
    extra = sorted(set(got) - set(want))
    if extra:
        problems.append(f"unexpected facts {extra}")
    if expect.get("question") and not res.question:
        problems.append("expected a question")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    data = yaml.safe_load(
        (store_dir() / "fixtures" / "samples" / "p1-07-classify.yaml").read_text(encoding="utf-8")
    )
    ledger_path = args.out / "fixtures" / "ledger" / "p0-anthropic.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    llm = AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    before = ledger.month_spend()
    lines = [
        f"# P1-07 classify_reply v1 eval - {stamp}",
        "",
        f"model: {cfg.llm.models.classify}",
        "",
    ]
    passed = 0
    cases: list[dict[str, Any]] = data["cases"]
    for case in cases:
        msg = ChannelMessage(
            channel="email",
            message_id=f"eval-{case['id']}",
            thread_id="eval",
            sender="owner@example.com",
            authenticated=True,
            received_at=datetime.now(UTC),
            subject=None,
            text=case["reply"],
        )
        res = classify_reply(
            msg, llm=llm, job_context=data["job_context"], job_id=f"p1-07-eval-{case['id']}"
        )
        problems = _check(res, case["expect"])
        passed += not problems
        facts = ", ".join(f"{f.key}={f.value!r} ({f.quote!r})" for f in res.facts) or "-"
        lines += [
            f"## {case['id']}: {'PASS' if not problems else 'FAIL'}",
            f"- reply: {case['reply']!r}",
            f"- intent: {res.intent}; facts: {facts}; unverified: {res.unverified_fields}",
            f"- also_wording_or_image: {res.also_wording_or_image}; question: {res.question!r}",
            *(f"- problem: {p}" for p in problems),
            "",
        ]
    lines += ["", f"**{passed}/{len(cases)} passed; cost {ledger.month_spend() - before:.4f} USD**"]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-07-classify.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{passed}/{len(cases)} passed; report {out.name}")


if __name__ == "__main__":
    main()
