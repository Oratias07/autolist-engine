"""Choose the store categories of one product (choose_category v1, DEV 15.11.2026). Pure but
for the LLM call.

The model proposes; the code decides what is allowed:
- only ids in the store's list, active and not hidden;
- never a category in ``categories.never_auto`` (promotional ones the owner sets himself);
- at most ``categories.max_per_product``;
- "unclear" means no category at all (the owner sets it, or names one in his reply).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autolist.core.models import CategoryChoice, JobFact, StoreCategory
from autolist.core.playbook import require
from autolist.llm.client import LLMClient, LLMRequest

MAX_OUTPUT_TOKENS = 1500  # 400 truncated real answers (eval run 43762137569)
FACT_KEYS = ("name", "keywords", "detail")


class ChosenCategories(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: int | None = Field(description="a category id from the list, or null")
    secondary: int | None = Field(description="a second category id from the list, or null")
    clear: bool
    question: str = Field(description="one short Hebrew question when not clear, else empty")


def allowed_categories(
    categories: list[StoreCategory], playbook: dict[str, Any]
) -> list[StoreCategory]:
    never: list[int] = require(playbook, "categories.never_auto")
    return [c for c in categories if c.status == 1 and c.hidden == 0 and c.category_id not in never]


def choose_categories(
    facts: list[JobFact],
    *,
    owner_hint: str | None,
    categories: list[StoreCategory],
    llm: LLMClient,
    playbook: dict[str, Any],
    job_id: str,
) -> CategoryChoice:
    allowed = allowed_categories(categories, playbook)
    by_id = {c.category_id: c for c in allowed}
    limit: int = require(playbook, "categories.max_per_product")
    lines = [f"{f.key}: {f.value}" for f in facts if f.key in FACT_KEYS]
    result = llm.complete(
        LLMRequest(
            prompt="choose_category",
            variables={
                "product_facts": "\n".join(lines),
                "owner_category": owner_hint or "",
                "categories": "\n".join(f"{c.category_id}: {c.name}" for c in allowed),
            },
            output_schema=ChosenCategories,
            model_role="classify",
            job_id=job_id,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    )
    out = result.parsed
    if not isinstance(out, ChosenCategories):
        raise TypeError("choose_category returned no parsed result")
    if not out.clear:
        return CategoryChoice(category_ids=[], names=[], question=out.question.strip())
    ids = [i for i in (out.primary, out.secondary) if i is not None and i in by_id]
    ids = list(dict.fromkeys(ids))[:limit]
    return CategoryChoice(category_ids=ids, names=[by_id[i].name for i in ids], question="")


def parse_store_categories(payload: Any) -> list[StoreCategory]:
    """`GET /categories` → the list (shape observed in P0-05: {"response": [...]})."""
    items = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("categories response has no list")
    return [
        StoreCategory(
            category_id=int(c["category_id"]),
            name=" ".join(str(c["name"]).split()),
            status=int(c["status"]),
            hidden=int(c["hidden"]),
        )
        for c in items
        if isinstance(c, dict)
    ]
