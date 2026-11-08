"""P0-10 text bake-off (thin CLI over llm.client).

For each product in the store repo's fixtures/samples/p0-10-bakeoff.yaml and each model in
llm.bakeoff_candidates, runs write_title and write_description, then checks the output:
  - schema-valid JSON (the llm client enforces one repair attempt),
  - every cited fact id exists,
  - every number in a title or spec value appears in one of its cited facts,
  - voice: no exclamation marks, no emoji.
Renders the description HTML in code from playbook.yaml description.sections (the model never
writes HTML). Writes JSON, HTML and a report to OUT. Costs go through the cost ledger.

The output schemas live here for the spike; they move to core/models.py with the content module
(phase 5), through a SPEC change.
"""

from __future__ import annotations

import argparse
import html
import json
import re
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

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TitleOut(_M):
    title: str
    fact_ids: list[str]
    open_points: list[str]


class SpecRow(_M):
    label: str
    value: str
    fact_ids: list[str]


class Benefit(_M):
    title: str
    sentence: str
    fact_ids: list[str]


class DescriptionOut(_M):
    headline: str
    intro: list[str] = Field(min_length=2, max_length=2)
    specs: list[SpecRow]
    benefits: list[Benefit] = Field(max_length=3)
    open_points: list[str]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(text)}


def _problems(facts: dict[str, str], title: TitleOut, desc: DescriptionOut) -> list[str]:
    out: list[str] = []
    cited = [*title.fact_ids]
    cited += [i for r in desc.specs for i in r.fact_ids]
    cited += [i for b in desc.benefits for i in b.fact_ids]
    out += [f"unknown fact id {i}" for i in sorted(set(cited) - set(facts))]

    def unsupported(text: str, ids: list[str]) -> set[str]:
        source = " ".join(facts.get(i, "") for i in ids)
        return _numbers(text) - _numbers(source)

    if bad := unsupported(title.title, title.fact_ids):
        out.append(f"title numbers not in cited facts: {sorted(bad)}")
    for r in desc.specs:
        if bad := unsupported(r.value, r.fact_ids):
            out.append(f"spec '{r.label}' numbers not in cited facts: {sorted(bad)}")
    texts = [title.title, desc.headline, *desc.intro]
    texts += [x for r in desc.specs for x in (r.label, r.value)]
    texts += [x for b in desc.benefits for x in (b.title, b.sentence)]
    joined = " ".join(texts)
    if "!" in joined:
        out.append("exclamation mark")
    if _EMOJI.search(joined):
        out.append("emoji")
    if len(desc.benefits) < 3 and not desc.open_points:
        out.append("fewer than 3 benefits without an open point")
    return out


def _render(desc: DescriptionOut, sections: list[dict[str, Any]]) -> str:
    """HTML from the playbook's section list; every model string is escaped."""
    e = html.escape
    parts: list[str] = []
    for sec in sections:
        sid = sec["id"]
        if sid == "headline":
            parts.append(str(sec["render"]).replace("{headline}", e(desc.headline)))
        elif sid == "intro":
            parts.append(f"<p>{e(' '.join(desc.intro))}</p>")
        elif sid == "specs":
            items = "".join(f"<li>{e(r.label)}: {e(r.value)}</li>" for r in desc.specs)
            parts.append(f"<p><strong>{e(sec['heading'])}</strong></p><ul>{items}</ul>")
        elif sid == "benefits":
            items = "".join(
                f"<li><strong>{e(b.title)}:</strong> {e(b.sentence.rstrip('.'))}.</li>"
                for b in desc.benefits
            )
            parts.append(f"<p><strong>{e(sec['heading'])}</strong></p><ul>{items}</ul>")
        else:
            raise SystemExit(f"unknown description section in playbook: {sid}")
    return "\n".join(parts)


def _voice(store: Path) -> str:
    text = (store / "docs" / "voice.md").read_text(encoding="utf-8")
    start = text.index("**עקרונות הקול:**")
    end = text.index("**מילון מונחים.**")
    return text[start:end].strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()

    base = load_store_config()
    store = store_dir()
    products: dict[str, Any] = yaml.safe_load(
        (store / "fixtures" / "samples" / "p0-10-bakeoff.yaml").read_text(encoding="utf-8")
    )["products"]
    playbook = yaml.safe_load((store / "playbook" / "playbook.yaml").read_text(encoding="utf-8"))
    glossary = yaml.safe_load((store / "playbook" / "glossary.yaml").read_text(encoding="utf-8"))
    title_rules = yaml.safe_dump(playbook["title"], allow_unicode=True, sort_keys=False)
    desc_rules = yaml.safe_dump(playbook["description"], allow_unicode=True, sort_keys=False)
    glossary_text = yaml.safe_dump(glossary["terms"], allow_unicode=True, sort_keys=False)
    voice = _voice(store)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ledger = CostLedger(
        base.costs,
        base.store.timezone,
        JsonFileLedgerStore(args.out / "fixtures" / "ledger" / "p0-anthropic.json"),
    )
    api_key = get_secret(base, "anthropic_api_key")
    run_dir = args.out / "fixtures" / "bakeoff" / "p0-10" / f"{stamp}-text"
    report = [f"# P0-10 text bake-off - {stamp}", ""]
    summary: list[str] = []

    candidates: list[str] = base.require("llm.bakeoff_candidates")
    for model in candidates:
        data = base.model_dump()
        data["llm"]["models"]["write"] = model
        cfg = type(base).model_validate(data)
        client = AnthropicClient(cfg, api_key, ledger)
        usd = Decimal(0)
        clean = 0
        report += [f"## {model}", ""]
        for pid, product in products.items():
            facts = {f["id"]: f["text"] for f in product["facts"]}
            facts_json = json.dumps(product["facts"], ensure_ascii=False, indent=1)
            try:
                t = client.complete(
                    LLMRequest(
                        prompt="write_title",
                        variables={
                            "facts_json": facts_json,
                            "title_rules": title_rules,
                            "glossary": glossary_text,
                        },
                        output_schema=TitleOut,
                        model_role="write",
                        job_id=f"p0-10-text-{pid}",
                        max_output_tokens=600,
                    )
                )
                d = client.complete(
                    LLMRequest(
                        prompt="write_description",
                        variables={
                            "facts_json": facts_json,
                            "description_rules": desc_rules,
                            "glossary": glossary_text,
                            "voice": voice,
                        },
                        output_schema=DescriptionOut,
                        model_role="write",
                        job_id=f"p0-10-text-{pid}",
                        max_output_tokens=2000,
                    )
                )
            except (LLMOutputInvalid, LLMUnavailable) as exc:
                report += [f"### {pid}: ERROR {type(exc).__name__}", ""]
                continue
            usd += t.usd + d.usd
            title, desc = t.parsed, d.parsed
            if not isinstance(title, TitleOut) or not isinstance(desc, DescriptionOut):
                report += [f"### {pid}: ERROR no parsed result", ""]
                continue
            problems = _problems(facts, title, desc)
            clean += not problems
            body = _render(desc, playbook["description"]["sections"])
            out = run_dir / model / pid
            out.mkdir(parents=True, exist_ok=True)
            (out / "title.json").write_text(
                json.dumps(title.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            (out / "description.json").write_text(
                json.dumps(desc.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            (out / "description.html").write_text(body + "\n", encoding="utf-8")
            report += [
                f"### {pid}",
                "",
                f"- title: {title.title}",
                f"- checks: {'; '.join(problems) or 'clean'}",
                f"- open points: {' | '.join([*title.open_points, *desc.open_points]) or '-'}",
                f"- tokens: title {t.input_tokens}/{t.output_tokens},"
                f" description {d.input_tokens}/{d.output_tokens}",
                "",
            ]
        summary.append(f"| {model} | {clean}/{len(products)} | {usd:.4f} |")

    report += ["## Summary", "", "| model | clean | cost USD |", "| --- | --- | --- |", *summary]
    path = args.out / "docs" / "live-runs" / f"{stamp}-p0-10-text.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
