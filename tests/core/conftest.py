"""Shared fixtures for core tests. Values are generic examples, never client data."""

from __future__ import annotations

import copy
from typing import Any

import pytest

VALID_STORE: dict[str, Any] = {
    "store": {"name": "Example Store", "domain": "shop.example.com", "timezone": "Asia/Jerusalem"},
    "istores": {
        "base_url": "https://api.example.com",
        "company_id": 1,
        "user_agent": "autolist/test",
        "rate_limit_per_second": 2,
        "languages": None,
        "required_name_languages": None,
        "stock_statuses": None,
    },
    "gcp": {"project_id": "example-project", "region": None, "reference_bucket": None},
    "secrets": {
        "istores_token": "istores-token",
        "anthropic_api_key": "anthropic-api-key",
        "gemini_api_key": "gemini-api-key",
        "gmail_refresh_token": "gmail-refresh-token",
    },
    "channels": {
        "email": {
            "agent_mailbox": "agent@example.com",
            "owner_address": "owner@example.com",
            "allowed_senders": ["owner@example.com"],
            "require": {"dkim": "pass", "dmarc": "pass"},
        }
    },
    "costs": {
        "currency": "USD",
        "monthly_cap": 60,
        "per_job_cap": 2,
        "alert_thresholds": [30, 40, 50],
        "phase0_budget": {"anthropic": 5, "gemini": 3},
    },
    "llm": {
        "provider": "anthropic",
        "models": {"extract": None, "write": None, "classify": None},
        "bakeoff_candidates": None,
        "prices": None,
    },
    "images": {
        "provider": "gemini",
        "model": None,
        "price_per_image_usd": None,
        "output_px": 1200,
        "variations_per_role": 2,
    },
    "publishing": {"unhide_enabled": False, "enabled_by": None, "enabled_at": None},
    "agent": {
        "max_revision_rounds": 3,
        "reminder_after_days": 3,
        "expire_after_days": 14,
        "approval_token_ttl_hours": 72,
    },
}


def key_paths(node: Any, prefix: str = "") -> list[str]:
    """Every mapping key path in the fixture (list elements are not keys)."""
    paths: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.append(path)
            paths.extend(key_paths(value, path))
    return paths


def without(data: dict[str, Any], path: str) -> dict[str, Any]:
    copied = copy.deepcopy(data)
    node = copied
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    del node[last]
    return copied


def with_value(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    copied = copy.deepcopy(data)
    node = copied
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    return copied


@pytest.fixture
def store_data() -> dict[str, Any]:
    return copy.deepcopy(VALID_STORE)
