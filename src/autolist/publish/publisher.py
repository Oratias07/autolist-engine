"""Create one product in the store, hidden and disabled, and verify it (P1-01).

Unhide is not here: it stays behind the client's two gates and arrives in phase 8.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autolist.core.blob_store import BlobStore
from autolist.core.logging import get_logger, register_secret
from autolist.core.models import ProductDraft, StoreConfig
from autolist.publish.errors import DuplicateProduct, UnobservedField, VerifyMismatch
from autolist.publish.istores_client import IStoresClient
from autolist.publish.verifier import created_problems

log = get_logger(__name__)
MAX_PAGES = 100


async def find_duplicate(
    client: IStoresClient, page_size: int, sku: str | None, model: str
) -> tuple[str, str, object] | None:
    """Scan the catalog for a product with the same SKU or model (no server-side search is
    recorded in the field map). Returns (field, value, product_id) or None.

    ``sku`` None (not known) checks the model only; a store SKU is never matched against an
    empty value."""
    for page in range(1, MAX_PAGES + 1):
        data = await client.list_products(limit=page_size, page=page)
        products = data.get("products") if isinstance(data, Mapping) else None
        if not isinstance(products, list) or not products:
            return None
        for p in products:
            if not isinstance(p, Mapping):
                continue
            if sku is not None and str(p.get("sku") or "") == sku:
                return ("sku", sku, p.get("product_id"))
            if str(p.get("model") or "") == model:
                return ("model", model, p.get("product_id"))
        if len(products) < page_size:
            return None
    raise RuntimeError(f"catalog scan did not end within {MAX_PAGES} pages")


def build_body(draft: ProductDraft, language_id: int, image_url: str) -> dict[str, Any]:
    """Only fields recorded in the store repo's field map. Always hidden and disabled."""
    body: dict[str, Any] = {
        "product_description": {
            str(language_id): {"name": draft.name, "description": draft.description_html}
        },
        "price": float(draft.price),
        "quantity": draft.quantity,
        "subtract": 1 if draft.stock_managed else 0,  # observed in P0-05 (field map)
        "hidden": 1,
        "status": 0,
        "model": draft.model,
        "image": image_url,
    }
    if draft.sku is not None:  # unknown: not sent; iStores fills its own value (P0-05)
        body["sku"] = draft.sku
    if draft.upc is not None:
        body["upc"] = draft.upc
    return body


async def create_hidden(
    draft: ProductDraft,
    *,
    config: StoreConfig,
    client: IStoresClient,
    blobs: BlobStore,
    language_id: int,
    page_size: int,
) -> int:
    """Duplicate check -> signed image URL -> POST (hidden, disabled) -> GET and compare.

    On a mismatch the product stays hidden and disabled and ``VerifyMismatch`` is raised;
    nothing is deleted or retried here.
    """
    if draft.extra_images:
        raise UnobservedField("the write shape of extra images was never observed; send none")
    dup = await find_duplicate(client, page_size, draft.sku, draft.model)
    if dup is not None:
        raise DuplicateProduct(*dup)
    url = blobs.signed_url(draft.main_image, config.publishing.image_url_ttl_s)
    register_secret(url)  # the signature grants read access; keep it out of logs
    product_id = await client.create_product(
        build_body(draft, language_id, url), job_id=draft.job_id
    )
    problems = created_problems(await client.get_product(product_id), draft, language_id)
    if problems:
        raise VerifyMismatch(product_id, problems)
    log.info("product created hidden and disabled", extra={"product_id": product_id})
    return product_id
