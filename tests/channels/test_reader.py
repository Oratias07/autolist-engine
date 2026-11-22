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
            _text('כיסא Q9004, 520 ש"ח\n\nבתאריך יום א׳ מאת מישהו <a@b>:\n> ישן'),
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
    assert out.text == 'כיסא Q9004, 520 ש"ח'
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
        "<html><head><style>p{}</style></head><body><p>כיסא&nbsp;Q9003</p>"
        "<script>alert(1)</script><div class='gmail_quote'>ישן</div></body></html>"
    )
    msg = _message([_text(html, "text/html")], email_cfg.allowed_senders[0])
    out = fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)
    assert out.text == "כיסא\xa0Q9003"


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
    assert (
        strip_quoted("שלום\nOn Sun, Nov 15 2026 Owner <owner@example.com> wrote:\n> old") == "שלום"
    )
    assert strip_quoted("‏תשובה\n‫בתאריך יום א׳, 15 בנוב׳ מאת X <x@y>:‬\n> a") == "‏תשובה"
    assert strip_quoted("טקסט\n> ציטוט\n>\n") == "טקסט"
    assert strip_quoted("כתבתי: מחיר 5") == "כתבתי: מחיר 5"
    assert strip_quoted("מחיר 449\nתודה\n\n--\n") == "מחיר 449\nתודה"
    assert strip_quoted("מחיר 449\n-- \nישראל, חנות\n050") == "מחיר 449"
    assert strip_quoted("מידות 120--60") == "מידות 120--60"


def test_html_to_text_keeps_line_breaks() -> None:
    assert html_to_text("<p>a</p><p>b<br>c</p>").split() == ["a", "b", "c"]


def test_message_id_and_in_reply_to(email_cfg: EmailChannelConfig) -> None:
    msg = _message([_text("מאשר")], email_cfg.allowed_senders[0])
    msg["payload"]["headers"] += [
        {"name": "Message-ID", "value": " <r1@example.com> "},
        {"name": "In-Reply-To", "value": "<p1@example.com>"},
    ]
    out = fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)
    assert (out.rfc_message_id, out.in_reply_to) == ("<r1@example.com>", "<p1@example.com>")


def test_no_reply_headers(email_cfg: EmailChannelConfig) -> None:
    msg = _message([_text("x")], email_cfg.allowed_senders[0])
    out = fetch_message(FakeGmail({"get": [msg]}), "m1", config=email_cfg, max_bytes=100)
    assert (out.rfc_message_id, out.in_reply_to) == (None, None)


@pytest.mark.parametrize(
    "tail",
    [
        "Sent from my iPhone",
        "נשלח מה-iPhone שלי",
        "‏נשלח מה‑iPhone שלי",
        "נשלח מ-Samsung Galaxy",
        "Get Outlook for Android",
        "sent from my android device.",
    ],
)
def test_phone_signature_line_is_cut(tail: str) -> None:
    from autolist.agent_runtime.commands import parse_command

    text = strip_quoted(f"מאשר\n\n{tail}\n")
    assert text == "מאשר"
    assert parse_command(text, {"approve": "מאשר"}) == "approve"


@pytest.mark.parametrize(
    "text",
    [
        "מאשר\nנשלח מהמחסן מחר",  # real content that only starts like a signature
        "מאשר\nSent from my office, price 480",
        "Sent from my iPhone\nמאשר אבל המחיר 480",  # not the last line: kept
    ],
)
def test_content_that_looks_like_a_signature_is_kept(text: str) -> None:
    assert strip_quoted(text) == text


@pytest.mark.parametrize(
    "header",
    [
        # observed 15.11.2026: Gmail wrapped the Hebrew header after "<", with direction marks
        "‫בתאריך יום א׳, 15 בנוב׳ 2026 ב-18:14 מאת <‪\nagent@example.com‬‏>:‬",
        "On Sun, Nov 15, 2026 at 6:14 PM Agent <\nagent@example.com> wrote:",
        "בתאריך יום א׳, 15 בנוב׳ 2026 ב-18:14 מאת <agent@example.com>:",
    ],
)
def test_wrapped_quote_header_is_cut(header: str) -> None:
    from autolist.agent_runtime.commands import parse_command

    text = strip_quoted(f"מאשר\n\n{header}\n\n> שלום ישראל,\n> גרסה 1\n")
    assert text == "מאשר"
    assert parse_command(text, {"approve": "מאשר"}) == "approve"


def test_line_starting_like_a_header_without_its_end_is_kept() -> None:
    text = "בתאריך 20.11 אגיע לחנות\nמאת ישראל"
    assert strip_quoted(text) == text


def test_content_ending_with_a_colon_is_not_a_header() -> None:
    text = "מאשר\nבתאריך 20.11 מאת הספק יגיעו:\n- המחיר 480"
    assert strip_quoted(text) == text


def test_signature_block_is_read_up_to_the_quote_header() -> None:
    from autolist.channels.email.reader import signature_block

    text = "מאשר\n-- \nרגע, לא\n\nOn Sun, 22 Nov 2026, a@example.com wrote:\n> old"
    assert signature_block(text) == "רגע, לא"
    assert signature_block("מאשר") == ""
    assert signature_block("מאשר\nOn Sun, a@example.com wrote:\n-- \nx") == ""


def test_html_after_a_closed_quote_is_read_again() -> None:
    """Security review 22.11.2026 (L5): text after </blockquote> is not dropped."""
    from autolist.channels.email.reader import html_to_text

    html = "<div>לפני</div><blockquote><p>ישן</p></blockquote><div>אחרי</div>"
    text = html_to_text(html)
    assert "לפני" in text and "אחרי" in text and "ישן" not in text
    nested = '<div class="gmail_quote"><div>ישן <b>מודגש</b></div></div><p>חדש</p>'
    assert "חדש" in html_to_text(nested) and "ישן" not in html_to_text(nested)
