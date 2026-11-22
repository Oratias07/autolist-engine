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


def test_no_thread_and_non_jpeg_attachments_refused(email_cfg: EmailChannelConfig) -> None:
    mime = build_mime(_msg(), email_cfg, REPLIES)
    assert mime["In-Reply-To"] is None
    att = Attachment(filename="a.pdf", mime_type="application/pdf", size_bytes=1, data=b"x")
    with pytest.raises(AttachmentRejected):
        build_mime(_msg(attachments=[att]), email_cfg, REPLIES)
    big = Attachment(filename="b.jpg", mime_type="image/jpeg", size_bytes=1, data=b"x" * 5_000_001)
    with pytest.raises(AttachmentRejected, match="large"):
        build_mime(_msg(attachments=[big]), email_cfg, REPLIES)


def test_jpeg_attachment_is_added(email_cfg: EmailChannelConfig) -> None:
    att = Attachment(filename="image-2.jpg", mime_type="image/jpeg", size_bytes=3, data=b"abc")
    mime = build_mime(_msg(attachments=[att]), email_cfg, REPLIES)
    files = [p for p in mime.iter_attachments()]
    assert [(p.get_filename(), p.get_content()) for p in files] == [("image-2.jpg", b"abc")]


def test_extra_recipient_only_when_allowed(email_cfg: EmailChannelConfig) -> None:
    msg = _msg(to="dev@example.com")
    with pytest.raises(RecipientNotAllowed):
        build_mime(msg, email_cfg, REPLIES)
    mime = build_mime(msg, email_cfg, REPLIES, frozenset({"dev@example.com"}))
    assert mime["To"] == "dev@example.com"


def test_deliver_reads_back_thread_and_message_id(email_cfg: EmailChannelConfig) -> None:
    meta = {"threadId": "t9", "payload": {"headers": [{"name": "Message-Id", "value": "<x@y>"}]}}
    g = FakeGmail({"send": {"id": "g-3"}, "get": [meta]})
    sent = GmailSender(g, email_cfg, REPLIES).deliver(_msg())
    assert (sent.gmail_id, sent.thread_id, sent.rfc_message_id) == ("g-3", "t9", "<x@y>")


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


def test_a_thread_subject_replaces_the_template_subject(email_cfg: EmailChannelConfig) -> None:
    """DEV 22.11.2026: every email of a job keeps the thread's one subject."""
    mime = build_mime(_msg(subject="Re: 2 מוצרים"), email_cfg, REPLIES)
    assert mime["Subject"] == "Re: 2 מוצרים"
    assert build_mime(_msg(), email_cfg, REPLIES)["Subject"] != "Re: 2 מוצרים"


class _FixedId:
    hex = "send-id-1"


def _meta_with(send_id: str) -> dict[str, object]:
    return {"payload": {"headers": [{"name": "X-Autolist-Send-Id", "value": send_id}]}}


def test_every_mail_carries_a_send_id(email_cfg: EmailChannelConfig) -> None:
    mime = build_mime(_msg(), email_cfg, REPLIES)
    assert len(str(mime["X-Autolist-Send-Id"])) == 32


def test_a_send_that_timed_out_but_was_sent_is_found_and_never_sent_twice(
    email_cfg: EmailChannelConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YTS-1007 (22.11.2026). Gmail replaces the Message-ID (alert test 22.11), so the mail
    is found by our own send id among the newest sent to the same recipient."""
    monkeypatch.setattr("autolist.channels.email.mail_sender.uuid.uuid4", lambda: _FixedId)
    gmail = FakeGmail(
        {
            "send": [TimeoutError("The read operation timed out")],
            "history.list": [{"messages": []}, {"messages": [{"id": "g-x"}, {"id": "g-found"}]}],
            "get": [_meta_with("other"), _meta_with("send-id-1")],
        }
    )
    waits: list[float] = []
    sender = GmailSender(gmail, email_cfg, REPLIES, sleep=waits.append)
    assert sender.send(_msg()) == "g-found"
    assert [c[0] for c in gmail.calls].count("send") == 1
    assert waits == [2.0, 5.0]
    query = next(kw["q"] for name, kw in gmail.calls if name == "history.list")
    assert query == "in:sent to:owner@example.com newer_than:1d"


def test_a_send_that_timed_out_and_is_not_in_sent_still_fails(
    email_cfg: EmailChannelConfig,
) -> None:
    gmail = FakeGmail({"send": [TimeoutError("timed out")], "history.list": [{"messages": []}] * 3})
    sender = GmailSender(gmail, email_cfg, REPLIES, sleep=lambda _s: None)
    with pytest.raises(TimeoutError):
        sender.send(_msg())
    assert [c[0] for c in gmail.calls].count("send") == 1


def test_a_read_back_that_keeps_timing_out_does_not_fail_a_sent_mail(
    email_cfg: EmailChannelConfig,
) -> None:
    """YTS-1007 (22.11.2026): the preview was sent and the read-back timed out."""
    gmail = FakeGmail({"send": {"id": "g-1"}, "get": [TimeoutError("timed out")] * 4})
    sender = GmailSender(gmail, email_cfg, REPLIES, sleep=lambda _s: None)
    sent = sender.deliver(_msg(thread_id="t-9"))
    assert (sent.gmail_id, sent.thread_id, sent.rfc_message_id) == ("g-1", "t-9", "")
    assert [c[0] for c in gmail.calls].count("send") == 1


def test_a_read_back_that_recovers_is_used(email_cfg: EmailChannelConfig) -> None:
    meta = {"threadId": "t-new", "payload": {"headers": [{"name": "Message-ID", "value": "<x@y>"}]}}
    gmail = FakeGmail({"send": {"id": "g-1"}, "get": [TimeoutError("t"), meta]})
    sent = GmailSender(gmail, email_cfg, REPLIES, sleep=lambda _s: None).deliver(_msg())
    assert (sent.thread_id, sent.rfc_message_id) == ("t-new", "<x@y>")


def test_a_new_thread_whose_read_back_never_answers_raises(email_cfg: EmailChannelConfig) -> None:
    from autolist.channels.email.mail_sender import MetadataUnavailable

    gmail = FakeGmail({"send": {"id": "g-1"}, "get": [TimeoutError("t")] * 4})
    with pytest.raises(MetadataUnavailable):
        GmailSender(gmail, email_cfg, REPLIES, sleep=lambda _s: None).deliver(_msg())
