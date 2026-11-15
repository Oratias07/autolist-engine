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


def build_body(
    draft: ProductDraft, language_id: int, image_url: str, extra_urls: list[str] | None = None
) -> dict[str, Any]:
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
    if draft.category_ids:  # observed in P1-02 (create and PUT)
        body["product_category"] = list(draft.category_ids)
    if extra_urls:  # observed in P1-02: saved as extra images on create only
        body["images"] = [{"image": u, "sort_order": i} for i, u in enumerate(extra_urls, 1)]
    if draft.special is not None:  # observed in P1-02 (create and PUT)
        body["product_special"] = [
            {
                "price": float(draft.special.price),
                "date_start": draft.special.date_start.isoformat(),
                "date_end": draft.special.date_end.isoformat(),
            }
        ]
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
    if len(draft.extra_images) != len({r.name for r in draft.extra_images}):
        raise UnobservedField("extra images must be distinct objects")
    dup = await find_duplicate(client, page_size, draft.sku, draft.model)
    if dup is not None:
        raise DuplicateProduct(*dup)
    url = blobs.signed_url(draft.main_image, config.publishing.image_url_ttl_s)
    register_secret(url)  # the signature grants read access; keep it out of logs
    extra_urls = []
    for ref in draft.extra_images:
        extra_url = blobs.signed_url(ref, config.publishing.image_url_ttl_s)
        register_secret(extra_url)
        extra_urls.append(extra_url)
    product_id = await client.create_product(
        build_body(draft, language_id, url, extra_urls), job_id=draft.job_id
    )
    problems = created_problems(await client.get_product(product_id), draft, language_id)
    if problems:
        raise VerifyMismatch(product_id, problems)
    log.info("product created hidden and disabled", extra={"product_id": product_id})
    return product_id
