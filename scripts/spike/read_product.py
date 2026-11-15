"""Read-only: the stock-related fields of products this system created (thin CLI).

GET /products/{id} for each id given; nothing is written. Prints only the fields named in
FIELDS (recorded in the store repo's field map) and saves the raw responses to the store
repo's fixtures. The client's allowlist refuses any path other than products.
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
from autolist.publish.istores_client import IStoresClient
from autolist.publish.registry import JsonFileRegistry

FIELDS = (
    "product_id",
    "quantity",
    "minimum",
    "maximum",
    "subtract",
    "stock_status",
    "status",
    "hidden",
    "price",
    "model",
    "sku",
)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ids", type=int, nargs="+", required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = args.out / "fixtures" / "istores" / f"{stamp}-read-product"
    registry = JsonFileRegistry(raw_dir / "registry-unused.json")
    lines = [f"# read product (read-only) - {stamp}", ""]
    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        for pid in args.ids:
            data: Any = await client.get_product(pid)
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{pid}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            p = data.get("product") if isinstance(data, dict) else None
            p = p if isinstance(p, dict) else {}
            lines.append(f"## {pid}")
            lines += [f"- {k}: {p.get(k)!r}" for k in FIELDS]
            lines.append("")
    report = args.out / "docs" / "live-runs" / f"{stamp}-read-product.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
