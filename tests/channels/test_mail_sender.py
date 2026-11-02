from __future__ import annotations

import base64
import email

import pytest

from autolist.channels.email.mail_sender import GmailSender, build_mime
from autolist.channels.errors import AttachmentRejected, RecipientNotAllowed
from autolist.core.models import Attachment, EmailChannelConfig, OutboundMessage
from tests.channels.conftest import REPLIES, FakeGmail

VARS = {"month_spend": "31.20", "monthly_cap": "60"}


def _msg(**over: object) -> OutboundMessage:
    base: dict[str, object] = {
        "channel": "email",
        "to": "owner@example.com",
        "template": "cost_alert",
        "variables": VARS,
    }
    base.update(over)
    return OutboundMessage.model_validate(base)


def test_sends_template_with_signature_to_owner(email_cfg: EmailChannelConfig) -> None:
    gmail = FakeGmail({"send": {"id": "g-1"}})
    sender = GmailSender(gmail, email_cfg, REPLIES)
    assert sender.send(_msg(thread_id="t-9", in_reply_to="<m@example.com>")) == "g-1"
    _, kw = gmail.calls[0]
    assert kw["userId"] == "me" and kw["body"]["threadId"] == "t-9"
    raw = base64.urlsafe_b64decode(kw["body"]["raw"])
    parsed = email.message_from_bytes(raw)
    assert parsed["To"] == "owner@example.com" and parsed["From"] == "agent@example.com"
    assert parsed["In-Reply-To"] == "<m@example.com>"
    parts = {p.get_content_type(): p for p in parsed.walk() if not p.is_multipart()}
    text = parts["text/plain"].get_payload(decode=True).decode("utf-8")
    assert "Example Store agent (automated)" in text
    page = parts["text/html"].get_payload(decode=True).decode("utf-8")
    assert 'dir="rtl"' in page and 'lang="he"' in page and "text-align:right" in page


def test_any_other_recipient_is_refused(email_cfg: EmailChannelConfig) -> None:
    gmail = FakeGmail({"send": {"id": "x"}})
    with pytest.raises(RecipientNotAllowed):
        GmailSender(gmail, email_cfg, REPLIES).send(_msg(to="someone@example.com"))
    assert gmail.calls == []


def test_no_thread_and_attachments_refused(email_cfg: EmailChannelConfig) -> None:
    mime = build_mime(_msg(), email_cfg, REPLIES)
    assert mime["In-Reply-To"] is None
    att = Attachment(filename="a.pdf", mime_type="application/pdf", size_bytes=1)
    with pytest.raises(AttachmentRejected):
        build_mime(_msg(attachments=[att]), email_cfg, REPLIES)


def test_logs_never_contain_the_body(
    email_cfg: EmailChannelConfig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    GmailSender(FakeGmail({"send": {"id": "g-2"}}), email_cfg, REPLIES).send(_msg())
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "31.20" not in logged and "Example Store agent" not in logged
    assert "cost_alert" in logged


def test_html_part_escapes_variables(email_cfg: EmailChannelConfig) -> None:
    mime = build_mime(_msg(variables={**VARS, "month_spend": "<b>1</b>"}), email_cfg, REPLIES)
    page = next(p for p in mime.walk() if p.get_content_type() == "text/html").get_content()
    assert "<b>1</b>" not in page and "&lt;b&gt;1&lt;/b&gt;" in page
