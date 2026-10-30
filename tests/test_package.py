"""Smoke test: the package and every module package import cleanly."""

import importlib

import pytest

MODULES = [
    "autolist",
    "autolist.orchestrator",
    "autolist.core",
    "autolist.llm",
    "autolist.channels",
    "autolist.agent_runtime",
    "autolist.intake",
    "autolist.research",
    "autolist.content",
    "autolist.images",
    "autolist.review",
    "autolist.publish",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None
