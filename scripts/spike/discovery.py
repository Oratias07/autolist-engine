"""P0-05 live read-only discovery (thin CLI over real modules).

Read-only: languages, stock statuses, currencies, dimensions, categories, the full product
catalog, and one known product. Measures pixel sizes of a sample of catalog images on the
CDN. Writes raw JSON and a markdown report into OUT (the private store repo). Never writes
to the store. Never prints secrets.

Usage: python -I scripts/spike/discovery.py --out <store-repo-dir> --find "<product name>"
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import InMemoryRegistry

MAX_PAGES = 50
IMAGE_SAMPLE = 20
CDN_URL = re.compile(r"https://cdn\.istores\.co\.il/[^\s\"'<>]+\.(?:jpe?g|png|webp)", re.I)


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _fill_stats(products: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """field -> (filled, total). Empty = None, "", [], {}, 0-string."""
    filled: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    for p in products:
        for k, v in p.items():
            seen[k] += 1
            if v not in (None, "", [], {}, "0", "0.0000"):
                filled[k] += 1
    return {k: (filled[k], seen[k]) for k in sorted(seen)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--find", required=True, help="exact product name to look up")
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    token = get_secret(cfg, "istores_token")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = args.out / "fixtures" / "istores" / stamp
    notes: list[str] = []

    async with IStoresClient(cfg, token, InMemoryRegistry()) as client:
        lookups = {
            "language_list": client.get_languages,
            "stock_status_list": client.get_stock_statuses,
            "currency_list": client.get_currencies,
            "dimension_list": client.get_dimensions,
            "categories": client.get_categories,
        }
        for name, call in lookups.items():
            data = await call()
            _dump(raw_dir / f"{name}.json", data)
            notes.append(f"- `{name}`: 200, top-level keys {sorted(data)}")

        products: list[dict[str, Any]] = []
        first_page: Any = None
        for page in range(1, MAX_PAGES + 1):
            data = await client.list_products(200, page)
            if page == 1:
                first_page = data
                _dump(raw_dir / "products_page1.json", data)
            batch = data.get("Products") if isinstance(data, dict) else None
            if not isinstance(batch, list) or not batch:
                notes.append(f"- products: stopped at page {page} (keys {sorted(data)})")
                break
            products.extend(p for p in batch if isinstance(p, dict))
            pages = data.get("pages")
            if isinstance(pages, int) and page >= pages:
                break
        _dump(raw_dir / "products_all.json", products)

        found_ids = [
            p.get("product_id") or p.get("id")
            for p in products
            if args.find in json.dumps(p, ensure_ascii=False)
        ]
        found_detail: Any = None
        if found_ids and found_ids[0] is not None:
            found_detail = await client.get_product(int(found_ids[0]))
            _dump(raw_dir / "found_product.json", found_detail)

    urls: list[str] = []
    for p in products:
        for u in CDN_URL.findall(json.dumps(p, ensure_ascii=False)):
            if u not in urls:
                urls.append(u)
    sizes: list[str] = []
    async with httpx.AsyncClient(timeout=30) as http:
        for url in urls[:IMAGE_SAMPLE]:
            try:
                r = await http.get(url)
                w, h = Image.open(io.BytesIO(r.content)).size
                sizes.append(
                    f"| {url.rsplit('/', 1)[-1]} | {w}x{h} | {len(r.content) // 1024} KB |"
                )
            except Exception as exc:
                sizes.append(f"| {url.rsplit('/', 1)[-1]} | error: {type(exc).__name__} | - |")

    stats = _fill_stats(products)
    with_sku = sum(1 for p in products if str(p.get("sku") or "").strip())
    with_upc = sum(1 for p in products if str(p.get("upc") or "").strip())
    report = [
        f"# P0-05 discovery - {stamp}",
        "",
        f"Read-only. Raw responses: `fixtures/istores/{stamp}/`.",
        "",
        "## Lookups",
        *notes,
        "",
        "## Catalog",
        f"- products read: {len(products)}",
        f"- page 1 top-level keys: {sorted(first_page) if isinstance(first_page, dict) else '-'}",
        f"- with sku: {with_sku}; with upc: {with_upc}",
        "",
        "| field | filled | of |",
        "| --- | --- | --- |",
        *[f"| `{k}` | {f} | {t} |" for k, (f, t) in stats.items()],
        "",
        "## Known product",
        f"- search: `{args.find}` -> ids {found_ids[:5]}",
        f"- detail saved: {'yes' if found_detail is not None else 'no'}",
        "",
        f"## Image sizes (first {IMAGE_SAMPLE} CDN images)",
        "| file | pixels | size |",
        "| --- | --- | --- |",
        *sizes,
    ]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-05-discovery.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
