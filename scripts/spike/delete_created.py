"""Delete products this system created (thin CLI; store write, DEV approval per run).

Only ids in the service's Firestore registry (`created_products`) can be deleted: the client
refuses any other id (`ForeignProductError`) before a request is made. After each delete a GET
confirms the product is gone. Writes a report to the store repo.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.publish.errors import StoreAPIError
from autolist.publish.firestore_registry import FirestoreRegistry
from autolist.publish.istores_client import IStoresClient


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ids", type=int, nargs="+", required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    from google.cloud import firestore  # live only

    registry = FirestoreRegistry(firestore.Client(project=cfg.gcp.project_id))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# delete created products (DEV approved) - {stamp}", ""]
    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        for pid in args.ids:
            await client.delete_product(pid)
            try:
                await client.get_product(pid)
                gone = "STILL THERE"
            except StoreAPIError as exc:
                gone = "gone (404)" if exc.status == 404 else f"UNKNOWN ({exc.status})"
            lines.append(f"- {pid}: deleted; GET after: {gone}")
    report = args.out / "docs" / "live-runs" / f"{stamp}-delete-created.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
