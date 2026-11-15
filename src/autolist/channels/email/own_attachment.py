"""Read back an image the agent itself sent (decision #36, DEV 15.11.2026).

The generated image (image 2) is not stored anywhere: it waits in the preview email in the
agent mailbox until the owner approves. This reads it back from that email.

Refuses unless all hold, so it can never read an owner's or a stranger's message:
- the message carries Gmail's ``SENT`` label;
- its ``From`` address is the agent mailbox;
- the caller asks for an exact file name, and the part is ``image/jpeg`` within ``max_bytes``.
The caller passes only the gmail id recorded on the job version it is publishing.
"""

from __future__ import annotations

import base64
from email.utils import parseaddr
from typing import Any

from autolist.channels.errors import AttachmentRejected, SenderRejected
from autolist.core.models import EmailChannelConfig


def _walk(part: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [part]
    for child in part.get("parts", []) or []:
        parts.extend(_walk(child))
    return parts


def fetch_own_attachment(
    service: Any,
    gmail_id: str,
    filename: str,
    *,
    config: EmailChannelConfig,
    max_bytes: int,
) -> bytes:
    res = service.users().messages().get(userId="me", id=gmail_id, format="full").execute()
    if "SENT" not in (res.get("labelIds") or []):
        raise SenderRejected(f"{gmail_id}: not a message the agent sent")
    payload: dict[str, Any] = res.get("payload", {})
    sender = next(
        (h["value"] for h in payload.get("headers", []) if h["name"].lower() == "from"), ""
    )
    if parseaddr(sender)[1].lower() != config.agent_mailbox.lower():
        raise SenderRejected(f"{gmail_id}: not sent from the agent mailbox")

    matches = [p for p in _walk(payload) if p.get("filename") == filename]
    if len(matches) != 1:
        raise AttachmentRejected(f"{gmail_id}: expected one attachment {filename}")
    part = matches[0]
    if part.get("mimeType") != "image/jpeg":
        raise AttachmentRejected(f"{gmail_id}: {filename} is not image/jpeg")
    body = part.get("body", {})
    if int(body.get("size", 0)) > max_bytes:
        raise AttachmentRejected(f"{gmail_id}: {filename} too large")
    if "attachmentId" in body:
        got = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=gmail_id, id=body["attachmentId"])
            .execute()
        )
        raw = str(got["data"])
    elif "data" in body:
        raw = str(body["data"])
    else:
        raise AttachmentRejected(f"{gmail_id}: {filename} has no data")
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(data) > max_bytes or not data.startswith(b"\xff\xd8\xff"):
        raise AttachmentRejected(f"{gmail_id}: {filename} is not a JPEG within the limit")
    return data
