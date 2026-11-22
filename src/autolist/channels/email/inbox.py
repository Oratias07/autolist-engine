"""Turn a Gmail push into the list of newly arrived, sender-verified messages.

Reads only message headers. Every new message id is claimed first, so a repeated push never
yields the same message twice. Messages that fail sender verification are recorded and
dropped without any reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autolist.channels.email.gmail_watch import message_headers, new_message_ids, start_watch
from autolist.channels.email.sender_auth import verify_sender
from autolist.core.logging import get_logger
from autolist.core.models import EmailChannelConfig
from autolist.core.state_store import ChannelStateStore, ProcessedMessages

log = get_logger(__name__)


@dataclass(frozen=True)
class AcceptedMessage:
    message_id: str  # Gmail id
    thread_id: str
    rfc_message_id: str  # the Message-ID header, for In-Reply-To


_AUTO_PRECEDENCE = frozenset({"bulk", "junk", "list", "auto_reply"})


def is_automatic(headers: list[tuple[str, str]]) -> bool:
    """An auto-reply or bulk mail (RFC 3834 and common headers): never answered."""
    for name, value in headers:
        n, v = name.lower(), value.strip().lower()
        if n == "auto-submitted" and v and v != "no":
            return True
        if n == "precedence" and v in _AUTO_PRECEDENCE:
            return True
        if n in ("x-autoreply", "x-autorespond"):
            return True
    return False


def poll_accepted(
    service: Any,
    config: EmailChannelConfig,
    state: ChannelStateStore,
    processed: ProcessedMessages,
    now: datetime,
) -> tuple[list[AcceptedMessage], str | None]:
    """The new accepted messages and the history id to move the cursor to.

    The caller moves the cursor only after every accepted message is queued. On any error,
    every claim made here that is not finished is released, so the next push retries it
    (security review 22.11.2026, L4: a timeout must never lose an owner email)."""
    cursor = state.history_id()
    if cursor is None:
        # no cursor yet: the watch was never started by this service, nothing can be listed
        log.warning("gmail push ignored: no history cursor (run renew-watch first)")
        return [], None
    ids, latest = new_message_ids(service, cursor)
    accepted: list[AcceptedMessage] = []
    current: str | None = None
    try:
        for mid in ids:
            if not processed.claim(mid, now, "seen"):
                continue
            current = mid
            headers, thread_id = message_headers(service, mid)
            current = None
            verdict = verify_sender(headers, config)
            if not verdict.accepted:
                processed.set_outcome(mid, "rejected", reason=verdict.reason)
                log.info("message rejected", extra={"gmail_id": mid, "reason": verdict.reason})
                continue
            if is_automatic(headers):
                processed.set_outcome(mid, "auto_reply")
                log.info("automatic reply dropped", extra={"gmail_id": mid})
                continue
            rfc_id = next((v for n, v in headers if n.lower() == "message-id"), "")
            processed.set_outcome(mid, "accepted", thread_id=thread_id)
            accepted.append(AcceptedMessage(mid, thread_id, rfc_id))
    except Exception:
        for mid in [m for m in [current] if m] + [a.message_id for a in accepted]:
            processed.release(mid)
        raise
    return accepted, latest


def renew_watch(service: Any, topic_path: str, state: ChannelStateStore) -> int:
    """Renew the Gmail watch and store its expiry. Returns the expiry (ms since epoch).

    The history cursor is set only when none is stored: pushes keep it current, and
    overwriting it here could skip a message that arrived just before the renewal.
    """
    watch = start_watch(service, topic_path)
    expires = int(watch["expiration"])
    state.set_watch_expires_ms(expires)
    if state.history_id() is None:
        state.set_history_id(watch["historyId"])
    return expires
