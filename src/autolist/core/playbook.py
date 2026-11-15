"""Read the store playbook (store repo, playbook/playbook.yaml). Read-only."""

from __future__ import annotations

from typing import Any

import yaml

from autolist.core.config import store_dir
from autolist.core.errors import ConfigMissing


def load_playbook() -> dict[str, Any]:
    path = store_dir() / "playbook" / "playbook.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigMissing("playbook")
    return data


def require(playbook: dict[str, Any], path: str) -> Any:
    """Value at a dotted path; ``ConfigMissing`` when absent or null (never a default)."""
    node: Any = playbook
    for part in path.split("."):
        if not isinstance(node, dict) or node.get(part) is None:
            raise ConfigMissing(f"playbook.{path}")
        node = node[part]
    return node


def load_glossary() -> dict[str, Any]:
    path = store_dir() / "playbook" / "glossary.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigMissing("glossary")
    return data


def load_voice_principles() -> str:
    """The voice principles section of the store's docs/voice.md."""
    text = (store_dir() / "docs" / "voice.md").read_text(encoding="utf-8")
    start, end = "**עקרונות הקול:**", "**מילון מונחים.**"
    if start not in text or end not in text:
        raise ConfigMissing("docs/voice.md voice principles section")
    return text[text.index(start) : text.index(end)].strip()
