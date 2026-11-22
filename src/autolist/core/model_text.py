"""Clean text that a model wrote, or that came from an email, before it reaches an email of the
agent or the store (security review 22.11.2026, L6/L7).

Model output follows text from web pages, invoices and emails, which are data and may carry
instructions. A question in the agent's own email is trusted by the owner, so it never carries
a link or an address; and no title carries markup, control or direction-override characters.
Lengths are bounded by each prompt's ``max_output_tokens`` and by the playbook, not here.
"""

from __future__ import annotations

import re

# C0 controls (tab and line feed aside), DEL, C1 controls, and the bidi embeddings, overrides
# and isolates that can make text read differently than it is stored
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_MARKUP = re.compile(r"[<>]")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_DOMAIN = re.compile(r"(?i)(?<![\w@.])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?(?![\w.])")


def _spaces(text: str) -> str:
    return " ".join(text.split())


def clean_title(text: str) -> str:
    """A product title: no control characters, no angle brackets, single spaces."""
    return _spaces(_MARKUP.sub("", _CONTROL.sub("", text)))


def clean_question(text: str) -> str:
    """A question to the owner: as a title, and without links, web addresses or emails."""
    text = _CONTROL.sub("", text)
    for pattern in (_URL, _EMAIL, _DOMAIN):
        text = pattern.sub("", text)
    return _spaces(_MARKUP.sub("", text))
