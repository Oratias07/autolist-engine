"""Send email from the agent mailbox: plain text plus the same text as RTL HTML.

Recipients are limited to the owner address in ``store.yaml`` and, for alerts, the
developer contact. Bodies come only from reply templates. Replies stay in the thread
(``threadId``, ``In-Reply-To``, ``References``). Attachments are images the system made
(JPEG only, size-limited), never files received from outside.
"""

from __future__ import annotations

import base64
import html
import time
import uuid
from collections.abc import Callable
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
    mime["Subject"] = msg.subject or reply.subject
    # our own send id: after a send timeout the sent mail is found by it (live 22.11.2026).
    # Not the Message-ID: Gmail replaces that one (observed, alert test 22.11.2026).
    mime[SEND_ID_HEADER] = uuid.uuid4().hex
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


SEND_ID_HEADER = "X-Autolist-Send-Id"
FIND_SENT_WAITS_S = (2.0, 5.0, 10.0)  # Gmail needs a moment to list a sent mail


class MetadataUnavailable(RuntimeError):
    """A mail was sent, but Gmail never answered the read-back of its thread."""


class GmailSender:
    """``service`` is a Gmail API client (googleapiclient) authorised as the agent mailbox."""

    def __init__(
        self,
        service: Any,
        config: EmailChannelConfig,
        templates_dir: Path | None = None,
        extra_recipients: frozenset[str] = frozenset(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._service = service
        self._config = config
        self._templates_dir = templates_dir
        self._extra = extra_recipients
        self._sleep = sleep

    def deliver(self, msg: OutboundMessage) -> SentMessage:
        """Send, then read back the thread id and the Message-ID header (owner replies quote it)."""
        gmail_id = self.send(msg)
        meta = self._meta(gmail_id)
        if meta is None:
            # sent, but Gmail did not answer the read-back (YTS-1007, 22.11.2026). The mail is
            # out: when its thread is known, never fail a sent mail. Its Message-ID stays
            # unknown (""), so the next email answers the owner's message instead.
            if msg.thread_id is None:
                raise MetadataUnavailable(f"sent {gmail_id}, but its thread is unknown")
            log.warning("sent; read-back failed", extra={"template": msg.template})
            return SentMessage(gmail_id=gmail_id, thread_id=msg.thread_id, rfc_message_id="")
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
        send_id = str(mime[SEND_ID_HEADER])
        body: dict[str, str] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if msg.thread_id:
            body["threadId"] = msg.thread_id
        try:
            sent = self._service.users().messages().send(userId="me", body=body).execute()
            message_id = str(sent["id"])
        except (TimeoutError, OSError) as exc:
            # the reply timed out, but the mail may well be sent (YTS-1007, 22.11.2026). Never
            # send twice: look for it in Sent by its Message-ID; only if it is not there, fail.
            found = self._find_sent(send_id, msg.to)
            if found is None:
                raise
            log.warning(
                "send timed out but the mail was sent",
                extra={"template": msg.template, "error": type(exc).__name__},
            )
            message_id = found
        log.info("mail sent", extra={"template": msg.template, "gmail_id": message_id})
        return message_id

    def _meta(self, gmail_id: str) -> dict[str, Any] | None:
        for attempt, wait_s in enumerate((0.0, *FIND_SENT_WAITS_S)):
            if attempt:
                self._sleep(wait_s)
            try:
                res: dict[str, Any] = (
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=gmail_id,
                        format="metadata",
                        metadataHeaders=["Message-ID"],
                    )
                    .execute()
                )
                return res
            except (TimeoutError, OSError):
                continue
        return None

    def _find_sent(self, send_id: str, to: str) -> str | None:
        """The sent mail with our send id, among the newest sent to the same recipient."""
        for wait_s in FIND_SENT_WAITS_S:
            self._sleep(wait_s)
            try:
                found = (
                    self._service.users()
                    .messages()
                    .list(userId="me", q=f"in:sent to:{to} newer_than:1d", maxResults=10)
                    .execute()
                )
                for m in found.get("messages", []) or []:
                    meta = (
                        self._service.users()
                        .messages()
                        .get(
                            userId="me",
                            id=str(m["id"]),
                            format="metadata",
                            metadataHeaders=[SEND_ID_HEADER],
                        )
                        .execute()
                    )
                    headers = meta.get("payload", {}).get("headers", [])
                    if any(
                        h["name"].lower() == SEND_ID_HEADER.lower() and h["value"] == send_id
                        for h in headers
                    ):
                        return str(m["id"])
            except (TimeoutError, OSError):
                continue
        return None
