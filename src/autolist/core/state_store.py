"""Small persistent state for the service: channel cursor and handled message ids (Firestore).

Pub/Sub delivers at least once, so every incoming message id is claimed before it is handled.
A second claim of the same id fails, and the duplicate is skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from google.api_core.exceptions import AlreadyExists, Conflict


class ChannelStateStore:
    """One document per channel: the history cursor and the watch expiry."""

    def __init__(self, client: Any, channel: str, collection: str = "channel_state") -> None:
        self._doc = client.collection(collection).document(channel)

    def _read(self) -> dict[str, Any]:
        snap = self._doc.get()
        return (snap.to_dict() or {}) if snap.exists else {}

    def history_id(self) -> str | None:
        value = self._read().get("history_id")
        return str(value) if value else None

    def watch_expires_ms(self) -> int | None:
        value = self._read().get("watch_expires_ms")
        return int(value) if value else None

    def set_history_id(self, history_id: str) -> None:
        self._doc.set({**self._read(), "history_id": str(history_id)})

    def set_watch_expires_ms(self, expires_ms: int) -> None:
        self._doc.set({**self._read(), "watch_expires_ms": int(expires_ms)})


class ProcessedMessages:
    def __init__(self, client: Any, collection: str = "processed_messages") -> None:
        self._col = client.collection(collection)

    def claim(self, message_id: str, at: datetime, outcome: str) -> bool:
        """Record the message id. False means it was already claimed (a duplicate delivery)."""
        try:
            self._col.document(message_id).create({"at": at.isoformat(), "outcome": outcome})
        except (AlreadyExists, Conflict):
            return False
        return True

    def set_outcome(self, message_id: str, outcome: str, **details: str) -> None:
        doc = self._col.document(message_id)
        snap = doc.get()
        doc.set({**((snap.to_dict() or {}) if snap.exists else {}), "outcome": outcome, **details})
