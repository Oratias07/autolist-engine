"""iStores REST client: allowlisted endpoints, own-product guard, hidden-first creation.

Every guard runs before any network I/O. The token is registered with the log redaction
filter on construction and never logged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from autolist.core.errors import ConfigMissing
from autolist.core.logging import get_logger, register_secret
from autolist.core.models import StoreConfig
from autolist.publish.errors import (
    CreateResultUnknown,
    EndpointNotAllowed,
    ForeignProductError,
    StoreAPIError,
    UnhideDisabled,
    UnsafeCreate,
    UnsafeUnhide,
)
from autolist.publish.rate_limiter import RateLimiter, Sleep, send_with_policy
from autolist.publish.registry import CreatedProduct, CreatedProductsRegistry

log = get_logger(__name__)

BODY_LOG_LIMIT = 2048
_CREATED_ID = re.compile(r"productID\s*:\s*(\d+)")

# (method, compiled path pattern). Anything else raises before I/O.
_ALLOWED: list[tuple[str, re.Pattern[str]]] = [
    ("GET", re.compile(r"/products/\d+/\d+")),
    ("GET", re.compile(r"/products/\d+")),
    ("POST", re.compile(r"/products")),
    ("PUT", re.compile(r"/products/\d+")),
    ("DELETE", re.compile(r"/products/\d+")),
    ("GET", re.compile(r"/categories(/[A-Za-z0-9_\-]+)*")),
    ("GET", re.compile(r"/misc(/[A-Za-z0-9_\-]+)+")),
]


def check_allowed(method: str, path: str) -> None:
    """Raise ``EndpointNotAllowed`` unless (method, path) is on the allowlist."""
    clean = path.split("?", 1)[0]
    if any(m == method and p.fullmatch(clean) for m, p in _ALLOWED):
        return
    raise EndpointNotAllowed(method, path)


def _is_truthy_flag(value: Any) -> bool:
    return value is True or value == 1 or value == "1"


def _is_falsy_flag(value: Any) -> bool:
    return value is False or value == 0 or value == "0"


def is_unhide(body: Mapping[str, Any]) -> bool:
    """A PUT that makes the product visible: ``hidden`` -> 0 or ``status`` -> 1."""
    return ("hidden" in body and _is_falsy_flag(body["hidden"])) or (
        "status" in body and _is_truthy_flag(body["status"])
    )


def visibility_problems(product: Mapping[str, Any], required_languages: list[Any]) -> list[str]:
    """Why a product read back from the store may not be shown. Fails closed.

    The GET response shape is not documented by iStores; fields are read with the names of
    the documented POST body. A field that is missing or unreadable counts as a problem,
    so an unconfirmed shape refuses instead of guessing (confirmed in P0-05/P0-06).
    """
    problems: list[str] = []
    try:
        price = Decimal(str(product["price"]))
        if not price > 0:
            problems.append("price must be > 0")
    except (KeyError, InvalidOperation, ValueError):
        problems.append("price missing or unreadable")
    categories = product.get("product_category")
    if not isinstance(categories, list) or not categories:
        problems.append("no category")
    descriptions = product.get("product_description")
    if not isinstance(descriptions, Mapping):
        problems.append("product_description missing or unreadable")
    else:
        for lang in required_languages:
            entry = descriptions.get(str(lang))
            name = entry.get("name") if isinstance(entry, Mapping) else None
            if not isinstance(name, str) or not name.strip():
                problems.append(f"empty name for language {lang}")
    return problems


class IStoresClient:
    def __init__(
        self,
        config: StoreConfig,
        token: str,
        registry: CreatedProductsRegistry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: RateLimiter | None = None,
        sleep: Sleep | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_s: float = 30.0,
    ) -> None:
        if not token:
            raise ConfigMissing(f"secret {config.secrets.istores_token}")
        register_secret(token)
        self._config = config
        self._registry = registry
        self._now = now
        self._limiter = limiter or RateLimiter(config.istores.rate_limit_per_second)
        self._sleep_kw: dict[str, Sleep] = {"sleep": sleep} if sleep is not None else {}
        self._http = httpx.AsyncClient(
            base_url=config.istores.base_url,
            transport=transport,
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {token}",
                "Company-Id": str(config.istores.company_id),
                "User-Agent": config.istores.user_agent,
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> IStoresClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ transport

    async def _request(
        self, method: str, path: str, *, json_body: Any = None, idempotent: bool
    ) -> httpx.Response:
        check_allowed(method, path)

        async def send() -> httpx.Response:
            return await self._http.request(method, path, json=json_body)

        response = await send_with_policy(
            send, limiter=self._limiter, idempotent=idempotent, **self._sleep_kw
        )
        log.info(
            "istores %s %s -> %s",
            method,
            path,
            response.status_code,
            extra={"response_excerpt": response.text[:BODY_LOG_LIMIT]},
        )
        if response.status_code >= 400:
            raise StoreAPIError(response.status_code, response.text[:BODY_LOG_LIMIT])
        return response

    async def _get_json(self, path: str) -> Any:
        return (await self._request("GET", path, idempotent=True)).json()

    # ------------------------------------------------------------------ reads

    async def list_products(self, limit: int, page: int) -> Any:
        return await self._get_json(f"/products/{int(limit)}/{int(page)}")

    async def get_product(self, product_id: int) -> Any:
        return await self._get_json(f"/products/{int(product_id)}")

    async def get_categories(self, subpath: str = "") -> Any:
        return await self._get_json(f"/categories{subpath}")

    async def get_misc(self, subpath: str) -> Any:
        return await self._get_json(f"/misc/{subpath.lstrip('/')}")

    # ------------------------------------------------------------------ writes

    async def create_product(self, body: Mapping[str, Any], job_id: str | None) -> int:
        """POST a hidden product, record its id in the registry, return the id.

        Never retried on timeout or 5xx. If no id can be parsed, raises
        ``CreateResultUnknown``: the caller searches by SKU before doing anything else.
        """
        if not _is_truthy_flag(body.get("hidden")):
            raise UnsafeCreate("products must be created with hidden: 1")
        check_allowed("POST", "/products")
        response = await self._request("POST", "/products", json_body=dict(body), idempotent=False)
        match = _CREATED_ID.search(response.text)
        if match is None:
            raise CreateResultUnknown(f"no product id in response: {response.text[:200]}")
        product_id = int(match.group(1))
        self._registry.add(
            CreatedProduct(product_id=product_id, created_at=self._now(), job_id=job_id)
        )
        return product_id

    def _require_own(self, product_id: int) -> None:
        if not self._registry.is_active(product_id):
            raise ForeignProductError(product_id)

    async def update_product(self, product_id: int, body: Mapping[str, Any]) -> Any:
        """PUT on a product this system created. Unhiding passes both gates first."""
        check_allowed("PUT", f"/products/{int(product_id)}")
        self._require_own(product_id)
        if is_unhide(body):
            await self._check_unhide(product_id)
        response = await self._request(
            "PUT", f"/products/{int(product_id)}", json_body=dict(body), idempotent=True
        )
        return response.json() if response.content else None

    async def delete_product(self, product_id: int) -> None:
        check_allowed("DELETE", f"/products/{int(product_id)}")
        self._require_own(product_id)
        await self._request("DELETE", f"/products/{int(product_id)}", idempotent=False)
        self._registry.mark_deleted(product_id, self._now())

    async def _check_unhide(self, product_id: int) -> None:
        publishing = self._config.publishing
        if not (publishing.unhide_enabled and publishing.enabled_by and publishing.enabled_at):
            raise UnhideDisabled(
                "store publishing switch is off; products stay hidden until it is enabled"
            )
        required: list[Any] = self._config.require("istores.required_name_languages")
        data = await self.get_product(product_id)
        product = data.get("product") if isinstance(data, Mapping) else None
        if not isinstance(product, Mapping):
            raise UnsafeUnhide("product read-back has no 'product' object")
        problems = visibility_problems(product, required)
        if problems:
            raise UnsafeUnhide("; ".join(problems))
