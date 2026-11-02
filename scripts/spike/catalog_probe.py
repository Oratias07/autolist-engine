"""P0-06b live probe: can the API turn the catalog flags on? (approved by DEV 02.11.2026)

--mode create
  1. POST a hidden test product (documented fields only, no category).
  2. PUT fb_feed=1, google_feed=1 and feed_category as an integer; GET to see what stuck.
  3. PUT the flags back to 0 (the product must not enter any catalog while it exists).
  4. Keep the product (hidden) for a manual storefront check; registry saved in OUT.
  On any error after the create, the product is deleted immediately.
--mode delete
  Delete every active product in the registry and confirm 404.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.publish.errors import StoreAPIError
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import JsonFileRegistry

NAME = "[TEST autolist] נא לא לגעת"
SKU = "AUTOLIST-TEST-P0-06B"
FLAGS_ON = {"hidden": 1, "fb_feed": 1, "google_feed": 1, "feed_category": 5502}
FLAGS_OFF = {"hidden": 1, "fb_feed": 0, "google_feed": 0, "tiktok_feed": 0}
WATCHED = ("hidden", "status", "fb_feed", "google_feed", "tiktok_feed", "feed_category")


def _p(data: Any) -> dict[str, Any]:
    p = data.get("product") if isinstance(data, dict) else None
    return p if isinstance(p, dict) else {}


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


async def create(
    client: IStoresClient, registry: JsonFileRegistry, raw: Path, image: str
) -> list[str]:
    lines: list[str] = []
    body = {
        "product_description": {"3": {"name": NAME, "description": "<p>מוצר בדיקה. יימחק.</p>"}},
        "price": 1,
        "quantity": 0,
        "hidden": 1,
        "status": 1,
        "sku": SKU,
        "model": SKU,
        "image": image,
    }
    pid = await client.create_product(body, job_id="P0-06b")
    lines.append(f"- created hidden product {pid}")
    try:
        before = _p(await client.get_product(pid))
        await client.update_product(pid, FLAGS_ON)
        on = _p(await client.get_product(pid))
        _dump(raw / "after_flags_on.json", on)
        lines.append("- PUT flags on:")
        for k in WATCHED:
            lines.append(f"  - `{k}`: before {before.get(k)!r} -> after {on.get(k)!r}")
        await client.update_product(pid, FLAGS_OFF)
        off = _p(await client.get_product(pid))
        lines.append(
            f"- flags reset: fb_feed={off.get('fb_feed')} google_feed={off.get('google_feed')} "
            f"tiktok_feed={off.get('tiktok_feed')} hidden={off.get('hidden')}"
        )
        url = str(off.get("product_url", "")).split("?", 1)[0]
        lines += [
            f"- product kept hidden for the manual check: `{url}`",
            "- next: DEV opens the URL in a private window; then run --mode delete",
        ]
    except Exception as exc:
        lines.append(f"- ERROR {type(exc).__name__}: {str(exc)[:300]} -> deleting now")
        await client.delete_product(pid)
        lines.append(f"- deleted {pid}")
    return lines


async def delete_all(
    client: IStoresClient, registry: JsonFileRegistry, ids: list[int]
) -> list[str]:
    lines: list[str] = []
    for pid in ids:
        if not registry.is_active(pid):
            continue
        await client.delete_product(pid)
        try:
            await client.get_product(pid)
            lines.append(f"- deleted {pid}, but GET still answers 200")
        except StoreAPIError as exc:
            lines.append(f"- deleted {pid}; GET -> {exc.status}")
    return lines or ["- nothing active in the registry"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mode", choices=["create", "delete"], required=True)
    ap.add_argument("--image-url", default="")
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    reg_path = args.out / "fixtures" / "istores" / "registry" / "created_products.json"
    registry = JsonFileRegistry(reg_path)
    raw = args.out / "fixtures" / "istores" / f"{stamp}-p0-06b-{args.mode}"
    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        if args.mode == "create":
            lines = await create(client, registry, raw, args.image_url)
        else:
            ids = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
            lines = await delete_all(client, registry, [int(i["product_id"]) for i in ids])
    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-06b-{args.mode}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# P0-06b {args.mode} - {stamp}\n\n" + "\n".join(lines) + "\n", "utf-8")


if __name__ == "__main__":
    asyncio.run(main())
