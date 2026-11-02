"""Send email from the agent mailbox. Plain text only in phase 0 (HTML preview: phase 3).

Recipients are limited to the owner address in ``store.yaml``. Bodies come only from reply
templates. Replies stay in the thread (``threadId``, ``In-Reply-To``, ``References``).
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
from autolist.core.models import EmailChannelConfig, OutboundMessage

log = get_logger(__name__)


def build_mime(
    msg: OutboundMessage, config: EmailChannelConfig, templates_dir: Path | None = None
) -> EmailMessage:
    if msg.to.lower() != config.owner_address.lower():
        raise RecipientNotAllowed(f"only the owner address may receive mail, not {msg.to}")
    if msg.attachments:
        raise AttachmentRejected("attachments are not sent in phase 0")
    reply = render_reply(msg.template, msg.variables, templates_dir)
    mime = EmailMessage()
    mime["From"] = config.agent_mailbox
    mime["To"] = config.owner_address
    mime["Subject"] = reply.subject
    if msg.in_reply_to:
        mime["In-Reply-To"] = msg.in_reply_to
        mime["References"] = msg.in_reply_to
    mime.set_content(reply.body, charset="utf-8")
    mime.add_alternative(_html(reply.body, config), subtype="html", charset="utf-8")
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
        self, service: Any, config: EmailChannelConfig, templates_dir: Path | None = None
    ) -> None:
        self._service = service
        self._config = config
        self._templates_dir = templates_dir

    def send(self, msg: OutboundMessage) -> str:
        mime = build_mime(msg, self._config, self._templates_dir)
        body: dict[str, str] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if msg.thread_id:
            body["threadId"] = msg.thread_id
        sent = self._service.users().messages().send(userId="me", body=body).execute()
        message_id = str(sent["id"])
        log.info("mail sent", extra={"template": msg.template, "gmail_id": message_id})
        return message_id
