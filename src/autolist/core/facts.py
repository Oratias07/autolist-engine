"""Owner-stated product facts that have no field of their own (key ``detail``). Pure.

Shared by intake (the first email) and agent_runtime (a reply). Each detail must quote the
message text exactly; an unquoted one is dropped, not asked about (it is optional data).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from autolist.core.models import ChannelMessage, JobFact

MAX_DETAILS = 12  # an owner email states a handful; more is not read
DETAIL = "detail"


class QuotedText(Protocol):
    @property
    def value(self) -> str | None: ...
    @property
    def quote(self) -> str | None: ...


def owner_details(
    details: Sequence[QuotedText], message: ChannelMessage, source: Literal["email", "reply"]
) -> list[JobFact]:
    out: list[JobFact] = []
    seen: set[str] = set()
    for d in details[:MAX_DETAILS]:
        value = (d.value or "").strip()
        if not value or not d.quote or d.quote not in message.text or value in seen:
            continue
        seen.add(value)
        out.append(
            JobFact(
                key=DETAIL,
                value=value,
                quote=d.quote,
                source=source,
                gmail_id=message.message_id,
            )
        )
    return out
