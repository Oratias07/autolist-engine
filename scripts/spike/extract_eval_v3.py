"""P1-08 live eval of extract_request v3 through intake.parse_request (thin CLI).

Runs the fast-track cases (fixtures/samples/p1-05-extract-v2.yaml) and the ten P0-07 emails
with the configured extract model. Paid (Anthropic), through the cost ledger; no store writes.
A case passes when every expected value matches and every kept fact quotes the email text.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import Attachment, ChannelMessage, ParsedRequest
from autolist.core.playbook import load_playbook, require
from autolist.intake.request_parser import parse_request
from autolist.llm.client import AnthropicClient

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _norm(v: Any) -> Any:
    try:
        return Decimal(str(v)).normalize()
    except (InvalidOperation, ValueError):
        return str(v)


def _check(res: ParsedRequest, expect: dict[str, Any], text: str) -> list[str]:
    p: list[str] = []
    if "items" in expect and len(res.items) != expect["items"]:
        p.append(f"items {len(res.items)} != {expect['items']}")
    if "separated" in expect and res.clearly_separated != expect["separated"]:
        p.append(f"separated {res.clearly_separated} != {expect['separated']}")
    if "unassigned" in expect and res.unassigned_photos != expect["unassigned"]:
        p.append(f"unassigned {res.unassigned_photos}")
    if expect.get("questions") and not res.questions:
        p.append("expected a question")
    for item in res.items:
        p += [
            f"item {item.index}: {f.key} quote not in email"
            for f in item.facts
            if f.quote not in text
        ]
        if item.unverified_fields:
            p.append(f"item {item.index}: unverified {item.unverified_fields}")
    for i, want in enumerate(expect.get("per_item", [])):
        if i >= len(res.items):
            break
        item = res.items[i]
        got = {f.key: f.value for f in item.facts}
        for k, v in (want.get("fields") or {}).items():
            if k == "detail":
                continue
            if v is None and k in got:
                p.append(f"item {i} {k}: expected none, got {got[k]}")
            elif v is not None and _norm(got.get(k)) != _norm(v):
                p.append(f"item {i} {k}: {got.get(k)!r} != {v!r}")
        details = [f.value for f in item.facts if f.key == "detail"]
        for must in want.get("details", []):
            if not any(must in d for d in details):
                p.append(f"item {i}: no detail with {must!r} (got {details})")
        if "max_questions" in want and len(res.questions) > want["max_questions"]:
            p.append(f"questions {res.questions} (max {want['max_questions']})")
        if "photos" in want and item.photo_indexes != want["photos"]:
            p.append(f"item {i} photos {item.photo_indexes} != {want['photos']}")
        if "lifestyle" in want and item.lifestyle_requested != want["lifestyle"]:
            p.append(f"item {i} lifestyle_requested {item.lifestyle_requested}")
        if "printed_text" in want and item.printed_text != want["printed_text"]:
            p.append(f"item {i} printed_text {item.printed_text} != {want['printed_text']}")
    return p


def _p007(case: dict[str, Any]) -> dict[str, Any]:
    exp = case["expect"]
    per = [{"fields": f} for f in exp.get("fields", [])]
    out: dict[str, Any] = {"items": exp["items"], "per_item": per}
    if exp.get("questions"):
        out["questions"] = True
    return {"case": f"P0-07 {case['case']}", "text": case["text"], "attachments": [], "expect": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    store = store_dir()
    samples = store / "fixtures" / "samples"
    cases = yaml.safe_load((samples / "p1-05-extract-v2.yaml").read_text(encoding="utf-8"))["cases"]
    cases += yaml.safe_load((samples / "p1-08-extract-v3.yaml").read_text(encoding="utf-8"))[
        "cases"
    ]
    v4 = samples / "p1-10-extract-v4.yaml"
    if v4.exists():
        cases += yaml.safe_load(v4.read_text(encoding="utf-8"))["cases"]
    old = yaml.safe_load((samples / "p0-07-emails.yaml").read_text(encoding="utf-8"))["emails"]
    cases += [_p007(c) for c in old]
    required: list[str] = require(load_playbook(), "listing.required")

    ledger_path = args.out / "fixtures" / "ledger" / "p0-anthropic.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    llm = AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    before = ledger.month_spend()
    lines = [
        f"# P1-08 extract_request v3 eval - {stamp}",
        "",
        f"model: {cfg.llm.models.extract}",
        "",
    ]
    passed = 0
    for n, case in enumerate(cases, 1):
        atts = []
        for rel in case.get("attachments", []):
            data = (samples / rel).read_bytes()
            atts.append(
                Attachment(
                    filename=Path(rel).name,
                    mime_type=_MIME[Path(rel).suffix.lower()],
                    size_bytes=len(data),
                    data=data,
                )
            )
        msg = ChannelMessage(
            channel="email",
            message_id=f"eval-{n}",
            thread_id=f"eval-{n}",
            sender="eval@example.com",
            authenticated=True,
            received_at=datetime.now(UTC),
            subject=None,
            text=case["text"],
            attachments=atts,
        )
        res = parse_request(
            msg, llm=llm, intake=cfg.intake, required=required, job_id=f"p1-08-eval-{n}"
        )
        problems = _check(res, case["expect"], case["text"])
        passed += not problems
        lines.append(
            f"- {'PASS' if not problems else 'FAIL'} {case['case']}: {'; '.join(problems)}"
        )
        for item in res.items:
            facts = ", ".join(f"{f.key}={f.value}" for f in item.facts)
            lines.append(
                f"  - item {item.index}: {facts}; photos {item.photo_indexes}; "
                f"printed_text {item.printed_text}; missing {item.missing_required}"
            )
        if res.questions:
            lines.append(f"  - questions: {' | '.join(res.questions)}")
    lines += ["", f"**{passed}/{len(cases)} passed; cost {ledger.month_spend() - before:.4f} USD**"]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-05-extract-v2.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
