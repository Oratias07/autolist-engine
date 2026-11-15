"""Choosing a product's categories: read the store's list, then content.choose_categories.

Read-only on the store (`GET /categories`). The owner's words come from the latest
``category_hint`` fact (first email or a reply).
"""

from __future__ import annotations

from autolist.content.categories import choose_categories, parse_store_categories
from autolist.core.models import CategoryChoice, Job
from autolist.orchestrator.runtime import Runtime


async def choose_for(rt: Runtime, job: Job) -> CategoryChoice:
    async with rt.istores() as client:
        categories = parse_store_categories(await client.get_categories())
    hint = job.fact("category_hint")
    return choose_categories(
        job.facts,
        owner_hint=hint.value if hint else None,
        categories=categories,
        llm=rt.llm,
        playbook=rt.playbook,
        job_id=job.job_id,
    )
