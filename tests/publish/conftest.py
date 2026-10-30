from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from autolist.core.config import parse_store_config
from autolist.core.logging import _clear_registered_secrets
from autolist.core.models import StoreConfig
from autolist.publish.istores_client import IStoresClient
from autolist.publish.rate_limiter import RateLimiter
from autolist.publish.registry import InMemoryRegistry
from tests.core.conftest import VALID_STORE

TOKEN = "test-token-value-123"  # noqa: S105 - fake


class FakeTime:
    """Deterministic monotonic clock + sleep for pacing tests."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.calls: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._handler(request)


def make_config(**publishing: Any) -> StoreConfig:
    data = copy.deepcopy(VALID_STORE)
    data["istores"]["required_name_languages"] = [3]
    data["publishing"].update(publishing)
    return parse_store_config(data)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    config: StoreConfig | None = None,
    registry: InMemoryRegistry | None = None,
) -> tuple[IStoresClient, Recorder, InMemoryRegistry, FakeTime]:
    rec = Recorder(handler)
    reg = registry or InMemoryRegistry()
    ft = FakeTime()
    client = IStoresClient(
        config or make_config(),
        TOKEN,
        reg,
        transport=httpx.MockTransport(rec),
        limiter=RateLimiter(2, clock=ft.clock, sleep=ft.sleep),
        sleep=ft.sleep,
    )
    return client, rec, reg, ft


@pytest.fixture(autouse=True)
def _clean_secrets() -> Any:
    yield
    _clear_registered_secrets()
