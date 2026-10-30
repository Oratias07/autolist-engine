from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from autolist.publish.errors import StoreAPIError, StoreRateLimited
from autolist.publish.rate_limiter import RateLimiter, retry_after_seconds
from tests.publish.conftest import make_client


async def test_ten_queued_gets_take_at_least_4_5_seconds() -> None:
    client, rec, _, ft = make_client(lambda r: httpx.Response(200, json={}))
    await asyncio.gather(*(client.get_product(i) for i in range(10)))
    assert len(rec.calls) == 10
    assert ft.now >= 4.5
    await client.aclose()


async def test_429_with_retry_after_waits_then_succeeds() -> None:
    responses = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})]
    client, rec, _, ft = make_client(lambda r: responses.pop(0))
    assert await client.get_product(1) == {}
    assert 2.0 in ft.sleeps and ft.now >= 2.0
    assert len(rec.calls) == 2
    await client.aclose()


async def test_429_exhausted_raises() -> None:
    client, rec, _, ft = make_client(lambda r: httpx.Response(429))
    with pytest.raises(StoreRateLimited):
        await client.get_product(1)
    assert len(rec.calls) == 4  # first try + 3 retries
    assert ft.sleeps[:3] == [1.0, 2.0, 4.0] or sorted(ft.sleeps)[-1] >= 4.0
    await client.aclose()


async def test_post_timeout_is_not_retried() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    client, rec, reg, _ = make_client(boom)
    with pytest.raises(StoreAPIError, match="timeout"):
        await client.create_product({"hidden": 1}, job_id="job-1")
    assert len(rec.calls) == 1
    assert reg.get(1) is None
    await client.aclose()


async def test_post_5xx_is_not_retried() -> None:
    client, rec, _, _ = make_client(lambda r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(StoreAPIError):
        await client.create_product({"hidden": 1}, job_id=None)
    assert len(rec.calls) == 1
    await client.aclose()


async def test_get_timeout_and_5xx_are_retried() -> None:
    state = {"n": 0}

    def flaky(r: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectTimeout("t", request=r)
        if state["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client, rec, _, _ = make_client(flaky)
    assert await client.get_product(1) == {"ok": True}
    assert len(rec.calls) == 3
    await client.aclose()


async def test_get_timeout_exhausted() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    client, rec, _, _ = make_client(boom)
    with pytest.raises(StoreAPIError):
        await client.get_product(1)
    assert len(rec.calls) == 4
    await client.aclose()


def test_retry_after_parsing() -> None:
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    assert retry_after_seconds("2") == 2.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds(format_datetime(now + timedelta(seconds=5)), now=now) == 5.0
    assert retry_after_seconds(format_datetime(now - timedelta(seconds=5)), now=now) == 0.0
    naive = "Fri, 30 Oct 2026 12:00:03 -0000"
    assert retry_after_seconds(naive, now=now) == 3.0


def test_limiter_rejects_bad_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)
