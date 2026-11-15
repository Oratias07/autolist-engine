"""Exact reply commands (decision DEV 15.11.2026). Pure.

A reply is a command only when its whole text, after the reader cut the quote and signature,
equals one of the store's command words. Normalizing removes only what does not change the
meaning: direction marks, extra whitespace, and one trailing period. No model is involved, so
"לא מאשר" or "מאשר אבל..." can never become an approval.
"""

from __future__ import annotations

import re
from typing import Literal, cast

Command = Literal["approve", "cancel", "image_1", "image_2"]
_BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _SPACE.sub(" ", _BIDI.sub("", text)).strip()
    return text[:-1].rstrip() if text.endswith(".") else text


def parse_command(text: str, words: dict[str, str]) -> Command | None:
    reply = normalize(text)
    for command, word in words.items():
        if reply == normalize(word):
            return cast(Command, command)
    return None


def mentions(text: str, word: str) -> bool:
    """The word appears in the reply (used only to refuse, never to approve)."""
    return normalize(word) in normalize(text)
