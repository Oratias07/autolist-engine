from __future__ import annotations

import base64
from typing import Any

import pytest

from autolist.channels.email.own_attachment import fetch_own_attachment
from autolist.channels.errors import AttachmentRejected, SenderRejected
from autolist.core.models import EmailChannelConfig
from tests.channels.conftest import FakeGmail

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 20


def _b(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _msg(cfg: EmailChannelConfig, **over: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "labelIds": ["SENT"],
        "payload": {
            "headers": [{"name": "From", "value": f"YTS <{cfg.agent_mailbox}>"}],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": _b(b"hi")}},
                {
                    "mimeType": "image/jpeg",
                    "filename": "image-2.jpg",
                    "body": {"size": len(JPEG), "attachmentId": "a1"},
                },
            ],
        },
    }
    msg.update(over)
    return msg


def test_reads_back_own_image(email_cfg: EmailChannelConfig) -> None:
    g = FakeGmail({"get": [_msg(email_cfg)], "attachments.get": [{"data": _b(JPEG)}]})
    got = fetch_own_attachment(g, "m1", "image-2.jpg", config=email_cfg, max_bytes=100)
    assert got == JPEG
    assert g.calls[1] == ("attachments.get", {"userId": "me", "messageId": "m1", "id": "a1"})


def test_inbound_message_refused(email_cfg: EmailChannelConfig) -> None:
    g = FakeGmail({"get": [_msg(email_cfg, labelIds=["INBOX"])]})
    with pytest.raises(SenderRejected):
        fetch_own_attachment(g, "m1", "image-2.jpg", config=email_cfg, max_bytes=100)


def test_other_sender_refused(email_cfg: EmailChannelConfig) -> None:
    msg = _msg(email_cfg)
    msg["payload"]["headers"] = [{"name": "From", "value": "x@example.org"}]
    with pytest.raises(SenderRejected):
        fetch_own_attachment(
            FakeGmail({"get": [msg]}), "m1", "image-2.jpg", config=email_cfg, max_bytes=100
        )


@pytest.mark.parametrize(
    ("change", "data"),
    [
        ({"filename": "other.jpg"}, JPEG),
        ({"mimeType": "image/png"}, JPEG),
        ({"body": {"size": 500, "attachmentId": "a1"}}, JPEG),
        ({}, b"not a jpeg"),
        ({"body": {"size": 1}}, JPEG),
    ],
)
def test_refusals(email_cfg: EmailChannelConfig, change: dict[str, Any], data: bytes) -> None:
    msg = _msg(email_cfg)
    msg["payload"]["parts"][1].update(change)
    g = FakeGmail({"get": [msg], "attachments.get": [{"data": _b(data)}]})
    with pytest.raises(AttachmentRejected):
        fetch_own_attachment(g, "m1", "image-2.jpg", config=email_cfg, max_bytes=100)


def test_inline_data(email_cfg: EmailChannelConfig) -> None:
    msg = _msg(email_cfg)
    msg["payload"]["parts"][1]["body"] = {"size": len(JPEG), "data": _b(JPEG)}
    got = fetch_own_attachment(
        FakeGmail({"get": [msg]}), "m1", "image-2.jpg", config=email_cfg, max_bytes=100
    )
    assert got == JPEG
