"""P0-06 live test: one hidden test product -> read back -> experiments -> delete.

Writes only to a product this script creates (registry guard), never unhides it, and
always attempts the delete, even after an error. Results go to OUT (private store repo).

Steps
1. POST with documented fields only: hidden=1, no category, price 1, quantity 0.
2. GET it back: shape, image host (copied to the iStores CDN?), model/sku/keyword filled?
3. PUT experiments on undocumented fields: feed flags off, Google category, upsale.
   GET again to see which ones were stored.
4. Storefront: direct product URL and a search request (both recorded as observed).
5. DELETE, then GET again and record what the API answers.

Usage: python -I scripts/spike/hidden_product.py --out <store-repo-dir> --image-url <public url>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.publish.errors import StoreAPIError
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import JsonFileRegistry

NAME = "[TEST autolist] נא לא לגעת"
SKU = "AUTOLIST-TEST-P0-06"
UPSALE_PROBE = 996403  # an existing product id from the P0-05 field map (reference only)
EXPERIMENT = {
    # hidden=1 is re-sent in case PUT replaces the whole record instead of patching it
    "hidden": 1,
    "fb_feed": 0,
    "google_feed": 0,
    "tiktok_feed": 0,
    "feed_category": "5502",
    "product_upsale": [UPSALE_PROBE],
}


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _product(data: Any) -> dict[str, Any]:
    p = data.get("product") if isinstance(data, dict) else None
    return p if isinstance(p, dict) else {}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--image-url", required=True)
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    token = get_secret(cfg, "istores_token")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw = args.out / "fixtures" / "istores" / f"{stamp}-p0-06"
    registry = JsonFileRegistry(raw / "created_products.json")
    lines: list[str] = [f"# P0-06 hidden test product - {stamp}", ""]

    body = {
        "product_description": {
            "3": {
                "name": NAME,
                "description": "<p>מוצר בדיקה של מערכת אוטומטית. יימחק מיד.</p>",
            }
        },
        "price": 1,
        "quantity": 0,
        "hidden": 1,
        "status": 0,
        "sku": SKU,
        "image": args.image_url,
    }
    _dump(raw / "01_post_body.json", body)

    product_id: int | None = None
    async with IStoresClient(cfg, token, registry) as client:
        try:
            product_id = await client.create_product(body, job_id="P0-06")
            lines.append(f"- created: product_id {product_id} (hidden=1, no category)")

            got = await client.get_product(product_id)
            _dump(raw / "02_get_after_create.json", got)
            p = _product(got)
            image = str(p.get("image", ""))
            lines += [
                f"- read back: hidden={p.get('hidden')} status={p.get('status')} "
                f"price={p.get('price')} quantity={p.get('quantity')}",
                f"- image stored as: `{image}`",
                f"- image copied to iStores CDN: {image.startswith('https://cdn.istores.co.il/')}",
                f"- sku: `{p.get('sku')}` model: `{p.get('model')}` keyword: `{p.get('keyword')}`",
                f"- defaults: fb_feed={p.get('fb_feed')} google_feed={p.get('google_feed')} "
                f"tiktok_feed={p.get('tiktok_feed')} zap={p.get('zap')} "
                f"feed_category={p.get('feed_category')}",
                f"- product_url: `{p.get('product_url')}`",
            ]

            try:
                await client.update_product(product_id, EXPERIMENT)
                after = await client.get_product(product_id)
                _dump(raw / "03_get_after_put.json", after)
                q = _product(after)
                lines.append("- PUT experiment (undocumented fields):")
                for key, sent in EXPERIMENT.items():
                    lines.append(f"  - `{key}`: sent {sent!r} -> stored {q.get(key)!r}")
                lines.append(f"  - hidden after PUT: {q.get('hidden')}")
            except StoreAPIError as exc:
                lines.append(f"- PUT experiment rejected: {exc.status} {exc.body_excerpt[:300]}")

            product_url = str(p.get("product_url", "")).split("?", 1)[0]
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as web:
                if product_url:
                    r = await web.get(product_url)
                    lines.append(
                        f"- storefront direct URL: HTTP {r.status_code}, "
                        f"name on page: {NAME in r.text}"
                    )
                search = (
                    f"https://{cfg.store.domain}/index.php?route=product/search&search="
                    f"{quote('TEST autolist')}"
                )
                r = await web.get(search)
                lines.append(
                    f"- storefront search attempt `{search}`: HTTP {r.status_code}, "
                    f"name on page: {NAME in r.text} (route is OpenCart's default; "
                    "unconfirmed for this theme)"
                )
        except Exception as exc:
            lines.append(f"- ERROR: {type(exc).__name__}: {str(exc)[:300]}")
        finally:
            if product_id is not None and registry.is_active(product_id):
                try:
                    await client.delete_product(product_id)
                    lines.append(f"- deleted product {product_id}")
                except Exception as exc:
                    lines.append(f"- DELETE FAILED: {type(exc).__name__}: {str(exc)[:300]}")
                try:
                    gone = await client.get_product(product_id)
                    _dump(raw / "04_get_after_delete.json", gone)
                    lines.append(f"- GET after delete: 200 with keys {sorted(gone)}")
                except StoreAPIError as exc:
                    lines.append(f"- GET after delete: {exc.status} {exc.body_excerpt[:200]}")

    lines.append(f"- registry: `{registry.get(product_id) if product_id else None}`")
    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-06-hidden-product.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
