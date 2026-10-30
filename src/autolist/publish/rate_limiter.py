"""Request pacing and retry policy for the store API.

- At most ``rate`` requests per second per token (strict spacing, no bursts).
- 429: wait ``Retry-After`` (seconds or HTTP date), else exponential backoff from 1 s;
  at most 3 retries, then ``StoreRateLimited``.
- Timeouts and 5xx are retried only for idempotent calls (GET, and PUT with the same body).
  POST is never retried on them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from autolist.publish.errors import StoreAPIError, StoreRateLimited

MAX_RETRIES = 3
BACKOFF_START_S = 1.0

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RateLimiter:
    """Strict spacing: one request every ``1/rate`` seconds. Safe across asyncio tasks."""

    def __init__(
        self,
        rate_per_second: float,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self._interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_at: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._next_at is not None and now < self._next_at:
                await self._sleep(self._next_at - now)
                now = self._next_at
            self._next_at = now + self._interval


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse ``Retry-After`` (delta seconds or HTTP date). ``None`` if absent or unparsable."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


async def send_with_policy(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    limiter: RateLimiter,
    idempotent: bool,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    """Run one logical request under the pacing and retry rules."""
    retries = 0
    backoff = BACKOFF_START_S
    while True:
        await limiter.acquire()
        try:
            response = await send()
        except httpx.TimeoutException as exc:
            if not idempotent or retries >= MAX_RETRIES:
                raise StoreAPIError(None, f"timeout: {type(exc).__name__}") from exc
            retries += 1
            await sleep(backoff)
            backoff *= 2
            continue
        if response.status_code == 429:
            if retries >= MAX_RETRIES:
                raise StoreRateLimited(f"still rate limited after {MAX_RETRIES} retries")
            retries += 1
            wait = retry_after_seconds(response.headers.get("Retry-After"))
            await sleep(wait if wait is not None else backoff)
            backoff *= 2
            continue
        if response.status_code >= 500 and idempotent and retries < MAX_RETRIES:
            retries += 1
            await sleep(backoff)
            backoff *= 2
            continue
        return response
