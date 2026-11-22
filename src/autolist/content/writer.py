"""Write the title and description of one product from its facts (P0-10 prompts v2, Sonnet).

The model returns structured JSON; the description HTML is built here from the playbook's
section list (every model string escaped), then sanitized. Checks from the P0-10 bake-off:
every cited fact id exists, every number in the title or a spec value appears in a cited
fact, every number in the headline, intro or a benefit appears in some fact,
no exclamation marks or emoji, fewer than three benefits only with an open point.
A failed check is reported in ``problems`` and blocks the job (the orchestrator decides).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autolist.content.sanitizer import sanitize
from autolist.core.facts import DETAIL
from autolist.core.models import JobFact, ListingText
from autolist.llm.client import LLMClient, LLMRequest

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
# facts the writer sees, plus every owner detail; price, quantity, category and the item code
# are never part of the text
WRITER_KEYS = ("name", "model", "barcode", "keywords")
TITLE_TOKENS = 1000
DESCRIPTION_TOKENS = 6000


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


def writer_facts(facts: list[JobFact]) -> list[dict[str, str]]:
    """Latest fact per key, then every owner detail in order (once each), numbered F1.."""
    latest: dict[str, JobFact] = {f.key: f for f in facts}
    if "model" not in latest and "item_code" in latest:
        # the item code fills the store's model field (DEV 15.11.2026), so the text uses it too
        latest["model"] = latest["item_code"]
    texts = [f"{key}: {latest[key].value}" for key in WRITER_KEYS if key in latest]
    details = list(dict.fromkeys(f.value for f in facts if f.key == DETAIL))
    texts += [f"detail: {d}" for d in details]
    return [{"id": f"F{i}", "text": t} for i, t in enumerate(texts, 1)]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(text)}


def check(facts: dict[str, str], title: TitleOut, desc: DescriptionOut) -> list[str]:
    out: list[str] = []
    cited = [*title.fact_ids]
    cited += [i for r in desc.specs for i in r.fact_ids]
    cited += [i for b in desc.benefits for i in b.fact_ids]
    out += [f"unknown fact id {i}" for i in sorted(set(cited) - set(facts))]

    def unsupported(text: str, ids: list[str]) -> set[str]:
        return _numbers(text) - _numbers(" ".join(facts.get(i, "") for i in ids))

    if bad := unsupported(title.title, title.fact_ids):
        out.append(f"title numbers not in cited facts: {sorted(bad)}")
    for r in desc.specs:
        if bad := unsupported(r.value, r.fact_ids):
            out.append(f"spec '{r.label}' numbers not in cited facts: {sorted(bad)}")
    free = [desc.headline, *desc.intro, *(b.sentence for b in desc.benefits)]
    if bad := _numbers(" ".join(free)) - _numbers(" ".join(facts.values())):
        out.append(f"numbers in free text not in any fact: {sorted(bad)}")
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


def render(desc: DescriptionOut, sections: list[dict[str, Any]]) -> tuple[str, str]:
    """(HTML, plain text) from the playbook's section list."""
    e = html.escape
    parts: list[str] = []
    lines: list[str] = []
    for sec in sections:
        sid = sec["id"]
        if sid == "headline":
            parts.append(str(sec["render"]).replace("{headline}", e(desc.headline)))
            lines.append(desc.headline)
        elif sid == "intro":
            parts.append(f"<p>{e(' '.join(desc.intro))}</p>")
            lines.append(" ".join(desc.intro))
        elif sid == "specs":
            items = "".join(f"<li>{e(r.label)}: {e(r.value)}</li>" for r in desc.specs)
            parts.append(f"<p><strong>{e(sec['heading'])}</strong></p><ul>{items}</ul>")
            lines += [sec["heading"], *(f"- {r.label}: {r.value}" for r in desc.specs)]
        elif sid == "benefits":
            if not desc.benefits:  # no heading over an empty list (DEV 22.11.2026, stage A)
                continue
            items = "".join(
                f"<li><strong>{e(b.title)}:</strong> {e(b.sentence.rstrip('.'))}.</li>"
                for b in desc.benefits
            )
            parts.append(f"<p><strong>{e(sec['heading'])}</strong></p><ul>{items}</ul>")
            lines += [
                sec["heading"],
                *(f"- {b.title}: {b.sentence.rstrip('.')}." for b in desc.benefits),
            ]
        else:
            raise ValueError(f"unknown description section in playbook: {sid}")
    return sanitize("\n".join(parts)), "\n".join(lines)


def write_listing(
    facts: list[JobFact],
    *,
    llm: LLMClient,
    playbook: dict[str, Any],
    glossary: dict[str, Any],
    voice: str,
    job_id: str,
) -> ListingText:
    numbered = writer_facts(facts)
    by_id = {f["id"]: f["text"] for f in numbered}
    facts_json = json.dumps(numbered, ensure_ascii=False, indent=1)
    glossary_text = yaml.safe_dump(glossary["terms"], allow_unicode=True, sort_keys=False)
    t = llm.complete(
        LLMRequest(
            prompt="write_title",
            variables={
                "facts_json": facts_json,
                "title_rules": yaml.safe_dump(playbook["title"], allow_unicode=True),
                "glossary": glossary_text,
            },
            output_schema=TitleOut,
            model_role="write",
            job_id=job_id,
            max_output_tokens=TITLE_TOKENS,
        )
    )
    d = llm.complete(
        LLMRequest(
            prompt="write_description",
            variables={
                "facts_json": facts_json,
                "description_rules": yaml.safe_dump(playbook["description"], allow_unicode=True),
                "glossary": glossary_text,
                "voice": voice,
            },
            output_schema=DescriptionOut,
            model_role="write",
            job_id=job_id,
            max_output_tokens=DESCRIPTION_TOKENS,
        )
    )
    if not isinstance(t.parsed, TitleOut) or not isinstance(d.parsed, DescriptionOut):
        raise TypeError("writer prompts returned no parsed result")
    body_html, body_text = render(d.parsed, playbook["description"]["sections"])
    open_points = list(dict.fromkeys([*t.parsed.open_points, *d.parsed.open_points]))
    return ListingText(
        title=t.parsed.title.strip(),
        description_html=body_html,
        description_text=body_text,
        open_points=open_points,
        problems=check(by_id, t.parsed, d.parsed),
    )
