"""P1-01 live run: create the prepared samples in the store, hidden and disabled.

STORE WRITE. Runs only through the store repo's `live` workflow, after DEV approves the exact
run. Thin CLI over core.blob_store + publish.publisher: reads the manifest
fixtures/samples/p1-01-samples.yaml in the store repo, uploads each main image to the private
bucket, and calls `create_hidden` (duplicate check, POST hidden + disabled, GET and compare).
Nothing is deleted: the products stay for the client to see in the admin UI. A failure on one
item is reported and the run continues with the next.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from autolist.core.blob_store import GcsBlobStore
from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.errors import AutolistError
from autolist.core.logging import configure_logging
from autolist.core.models import ProductDraft
from autolist.images.postprocess import postprocess
from autolist.publish.istores_client import IStoresClient
from autolist.publish.publisher import create_hidden
from autolist.publish.registry import JsonFileRegistry


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    store = store_dir()
    manifest: dict[str, Any] = yaml.safe_load(
        (store / "fixtures" / "samples" / "p1-01-samples.yaml").read_text(encoding="utf-8")
    )
    languages: list[Any] = cfg.require("istores.required_name_languages")
    language_id = int(languages[0])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    registry = JsonFileRegistry(
        args.out / "fixtures" / "istores" / "registry" / "created_products.json"
    )
    blobs = GcsBlobStore(cfg)
    lines = [f"# P1-01 samples create - {stamp}", ""]
    ok = 0
    async with IStoresClient(cfg, get_secret(cfg, "istores_token"), registry) as client:
        for item in manifest["items"]:
            pid = item["id"]
            title = json.loads((store / item["title_json"]).read_text(encoding="utf-8"))["title"]
            body = (store / item["description_html"]).read_text(encoding="utf-8")
            image = (store / item["main_image"]).read_bytes()
            if item["postprocess"]:
                image = postprocess(image, cfg.images.output_px)
            ref = blobs.put(f"p1-01/{stamp}/{pid}.jpg", image, "image/jpeg")
            draft = ProductDraft(
                job_id=f"p1-01-{pid}",
                name=manifest["name_prefix"] + title,
                description_html=manifest["description_banner"] + "\n" + body,
                price=Decimal(str(manifest["price"])),
                quantity=int(item["quantity"]),
                sku=item["sku"],
                model=item["model"],
                upc=None,
                main_image=ref,
                extra_images=[],
            )
            try:
                product_id = await create_hidden(
                    draft,
                    config=cfg,
                    client=client,
                    blobs=blobs,
                    language_id=language_id,
                    page_size=int(manifest["catalog_page_size"]),
                )
            except AutolistError as exc:
                lines.append(f"- {pid}: FAILED {type(exc).__name__}: {exc}")
            else:
                ok += 1
                lines.append(
                    f"- {pid}: created product {product_id}, verified hidden + disabled "
                    f"(sku {draft.sku}, price {draft.price}, quantity {draft.quantity})"
                )
            finally:
                blobs.delete(ref)  # the store has copied the image (or the create failed)
    lines += ["", f"**Result: {ok}/{len(manifest['items'])} created and verified**"]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-01-samples-create.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
