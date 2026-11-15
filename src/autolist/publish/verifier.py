"""Read a created product back and compare it to the draft. Read-only."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from autolist.core.models import ProductDraft
from autolist.publish.istores_client import _is_falsy_flag, _is_truthy_flag

CDN_PREFIX = "https://cdn.istores.co.il/"


def _product(payload: Any) -> Mapping[str, Any]:
    inner = payload.get("product", payload) if isinstance(payload, Mapping) else None
    return inner if isinstance(inner, Mapping) else {}


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def created_problems(payload: Any, draft: ProductDraft, language_id: int) -> list[str]:
    """Every difference between the store's product and the draft. Empty means verified."""
    p = _product(payload)
    if not p:
        return ["product not readable"]
    problems: list[str] = []
    descriptions = p.get("product_description")
    lang = descriptions.get(str(language_id)) if isinstance(descriptions, Mapping) else None
    name = lang.get("name") if isinstance(lang, Mapping) else None
    if name != draft.name:
        problems.append("name differs")
    if _dec(p.get("price")) != draft.price:
        problems.append(f"price is {p.get('price')!r}")
    if _dec(p.get("quantity")) != Decimal(draft.quantity):
        problems.append(f"quantity is {p.get('quantity')!r}")
    if _dec(p.get("subtract")) != Decimal(1 if draft.stock_managed else 0):
        problems.append(f"subtract is {p.get('subtract')!r}")
    checks = [("model", draft.model)] + ([("sku", draft.sku)] if draft.sku is not None else [])
    for field, want in checks:
        if str(p.get(field) or "") != want:
            problems.append(f"{field} is {p.get(field)!r}")
    got_categories = p.get("category")
    got_ids = {str(k) for k in got_categories} if isinstance(got_categories, Mapping) else set()
    if {str(i) for i in draft.category_ids} - got_ids:
        problems.append(f"categories are {sorted(got_ids)}")
    problems += _extras_problems(p, draft)
    if not _is_truthy_flag(p.get("hidden")):
        problems.append("NOT hidden")
    if not _is_falsy_flag(p.get("status")):
        problems.append("NOT disabled")
    if not str(p.get("image") or "").startswith(CDN_PREFIX):
        problems.append("image not on the store CDN")
    return problems


def _extras_problems(p: Mapping[str, Any], draft: ProductDraft) -> list[str]:
    """Extra images and the sale, in the read shapes observed in P1-02."""
    problems: list[str] = []
    if draft.extra_images:
        images = p.get("images")
        items = list(images.values()) if isinstance(images, Mapping) else []
        on_cdn = [
            i
            for i in items
            if isinstance(i, Mapping) and str(i.get("image") or "").startswith(CDN_PREFIX)
        ]
        if len(on_cdn) < len(draft.extra_images):
            problems.append(f"extra images: {len(on_cdn)} on the store CDN")
    if draft.special is not None:
        want = draft.special
        specials = p.get("special")
        found = [
            s
            for s in (specials if isinstance(specials, list) else [])
            if isinstance(s, Mapping)
            and _dec(s.get("price")) == want.price
            and s.get("date_start") == want.date_start.isoformat()
            and s.get("date_end") == want.date_end.isoformat()
        ]
        if not found:
            problems.append(f"sale is {specials!r}")
    return problems
