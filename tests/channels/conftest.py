from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from autolist.core.config import parse_store_config
from autolist.core.models import EmailChannelConfig
from tests.core.conftest import VALID_STORE

REPLIES = Path(__file__).parent / "fixtures" / "replies"


@pytest.fixture
def email_cfg() -> EmailChannelConfig:
    return parse_store_config(copy.deepcopy(VALID_STORE)).channels.email


class Call:
    def __init__(self, result: Any, log: list[tuple[str, dict[str, Any]]], name: str) -> None:
        self._result, self._log, self._name = result, log, name

    def execute(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeGmail:
    """Minimal stand-in for the googleapiclient Gmail service (records calls)."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def users(self) -> FakeGmail:
        return self

    def messages(self) -> FakeGmail:
        return self

    def history(self) -> FakeGmail:
        return self

    def _call(self, name: str, **kw: Any) -> Call:
        self.calls.append((name, kw))
        res = self.results[name]
        if isinstance(res, list):
            res = res.pop(0)
        return Call(res, self.calls, name)

    def send(self, **kw: Any) -> Call:
        return self._call("send", **kw)

    def watch(self, **kw: Any) -> Call:
        return self._call("watch", **kw)

    def list(self, **kw: Any) -> Call:
        return self._call("history.list", **kw)

    def get(self, **kw: Any) -> Call:
        return self._call("get", **kw)

    def getProfile(self, **kw: Any) -> Call:
        return self._call("getProfile", **kw)
