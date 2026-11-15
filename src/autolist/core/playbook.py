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


def _labels(file: str) -> dict[str, str]:
    path = store_dir() / "agent" / file
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
    ):
        raise ConfigMissing(f"agent/{file} labels")
    return labels


def load_field_labels() -> dict[str, str]:
    """Hebrew labels of owner fields for agent emails (store repo, agent/field_labels.yaml)."""
    return _labels("field_labels.yaml")


def load_state_labels() -> dict[str, str]:
    """Hebrew job-state lines for status replies (store repo, agent/state_labels.yaml)."""
    return _labels("state_labels.yaml")


COMMANDS = (
    "approve",
    "cancel",
    "image_1",
    "image_2",
    "lifestyle_add",
    "lifestyle_remove",
    "no_sale",
)


def load_commands() -> dict[str, str]:
    """The exact reply command words (store repo, agent/commands.yaml); all of them, non-empty."""
    path = store_dir() / "agent" / "commands.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    words = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(words, dict) or set(words) != set(COMMANDS):
        raise ConfigMissing("agent/commands.yaml commands")
    if not all(isinstance(v, str) and v.strip() for v in words.values()):
        raise ConfigMissing("agent/commands.yaml commands (empty word)")
    return {k: str(v) for k, v in words.items()}
