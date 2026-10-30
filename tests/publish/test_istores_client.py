from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from autolist.core.errors import ConfigMissing
from autolist.publish.errors import (
    CreateResultUnknown,
    EndpointNotAllowed,
    ForeignProductError,
    StoreAPIError,
    UnhideDisabled,
    UnsafeCreate,
    UnsafeUnhide,
)
from autolist.publish.istores_client import (
    IStoresClient,
    check_allowed,
    is_unhide,
    visibility_problems,
)
from autolist.publish.registry import CreatedProduct, InMemoryRegistry
from tests.publish.conftest import TOKEN, make_client, make_config

OK = httpx.Response(200, json={"success": True})

VISIBLE_OK = {
    "success": True,
    "product": {
        "price": "25.00",
        "product_category": [12],
        "product_description": {"3": {"name": "Item", "description": "<p>x</p>"}},
    },
}


def _own(reg: InMemoryRegistry, pid: int) -> None:
    reg.add(CreatedProduct(product_id=pid, created_at=datetime.now(UTC), job_id="job-1"))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/orders"),
        ("POST", "/orders"),
        ("GET", "/order/1"),
        ("GET", "/customers"),
        ("GET", "/customers/5"),
        ("PUT", "/products/bulk"),
        ("GET", "/products"),
        ("POST", "/products/5"),
        ("DELETE", "/categories/1"),
        ("GET", "/misc"),
        ("GET", "/products/1/../../orders"),
        ("GET", "/payments"),
    ],
)
async def test_disallowed_paths_never_reach_the_network(method: str, path: str) -> None:
    client, rec, _, _ = make_client(lambda r: OK)
    with pytest.raises(EndpointNotAllowed):
        await client._request(method, path, idempotent=True)
    assert rec.calls == []
    await client.aclose()


def test_allowlist_accepts_documented_paths() -> None:
    for method, path in [
        ("GET", "/products/200/1"),
        ("GET", "/products/970"),
        ("POST", "/products"),
        ("PUT", "/products/970"),
        ("DELETE", "/products/970"),
        ("GET", "/categories"),
        ("GET", "/categories/tree"),
        ("GET", "/misc/languages"),
        ("GET", "/misc/stock_status?x=1"),
    ]:
        check_allowed(method, path)


async def test_headers_and_reads() -> None:
    client, rec, _, _ = make_client(lambda r: httpx.Response(200, json={"ok": r.url.path}))
    assert await client.list_products(200, 1) == {"ok": "/products/200/1"}
    assert await client.get_product(970) == {"ok": "/products/970"}
    assert await client.get_categories() == {"ok": "/categories"}
    assert await client.get_misc("languages") == {"ok": "/misc/languages"}
    req = rec.calls[0]
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["Company-Id"] == "1"
    assert req.headers["User-Agent"] == "autolist/test"
    assert req.headers["Content-Type"] == "application/json"
    await client.aclose()


@pytest.mark.parametrize("method", ["update", "delete"])
async def test_foreign_product_guard(method: str) -> None:
    client, rec, _reg, _ = make_client(lambda r: OK)
    with pytest.raises(ForeignProductError):
        if method == "update":
            await client.update_product(555, {"price": 10})
        else:
            await client.delete_product(555)
    assert rec.calls == []
    await client.aclose()


@pytest.mark.parametrize("hidden", [None, 0, "0", False])
async def test_create_requires_hidden(hidden: Any) -> None:
    client, rec, _, _ = make_client(lambda r: OK)
    body: dict[str, Any] = {"product_description": {"3": {"name": "x"}}}
    if hidden is not None:
        body["hidden"] = hidden
    with pytest.raises(UnsafeCreate):
        await client.create_product(body, job_id="job-1")
    assert rec.calls == []
    await client.aclose()


async def test_create_parses_id_and_registers() -> None:
    client, rec, reg, _ = make_client(lambda r: httpx.Response(200, text='"productID: 970"'))
    pid = await client.create_product({"hidden": 1, "price": 25}, job_id="job-1")
    assert pid == 970
    assert reg.is_active(970)
    assert json.loads(rec.calls[0].content) == {"hidden": 1, "price": 25}
    await client.aclose()


async def test_create_without_id_raises_unknown_and_registers_nothing() -> None:
    client, _, reg, _ = make_client(lambda r: httpx.Response(200, text="created"))
    with pytest.raises(CreateResultUnknown):
        await client.create_product({"hidden": True}, job_id=None)
    assert reg.get(970) is None
    await client.aclose()


async def test_unhide_refused_while_switch_off_even_for_valid_product() -> None:
    client, rec, reg, _ = make_client(lambda r: httpx.Response(200, json=VISIBLE_OK))
    _own(reg, 970)
    for body in ({"hidden": 0}, {"status": 1}, {"hidden": "0", "price": 30}):
        with pytest.raises(UnhideDisabled):
            await client.update_product(970, body)
    assert rec.calls == []
    await client.aclose()


async def test_unhide_with_switch_on_and_price_zero_is_refused() -> None:
    bad = {"success": True, "product": {**VISIBLE_OK["product"], "price": "0.00"}}
    config = make_config(unhide_enabled=True, enabled_by="DEV", enabled_at="2026-11-01")
    client, rec, reg, _ = make_client(lambda r: httpx.Response(200, json=bad), config=config)
    _own(reg, 970)
    with pytest.raises(UnsafeUnhide, match="price"):
        await client.update_product(970, {"hidden": 0})
    assert [r.method for r in rec.calls] == ["GET"]  # read-back only, zero writes
    await client.aclose()


async def test_unhide_with_switch_on_and_valid_product_puts() -> None:
    config = make_config(unhide_enabled=True, enabled_by="DEV", enabled_at="2026-11-01")

    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=VISIBLE_OK if r.method == "GET" else {"success": True})

    client, rec, reg, _ = make_client(handler, config=config)
    _own(reg, 970)
    assert await client.update_product(970, {"hidden": 0}) == {"success": True}
    assert [r.method for r in rec.calls] == ["GET", "PUT"]
    await client.aclose()


async def test_unhide_needs_known_languages_and_product_object() -> None:
    config = make_config(unhide_enabled=True, enabled_by="DEV", enabled_at="2026-11-01")
    client, _rec, reg, _ = make_client(
        lambda r: httpx.Response(200, json={"success": True}), config=config
    )
    _own(reg, 970)
    with pytest.raises(UnsafeUnhide, match="no 'product'"):
        await client.update_product(970, {"status": 1})
    await client.aclose()
    data = config.model_dump()
    data["istores"]["required_name_languages"] = None
    unknown_langs = type(config).model_validate(data)
    client2, rec2, reg2, _ = make_client(lambda r: OK, config=unknown_langs)
    _own(reg2, 970)
    with pytest.raises(ConfigMissing):
        await client2.update_product(970, {"hidden": 0})
    assert rec2.calls == []
    await client2.aclose()


def test_visibility_problems_fail_closed() -> None:
    assert visibility_problems(VISIBLE_OK["product"], [3]) == []
    assert visibility_problems({}, [3]) == [
        "price missing or unreadable",
        "no category",
        "product_description missing or unreadable",
    ]
    p = {"price": "abc", "product_category": [], "product_description": {"3": {"name": " "}}}
    assert visibility_problems(p, [3, 1]) == [
        "price missing or unreadable",
        "no category",
        "empty name for language 3",
        "empty name for language 1",
    ]


def test_is_unhide() -> None:
    assert is_unhide({"hidden": 0}) and is_unhide({"status": True}) and is_unhide({"status": "1"})
    assert not is_unhide({"price": 10}) and not is_unhide({"hidden": 1, "status": 0})


async def test_put_and_delete_own_product() -> None:
    client, rec, reg, _ = make_client(lambda r: httpx.Response(200, content=b""))
    _own(reg, 971)
    assert await client.update_product(971, {"price": 30}) is None
    await client.delete_product(971)
    assert not reg.is_active(971)
    with pytest.raises(ForeignProductError):
        await client.delete_product(971)
    assert [r.method for r in rec.calls] == ["PUT", "DELETE"]
    await client.aclose()


async def test_http_error_raises_with_excerpt() -> None:
    client, _, _, _ = make_client(lambda r: httpx.Response(404, text="not found"))
    with pytest.raises(StoreAPIError) as exc:
        await client.get_product(1)
    assert exc.value.status == 404 and "not found" in exc.value.body_excerpt
    await client.aclose()


async def test_empty_token_refused_and_context_manager() -> None:
    with pytest.raises(ConfigMissing):
        IStoresClient(make_config(), "", InMemoryRegistry())
    async with IStoresClient(
        make_config(), TOKEN, InMemoryRegistry(), transport=httpx.MockTransport(lambda r: OK)
    ) as client:
        assert await client.get_product(1) == {"success": True}
