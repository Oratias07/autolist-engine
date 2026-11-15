"""Look up a message the agent itself sent: its thread and when it was sent. Read-only.

Used to decide whether an owner's reply answers an agent email sent at or after the latest
preview (decision DEV 15.11.2026). A message that is not ``SENT`` from the agent mailbox is
treated as not found, so an owner's or a stranger's message can never stand in for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from autolist.core.models import EmailChannelConfig


@dataclass(frozen=True)
class SentInfo:
    gmail_id: str
    thread_id: str
    sent_ms: int


def _info(res: dict[str, Any], config: EmailChannelConfig) -> SentInfo | None:
    if "SENT" not in (res.get("labelIds") or []):
        return None
    headers = res.get("payload", {}).get("headers", [])
    sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
    if parseaddr(sender)[1].lower() != config.agent_mailbox.lower():
        return None
    return SentInfo(str(res["id"]), str(res["threadId"]), int(res["internalDate"]))


def sent_by_gmail_id(service: Any, gmail_id: str, *, config: EmailChannelConfig) -> SentInfo | None:
    res = (
        service.users()
        .messages()
        .get(userId="me", id=gmail_id, format="metadata", metadataHeaders=["From"])
        .execute()
    )
    return _info(res, config)


def sent_by_rfc_id(service: Any, rfc_id: str, *, config: EmailChannelConfig) -> SentInfo | None:
    """The agent's sent message with this Message-ID, or None."""
    rfc = rfc_id.strip()
    if not (rfc.startswith("<") and rfc.endswith(">")) or any(c in rfc for c in ' "\\'):
        return None  # only a well-formed Message-ID goes into the search query
    found = (
        service.users()
        .messages()
        .list(userId="me", q=f"rfc822msgid:{rfc[1:-1]} in:sent", maxResults=5)
        .execute()
    )
    for m in found.get("messages", []) or []:
        info = sent_by_gmail_id(service, str(m["id"]), config=config)
        if info is not None:
            return info
    return None
