"""Send email from the agent mailbox: plain text plus the same text as RTL HTML.

Recipients are limited to the owner address in ``store.yaml`` and, for alerts, the
developer contact. Bodies come only from reply templates. Replies stay in the thread
(``threadId``, ``In-Reply-To``, ``References``). Attachments are images the system made
(JPEG only, size-limited), never files received from outside.
"""

from __future__ import annotations

import base64
import html
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from autolist.channels.errors import AttachmentRejected, RecipientNotAllowed
from autolist.channels.templates import render_reply
from autolist.core.logging import get_logger
from autolist.core.models import EmailChannelConfig, OutboundMessage, SentMessage

log = get_logger(__name__)


MAX_OUT_ATTACHMENT_BYTES = 5_000_000
MAX_OUT_ATTACHMENTS = 4


def build_mime(
    msg: OutboundMessage,
    config: EmailChannelConfig,
    templates_dir: Path | None = None,
    extra_recipients: frozenset[str] = frozenset(),
) -> EmailMessage:
    allowed = {config.owner_address.lower(), *(r.lower() for r in extra_recipients)}
    if msg.to.lower() not in allowed:
        raise RecipientNotAllowed(f"recipient not allowed: {msg.to}")
    if len(msg.attachments) > MAX_OUT_ATTACHMENTS:
        raise AttachmentRejected("too many attachments")
    for att in msg.attachments:
        if att.mime_type != "image/jpeg" or att.data is None:
            raise AttachmentRejected(
                f"only JPEG images made by the system are sent: {att.filename}"
            )
        if len(att.data) > MAX_OUT_ATTACHMENT_BYTES:
            raise AttachmentRejected(f"attachment too large: {att.filename}")
    reply = render_reply(msg.template, msg.variables, templates_dir)
    mime = EmailMessage()
    mime["From"] = config.agent_mailbox
    mime["To"] = msg.to
    mime["Subject"] = reply.subject
    if msg.in_reply_to:
        mime["In-Reply-To"] = msg.in_reply_to
        mime["References"] = msg.in_reply_to
    mime.set_content(reply.body, charset="utf-8")
    mime.add_alternative(_html(reply.body, config), subtype="html", charset="utf-8")
    for att in msg.attachments:
        if att.data is not None:  # checked above; keeps the type checker exact
            mime.add_attachment(att.data, maintype="image", subtype="jpeg", filename=att.filename)
    return mime


def _html(body: str, config: EmailChannelConfig) -> str:
    """The same text as HTML, so mail clients render the store's direction (RTL for Hebrew).

    Text is escaped; no markup from templates or variables is passed through.
    """
    paragraphs = "".join(
        f"<p>{'<br>'.join(html.escape(line) for line in block.splitlines())}</p>"
        for block in body.strip().split("\n\n")
    )
    align = "right" if config.text_direction == "rtl" else "left"
    return (
        f'<!doctype html><html lang="{html.escape(config.language)}" '
        f'dir="{config.text_direction}"><body dir="{config.text_direction}" '
        f'style="text-align:{align}">{paragraphs}</body></html>'
    )


class GmailSender:
    """``service`` is a Gmail API client (googleapiclient) authorised as the agent mailbox."""

    def __init__(
        self,
        service: Any,
        config: EmailChannelConfig,
        templates_dir: Path | None = None,
        extra_recipients: frozenset[str] = frozenset(),
    ) -> None:
        self._service = service
        self._config = config
        self._templates_dir = templates_dir
        self._extra = extra_recipients

    def deliver(self, msg: OutboundMessage) -> SentMessage:
        """Send, then read back the thread id and the Message-ID header (owner replies quote it)."""
        gmail_id = self.send(msg)
        meta = (
            self._service.users()
            .messages()
            .get(userId="me", id=gmail_id, format="metadata", metadataHeaders=["Message-ID"])
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])
        }
        return SentMessage(
            gmail_id=gmail_id,
            thread_id=str(meta["threadId"]),
            rfc_message_id=headers.get("message-id", ""),
        )

    def send(self, msg: OutboundMessage) -> str:
        mime = build_mime(msg, self._config, self._templates_dir, self._extra)
        body: dict[str, str] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if msg.thread_id:
            body["threadId"] = msg.thread_id
        sent = self._service.users().messages().send(userId="me", body=body).execute()
        message_id = str(sent["id"])
        log.info("mail sent", extra={"template": msg.template, "gmail_id": message_id})
        return message_id
