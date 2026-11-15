"""Which store-repo texts may be used (prompts and reply templates). Fail closed.

A text is used only when its front matter says ``approved`` or ``approved_dev`` (approved by
the developer for the dev stage). Anything else, including a missing status, is refused.
``AUTOLIST_ALLOW_DRAFTS=1`` lets a live eval run read a ``draft`` (never ``not-written``);
the service refuses to start while it is set (``orchestrator.main``).
"""

from __future__ import annotations

import os

USABLE = frozenset({"approved", "approved_dev"})
ALLOW_DRAFTS_ENV = "AUTOLIST_ALLOW_DRAFTS"


def drafts_allowed() -> bool:
    return os.environ.get(ALLOW_DRAFTS_ENV) == "1"


def refusal(status: object) -> str | None:
    """None when usable, else the reason it is refused."""
    if status in USABLE:
        return None
    if status == "draft" and drafts_allowed():
        return None
    return f"status is {status!r}; only {sorted(USABLE)} are used"
