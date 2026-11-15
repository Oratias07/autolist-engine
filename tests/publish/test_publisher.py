"""publisher.create_hidden + verifier (P1-01): synthetic store, no network."""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from autolist.core.blob_store import InMemoryBlobStore
from autolist.core.models import ImageRef, ProductDraft, SaleSpec
from autolist.publish.errors import DuplicateProduct, UnobservedField, VerifyMismatch
from autolist.publish.publisher import create_hidden
from tests.publish.conftest import make_client, make_config

LANG = 3
CDN = "https://cdn.istores.co.il/image/upload/clients/1/abc.jpg"


def _draft(blobs: InMemoryBlobStore, **kw: Any) -> ProductDraft:
    ref = blobs.put("job-1/main.jpg", b"img", "image/jpeg")
    base: dict[str, Any] = {
        "job_id": "job-1",
        "name": "כיסא בדיקה",
        "description_html": "<p>תיאור</p>",
        "price": Decimal(0),
        "quantity": 2,
        "sku": "SKU-1",
        "model": "M-1",
        "upc": None,
        "stock_managed": True,
        "category_ids": [],
        "main_image": ref,
        "extra_images": [],
    }
    base.update(kw)
    return ProductDraft(**base)


class Store:
    """Synthetic store: one catalog page, POST creates id 970, GET echoes the posted body."""

    def __init__(self, catalog: list[dict[str, Any]] | None = None, **overrides: Any) -> None:
        self.catalog = catalog or []
        self.overrides = overrides
        self.posted: dict[str, Any] | None = None

    def __call__(self, r: httpx.Request) -> httpx.Response:
        if r.method == "GET" and r.url.path.count("/") == 3:  # /products/<limit>/<page>
            return httpx.Response(200, json={"products": self.catalog})
        if r.method == "POST":
            self.posted = json.loads(r.content)
            return httpx.Response(200, text='"productID: 970"')
        if r.method == "GET":
            assert self.posted is not None
            # read shape observed in P1-02: categories come back as an object keyed by id
            cats = {str(i): [{"category_id": i}] for i in self.posted.get("product_category", [])}
            # read shapes observed in P1-02
            images = {
                str(i["sort_order"]): {"image": CDN, "sort_order": i["sort_order"]}
                for i in self.posted.get("images", [])
            }
            special = [
                {**s, "price": f"{s['price']:.4f}", "customer_group_id": 1, "priority": 0}
                for s in self.posted.get("product_special", [])
            ]
            p = {
                **self.posted,
                "image": CDN,
                "product_id": 970,
                "category": cats,
                "images": images,
                "special": special,
                **self.overrides,
            }
            return httpx.Response(200, json={"product": p})
        return httpx.Response(500)


async def _run(store: Store, draft: ProductDraft, blobs: InMemoryBlobStore) -> Any:
    client, rec, reg, _ = make_client(store)
    try:
        pid = await create_hidden(
            draft, config=make_config(), client=client, blobs=blobs, language_id=LANG, page_size=100
        )
    finally:
        await client.aclose()
    return pid, rec, reg


async def test_creates_hidden_disabled_and_verifies() -> None:
    blobs, store = InMemoryBlobStore(), Store(catalog=[{"sku": "other", "model": "x"}])
    pid, rec, reg = await _run(store, _draft(blobs), blobs)
    assert pid == 970
    assert reg.is_active(970)
    assert store.posted is not None
    assert store.posted["hidden"] == 1
    assert store.posted["status"] == 0
    assert store.posted["price"] == 0
    assert store.posted["product_description"] == {
        "3": {"name": "כיסא בדיקה", "description": "<p>תיאור</p>"}
    }
    assert store.posted["image"].startswith("https://storage.example.com/")
    assert "upc" not in store.posted
    assert [c.method for c in rec.calls] == ["GET", "POST", "GET"]


@pytest.mark.parametrize("field", ["sku", "model"])
async def test_duplicate_sends_no_post(field: str) -> None:
    blobs = InMemoryBlobStore()
    value = "SKU-1" if field == "sku" else "M-1"
    store = Store(catalog=[{"product_id": 5, field: value}])
    with pytest.raises(DuplicateProduct) as exc:
        await _run(store, _draft(blobs), blobs)
    assert exc.value.field == field
    assert store.posted is None


def _extra(blobs: InMemoryBlobStore) -> ImageRef:
    return blobs.put("job-1/lifestyle.jpg", b"life", "image/jpeg")


SALE = SaleSpec(price=Decimal("5.9"), date_start=date(2026, 11, 15), date_end=date(2026, 11, 30))


async def test_extra_image_and_sale_are_sent_on_create_and_verified() -> None:
    """P1-02 write shapes: `images` (create only) and `product_special`."""
    blobs = InMemoryBlobStore()
    store = Store()
    draft = _draft(blobs, price=Decimal("7.9"), extra_images=[_extra(blobs)], special=SALE)
    await _run(store, draft, blobs)
    assert store.posted is not None
    [image] = store.posted["images"]
    assert image["sort_order"] == 1 and "lifestyle.jpg" in image["image"]
    assert store.posted["product_special"] == [
        {"price": 5.9, "date_start": "2026-11-15", "date_end": "2026-11-30"}
    ]


async def test_no_extras_sends_neither_field() -> None:
    blobs = InMemoryBlobStore()
    store = Store()
    await _run(store, _draft(blobs), blobs)
    assert store.posted is not None
    assert "images" not in store.posted and "product_special" not in store.posted


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"images": {}}, "extra images"),
        ({"special": []}, "sale"),
        (
            {
                "special": [
                    {"price": "5.9000", "date_start": "2026-11-15", "date_end": "2026-12-01"}
                ]
            },
            "sale",
        ),
    ],
)
async def test_a_missing_extra_or_sale_fails_the_verify(
    overrides: dict[str, Any], problem: str
) -> None:
    blobs = InMemoryBlobStore()
    draft = _draft(blobs, price=Decimal("7.9"), extra_images=[_extra(blobs)], special=SALE)
    with pytest.raises(VerifyMismatch, match=problem):
        await _run(Store(**overrides), draft, blobs)


async def test_the_same_extra_image_twice_is_refused_before_any_call() -> None:
    blobs = InMemoryBlobStore()
    extra = _extra(blobs)
    store = Store()
    with pytest.raises(UnobservedField):
        await _run(store, _draft(blobs, extra_images=[extra, extra]), blobs)
    assert store.posted is None


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"hidden": 0}, "NOT hidden"),
        ({"status": 1}, "NOT disabled"),
        ({"price": 5}, "price"),
        ({"quantity": 9}, "quantity"),
        ({"sku": "zzz"}, "sku"),
        ({"image": "https://elsewhere.example.com/a.jpg"}, "image"),
        ({"product_description": {"3": {"name": "אחר"}}}, "name"),
        ({"subtract": 0}, "subtract"),
    ],
)
async def test_mismatch_raises_and_sends_no_delete_or_put(
    overrides: dict[str, Any], problem: str
) -> None:
    blobs, store = InMemoryBlobStore(), Store(**overrides)
    client, rec, _, _ = make_client(store)
    with pytest.raises(VerifyMismatch) as exc:
        await create_hidden(
            _draft(blobs),
            config=make_config(),
            client=client,
            blobs=blobs,
            language_id=LANG,
            page_size=100,
        )
    await client.aclose()
    assert exc.value.product_id == 970
    assert any(problem in p for p in exc.value.problems)
    assert {c.method for c in rec.calls} <= {"GET", "POST"}


async def test_signed_url_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    blobs, store = InMemoryBlobStore(), Store()
    await _run(store, _draft(blobs), blobs)
    assert "sig=test" not in caplog.text


async def test_upc_is_sent_when_present() -> None:
    blobs, store = InMemoryBlobStore(), Store()
    await _run(store, _draft(blobs, upc="7290000000001"), blobs)
    assert store.posted is not None
    assert store.posted["upc"] == "7290000000001"


async def test_unknown_sku_is_not_sent_and_dup_check_uses_model_only() -> None:
    blobs = InMemoryBlobStore()
    # a store product with an empty SKU must not match an unknown SKU; iStores fills its own
    store = Store(catalog=[{"sku": "", "model": "other"}], sku="0a53773d" * 4)
    pid, _, _ = await _run(store, _draft(blobs, sku=None), blobs)
    assert pid == 970
    assert store.posted is not None and "sku" not in store.posted


async def test_unknown_sku_duplicate_model_found() -> None:
    blobs = InMemoryBlobStore()
    store = Store(catalog=[{"sku": "x", "model": "M-1", "product_id": 5}])
    with pytest.raises(DuplicateProduct):
        await _run(store, _draft(blobs, sku=None), blobs)


async def test_stock_management_is_sent() -> None:
    blobs = InMemoryBlobStore()
    store = Store()
    await _run(store, _draft(blobs), blobs)
    assert store.posted is not None and store.posted["subtract"] == 1


async def test_categories_are_sent_and_verified() -> None:
    blobs = InMemoryBlobStore()
    store = Store()
    await _run(store, _draft(blobs, category_ids=[97008]), blobs)
    assert store.posted is not None and store.posted["product_category"] == [97008]


async def test_a_missing_category_on_the_store_fails_the_verify() -> None:
    blobs = InMemoryBlobStore()
    with pytest.raises(VerifyMismatch, match="categories"):
        await _run(Store(category={}), _draft(blobs, category_ids=[97008]), blobs)
