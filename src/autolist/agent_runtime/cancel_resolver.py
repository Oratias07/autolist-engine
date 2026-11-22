"""Which products a free-wording cancel asks for (resolve_cancel v1, DEV 22.11.2026).

Called only when the reply contains a cancel word (``CANCEL_WORD``), so a reply without one
never cancels anything. The model returns numbers from the given list. The code keeps only
numbers in that list. Approval is never resolved here: only an exact "מאשר" approves.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from autolist.core.models import ChannelMessage
from autolist.llm.client import LLMClient, LLMRequest

CANCEL_WORD = re.compile("בטל|ביטול")
MAX_OUTPUT_TOKENS = 300


class ResolvedCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel: bool
    products: list[int]


def has_cancel_word(text: str) -> bool:
    return CANCEL_WORD.search(text) is not None


def resolve_cancel(
    msg: ChannelMessage, products: list[str], *, llm: LLMClient, job_id: str
) -> list[int]:
    """0-based indexes of ``products`` to cancel. [] when the reply is not a cancel.

    Raises ``ValueError`` (``CancelUnclear``) when it is a cancel but the products are not
    clear."""
    listing = "\n".join(f"{n}. {line}" for n, line in enumerate(products, 1))
    result = llm.complete(
        LLMRequest(
            prompt="resolve_cancel",
            variables={"reply_text": msg.text, "products": listing},
            output_schema=ResolvedCancel,
            model_role="classify",
            job_id=job_id,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    )
    out = result.parsed
    if not isinstance(out, ResolvedCancel):
        raise TypeError("resolve_cancel returned no parsed result")
    if not out.cancel:
        return []
    chosen = sorted({n - 1 for n in out.products if 1 <= n <= len(products)})
    if not chosen:
        raise CancelUnclear("a cancel, but which products is not clear")
    return chosen


class CancelUnclear(ValueError):
    pass
