"""channels.email.reader: full message -> ChannelMessage."""
# ruff: noqa: RUF001  (Hebrew geresh in realistic Gmail quote lines)

from __future__ import annotations

import base64
from typing import Any

import pytest

from autolist.channels.email.reader import fetch_message, html_to_text, strip_quoted
from autolist.channels.errors import SenderRejected
from autolist.core.models import EmailChannelConfig
from tests.channels.conftest import FakeGmail

AR = (
    "mx.google.com; dkim=pass header.i=@example.com header.s=s1 header.b=abc; "
    "dmarc=pass (p=NONE) header.from=example.com"
)


def _b(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _message(parts: list[dict[str, Any]], sender: str, ar: str | None = AR) -> dict[str, Any]:
    headers = [{"name": "From", "value": f"Owner <{sender}>"}, {"name": "Subject", "value": "מוצר"}]
    if ar:
        headers.insert(0, {"name": "Authentication-Results", "value": ar})
    return {
        "threadId": "t1",
        "internalDate": "1794730000000",
        "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
    }


def _text(body: str, mime: str = "text/plain", charset: str = "UTF-8") -> dict[str, Any]:
    return {
        "mimeType": mime,
        "filename": "",
        "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}],
        "body": {"data": _b(body.encode(charset if charset.lower() != "bogus" else "utf-8"))},
    }


def test_plain_text_subject_and_attachment(email_cfg: EmailChannelConfig) -> None:
    owner = email_cfg.allowed_senders[0]
    msg = _message(
        [
            _text('כיסא M2192, 520 ש"ח\n\nבתאריך יום א׳ מאת מישהו <a@b>:\n> ישן'),
            {
                "mimeType": "image/jpeg",
                "filename": "a.jpg",
                "body": {"attachmentId": "X", "size": 3},
            },
        ],
        owner,
    )
    g = FakeGmail({"get": [msg], "attachments.get": [{"data": _b(b"abc")}]})
    out = fetch_message(g, "m1", config=email_cfg, max_bytes=100)
    assert out.text == 'כיסא M2192, 520 ש"ח'
    assert out.subject == "מוצר"
    assert out.sender == owner
    assert out.authenticated is True
    assert out.thread_id == "t1"
    assert [(a.filename, a.data) for a in out.attachments] == [("a.jpg", b"abc")]
    assert g.calls[0][1]["format"] == "full"


def test_oversized_attachment_is_never_downloaded(email_cfg: EmailChannelConfig) -> None:
    msg = _message(
        [
            _text("x"),
            {
                "mimeType": "image/png",
                "filename": "big.png",
                "body": {"attachmentId": "Y", "size": 999},
            },
        ],
        email_cfg.allowed_senders[0],
    )
    g = FakeGmail({"get": [msg]})
    out = fetch_message(g, "m1", config=email_cfg, max_bytes=100)
    assert out.attachments[0].data is None
    assert [c[0] for c in g.calls] == ["get"]


def test_html_only_body_becomes_text_without_scripts_or_quotes(
    email_cfg: EmailChannelConfig,
) -> None:
    html = (
        "<html><head><style>p{}</style></head><body><p>כיסא&nbsp;T8103</p>"
        "<script>alert(1)</script><div class='gmail_quote'>ישן</div></body></html>"
    )
    msg = _message([_text(html, "text/html")], email_cfg.allowed_senders[0])
    out = fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)
    assert out.text == "כיסא\xa0T8103"


def test_unknown_charset_falls_back(email_cfg: EmailChannelConfig) -> None:
    msg = _message([_text("שלום", charset="bogus")], email_cfg.allowed_senders[0])
    out = fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)
    assert out.text == "שלום"


@pytest.mark.parametrize(("sender", "ar"), [("stranger@elsewhere.example", AR), (None, None)])
def test_unverified_sender_is_refused(
    email_cfg: EmailChannelConfig, sender: str | None, ar: str | None
) -> None:
    msg = _message([_text("x")], sender or email_cfg.allowed_senders[0], ar)
    with pytest.raises(SenderRejected):
        fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)


def test_strip_quoted_variants() -> None:
    assert strip_quoted("שלום\nOn Sun, Nov 15 2026 Owner wrote:\n> old") == "שלום"
    assert strip_quoted("‏תשובה\n‫בתאריך יום א׳, 15 בנוב׳ מאת X <x@y>:‬\n> a") == "‏תשובה"
    assert strip_quoted("טקסט\n> ציטוט\n>\n") == "טקסט"
    assert strip_quoted("כתבתי: מחיר 5") == "כתבתי: מחיר 5"


def test_html_to_text_keeps_line_breaks() -> None:
    assert html_to_text("<p>a</p><p>b<br>c</p>").split() == ["a", "b", "c"]
