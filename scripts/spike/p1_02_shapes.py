"""P1-02 live run: observe how iStores stores a category, an extra image and a sale price.

STORE WRITE, approved by DEV for this exact run (15.11.2026). Runs only through the store
repo's `live` workflow. Uses the documented shapes (apidocs.istores.co.il/openapi.json,
ProductAddRequest / ProductEditRequest), read 15.11.2026:
  product_category: [category ids]
  images: [{"image": url, "sort_order": n}]
  product_special: [{"price": n > 0, "date_start": "Y-m-d", "date_end": "Y-m-d"}]

Product A: a documented create (as the service does), then ONE field per PUT, each followed by
a GET, so every result is attributable (CLAUDE.md: experiments in separate requests).
Product B: one create with all three fields in the body, then a GET.
Both are test products, hidden and disabled, priced 1. Both are deleted in `finally`, always,
and a GET then confirms 404. Nothing else in the store is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from autolist.core.blob_store import GcsBlobStore
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.logging import configure_logging, register_secret
from autolist.publish.errors import StoreAPIError
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import JsonFileRegistry

NAME = "[TEST autolist] נא לא לגעת"
CATEGORY = 97008  # "ציוד משרדי", observed in P0-05
IMAGES = ("tape-001/store-main.jpg", "invoice-WF26352/images/H2201.jpeg")
WATCHED = ("hidden", "status", "price", "category", "images", "special", "image")


def _p(data: Any) -> dict[str, Any]:
    p = data.get("product") if isinstance(data, dict) else None
    return p if isinstance(p, dict) else {}


def _base(sku: str, main_url: str) -> dict[str, Any]:
    return {
        "product_description": {"3": {"name": NAME, "description": "<p>מוצר בדיקה. יימחק.</p>"}},
        "price": 1,
        "quantity": 0,
        "subtract": 1,
        "hidden": 1,
        "status": 0,
        "sku": sku,
        "model": sku,
        "image": main_url,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    samples = store_dir() / "fixtures" / "samples"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = args.out / "fixtures" / "istores" / f"{stamp}-p1-02"
    raw_dir.mkdir(parents=True, exist_ok=True)
    registry = JsonFileRegistry(raw_dir / "created_products.json")
    blobs = GcsBlobStore(cfg)
    refs = [
        blobs.put(f"p1-02/{stamp}/{i}.jpg", (samples / rel).read_bytes(), "image/jpeg")
        for i, rel in enumerate(IMAGES)
    ]
    urls = [blobs.signed_url(r, cfg.publishing.image_url_ttl_s) for r in refs]
    for u in urls:
        register_secret(u)
    today = date.today()
    special = [
        {
            "price": 0.5,
            "date_start": today.isoformat(),
            "date_end": (today + timedelta(7)).isoformat(),
        }
    ]
    extra = [{"image": urls[1], "sort_order": 1}]
    lines = [f"# P1-02 write shapes - {stamp}", ""]
    created: list[int] = []

    def record(label: str, data: Any) -> None:
        (raw_dir / f"{label}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        p = _p(data)
        lines.append(f"- {label}: " + "; ".join(f"{k}={p.get(k)!r}" for k in WATCHED))

    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        try:
            a = await client.create_product(_base("AUTOLIST-TEST-P1-02-A", urls[0]), job_id=None)
            created.append(a)
            lines += [f"## product A {a}: documented create, then one field per PUT", ""]
            record("A-0-created", await client.get_product(a))
            for label, body in (
                ("A-1-category", {"product_category": [CATEGORY]}),
                ("A-2-images", {"images": extra}),
                ("A-3-special", {"product_special": special}),
            ):
                try:
                    put = await client.update_product(a, body)
                    lines.append(
                        f"- {label} PUT answer: {json.dumps(put, ensure_ascii=False)[:300]}"
                    )
                except StoreAPIError as exc:
                    lines.append(f"- {label} PUT refused: {exc}")
                record(label, await client.get_product(a))
            body_b = {
                **_base("AUTOLIST-TEST-P1-02-B", urls[0]),
                "product_category": [CATEGORY],
                "images": extra,
                "product_special": special,
            }
            b = await client.create_product(body_b, job_id=None)
            created.append(b)
            lines += ["", f"## product B {b}: all three fields in the create body", ""]
            record("B-0-created", await client.get_product(b))
        finally:
            lines += ["", "## cleanup", ""]
            for pid in created:
                try:
                    await client.delete_product(pid)
                    try:
                        await client.get_product(pid)
                        lines.append(f"- {pid}: STILL THERE after delete")
                    except StoreAPIError as exc:
                        lines.append(f"- {pid}: deleted ({exc})")
                except StoreAPIError as exc:
                    lines.append(f"- {pid}: DELETE FAILED {exc}")
            for r in refs:
                blobs.delete(r)
            report = args.out / "docs" / "live-runs" / f"{stamp}-p1-02-shapes.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
