"""Gmail push (users.watch -> Pub/Sub) and history-based fetching of new messages."""

from __future__ import annotations

from typing import Any

WATCH_LABELS = ["INBOX"]
_HEADER_NAMES = ["From", "Subject", "Message-ID", "Authentication-Results", "Date"]


def start_watch(service: Any, topic_path: str) -> dict[str, str]:
    """Start or renew the watch. Returns Gmail's ``historyId`` and ``expiration`` (ms)."""
    res = (
        service.users()
        .watch(userId="me", body={"topicName": topic_path, "labelIds": WATCH_LABELS})
        .execute()
    )
    return {"historyId": str(res["historyId"]), "expiration": str(res["expiration"])}


def new_message_ids(service: Any, start_history_id: str) -> tuple[list[str], str]:
    """Message ids added since ``start_history_id`` (all pages) and the latest history id."""
    ids: list[str] = []
    latest = start_history_id
    page: str | None = None
    while True:
        res = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page,
            )
            .execute()
        )
        for h in res.get("history", []):
            for added in h.get("messagesAdded", []):
                mid = added["message"]["id"]
                if mid not in ids:
                    ids.append(mid)
        latest = str(res.get("historyId", latest))
        page = res.get("nextPageToken")
        if not page:
            return ids, latest


def message_headers(service: Any, message_id: str) -> tuple[list[tuple[str, str]], str]:
    """Headers in message order (top first) and the Gmail thread id. No body is fetched."""
    res = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=_HEADER_NAMES)
        .execute()
    )
    headers = [(h["name"], h["value"]) for h in res.get("payload", {}).get("headers", [])]
    return headers, str(res["threadId"])
