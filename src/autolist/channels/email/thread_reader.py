"""The owner's earlier messages in a thread, for a reply that answers the agent's question.

When the agent asked a clarifying question and opened no job, the owner's answer alone does
not carry the products. The orchestrator reads the whole thread so the extraction sees the
first email and every answer together (DEV 15.11.2026: the owner never has to resend).

Each message goes through ``fetch_message`` again: sender allowlist, DKIM and DMARC, and the
attachment size limit. Messages the agent sent (label ``SENT``) are skipped, and so is any
message that fails the sender check. At most ``MAX_MESSAGES`` owner messages are read.
"""

from __future__ import annotations

from typing import Any

from autolist.channels.email.reader import fetch_message
from autolist.channels.errors import SenderRejected
from autolist.core.models import ChannelMessage, EmailChannelConfig

MAX_MESSAGES = 5


def thread_has_agent_message(service: Any, thread_id: str) -> bool:
    res = service.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
    return any("SENT" in (m.get("labelIds") or []) for m in res.get("messages", []) or [])


def owner_messages(
    service: Any, thread_id: str, *, config: EmailChannelConfig, max_bytes: int
) -> list[ChannelMessage]:
    """The owner's accepted messages in the thread, oldest first (the newest included)."""
    res = service.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
    out: list[ChannelMessage] = []
    for m in res.get("messages", []) or []:
        if "SENT" in (m.get("labelIds") or []):
            continue
        try:
            out.append(fetch_message(service, str(m["id"]), config=config, max_bytes=max_bytes))
        except SenderRejected:
            continue
    return out[-MAX_MESSAGES:]
