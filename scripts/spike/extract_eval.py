"""P0-07 live smoke + extraction comparison (thin CLI over llm.client).

Runs agent/prompts/extract_request.md on the sample emails in
$AUTOLIST_STORE_DIR/fixtures/samples/p0-07-emails.yaml with every model in
store.yaml llm.bakeoff_candidates. Checks:
  - schema-valid JSON (the llm client already enforces one repair attempt),
  - every non-null value carries a quote that appears verbatim in the email,
  - expected values per sample (written with the samples).
Writes a markdown report + raw JSON to OUT. Costs go through the cost ledger.

The extraction schema lives here for the spike; it moves to core/models.py with the
intake module (phase 3), through a SPEC change.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.llm.client import AnthropicClient, LLMRequest
from autolist.llm.errors import LLMOutputInvalid, LLMUnavailable


class Quoted[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: T | None = Field(description="null when the email does not state it explicitly")
    quote: str | None = Field(description="exact substring of the email that states the value")


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Quoted[str]
    price: Quoted[Decimal]
    sale_price: Quoted[Decimal]
    quantity: Quoted[int]
    barcode: Quoted[str]
    model: Quoted[str]
    keywords: Quoted[list[str]]
    category_hint: Quoted[str]


class ExtractedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedItem]
    questions: list[str] = Field(description="questions for the owner, in Hebrew")


def _quote_problems(item: ExtractedItem, email: str) -> list[str]:
    problems = []
    for field, q in item:
        if q.value is not None and (not q.quote or q.quote not in email):
            problems.append(f"{field}: value without a verbatim quote")
    return problems


def _norm(value: Any) -> Any:
    """None stays None, numbers compare as floats, everything else as text."""
    if value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return float(value)
    return str(value)


def _expect_problems(result: ExtractedRequest, expect: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "items" in expect and len(result.items) != expect["items"]:
        problems.append(f"items: expected {expect['items']}, got {len(result.items)}")
    for i, fields in enumerate(expect.get("fields", [])):
        if i >= len(result.items):
            break
        for key, want in fields.items():
            got = getattr(result.items[i], key).value
            if _norm(got) != _norm(want):
                problems.append(f"item {i + 1} {key}: expected {want!r}, got {got!r}")
    if expect.get("questions") and not result.questions:
        problems.append("expected at least one question")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()

    base = load_store_config()
    candidates: list[str] = base.require("llm.bakeoff_candidates")
    samples = yaml.safe_load(
        (store_dir() / "fixtures" / "samples" / "p0-07-emails.yaml").read_text(encoding="utf-8")
    )["emails"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = args.out / "fixtures" / "llm" / f"{stamp}-p0-07"
    ledger = CostLedger(
        base.costs, base.store.timezone, JsonFileLedgerStore(raw_dir / "ledger.json")
    )
    api_key = get_secret(base, "anthropic_api_key")

    report = [f"# P0-07 extraction run - {stamp}", "", f"Models: {', '.join(candidates)}", ""]
    totals: dict[str, dict[str, Any]] = {}
    for model in candidates:
        data = base.model_dump()
        data["llm"]["models"]["extract"] = model
        cfg = type(base).model_validate(data)
        client = AnthropicClient(cfg, api_key, ledger)
        ok = 0
        usd = Decimal(0)
        report += [
            f"## {model}",
            "",
            "| # | case | result | problems |",
            "| --- | --- | --- | --- |",
        ]
        for n, sample in enumerate(samples, start=1):
            req = LLMRequest(
                prompt="extract_request",
                variables={"email_text": sample["text"]},
                output_schema=ExtractedRequest,
                model_role="extract",
                job_id=f"P0-07-{model}-{n}",
                max_output_tokens=1500,
            )
            try:
                res = client.complete(req)
            except (LLMOutputInvalid, LLMUnavailable) as exc:
                report.append(f"| {n} | {sample['case']} | ERROR | {type(exc).__name__} |")
                continue
            usd += res.usd
            parsed = res.parsed
            if not isinstance(parsed, ExtractedRequest):
                report.append(f"| {n} | {sample['case']} | ERROR | no parsed result |")
                continue
            problems = [p for it in parsed.items for p in _quote_problems(it, sample["text"])]
            problems += _expect_problems(parsed, sample.get("expect", {}))
            ok += not problems
            (raw_dir / model).mkdir(parents=True, exist_ok=True)
            (raw_dir / model / f"{n:02d}.json").write_text(
                json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            verdict = "pass" if not problems else "FAIL"
            report.append(
                f"| {n} | {sample['case']} | {verdict} ({res.input_tokens}/{res.output_tokens} tok)"
                f" | {'; '.join(problems) or '-'} |"
            )
        totals[model] = {"passed": ok, "of": len(samples), "usd": usd}
        report.append("")
    report += ["## Summary", "", "| model | passed | cost USD |", "| --- | --- | --- |"]
    report += [f"| {m} | {t['passed']}/{t['of']} | {t['usd']:.4f} |" for m, t in totals.items()]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-07-extract.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
