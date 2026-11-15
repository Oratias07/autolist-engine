"""Read one full email from the agent mailbox: text, subject, image attachments.

Called by the job runner with a message id that `inbox.poll_accepted` already accepted.
The sender is verified again here from the full message headers (defence in depth), so a
caller that skipped the inbox step still cannot hand an unverified email to the pipeline.

Body text:
- the first text/plain part; if there is none, text/html reduced to plain text
  (scripts and styles dropped, tags removed, entities unescaped);
- an unknown charset falls back to UTF-8 with replacement characters;
- quoted history from earlier messages in the thread is cut ("On ... wrote:",
  the Hebrew Gmail line "בתאריך ... מאת ...:", and trailing ">" lines), and so is the
  signature below the standard "-- " line (observed in a real owner email, 15.11.2026),
  and a phone app's default "sent from" line when it is the last line (exact list below).

Attachments: every part with a file name (attached or inline). A part larger than
`max_bytes` is listed with no data and is never downloaded; the intake guard rejects it.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from autolist.channels.email.sender_auth import verify_sender
from autolist.channels.errors import SenderRejected
from autolist.core.models import Attachment, ChannelMessage, EmailChannelConfig

_BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
_EN_QUOTE = re.compile(r"^On .+ wrote:\s*$")
_MAX_TEXT_CHARS = 20_000  # an owner email is short; anything beyond this is not read


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _charset(part: dict[str, Any]) -> str:
    for h in part.get("headers", []):
        if h.get("name", "").lower() == "content-type":
            m = re.search(r'charset="?([\w\-]+)"?', h.get("value", ""), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def _decode(part: dict[str, Any]) -> str:
    raw = _b64(part.get("body", {}).get("data", ""))
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _TextOnly(HTMLParser):
    _SKIP: frozenset[str] = frozenset({"script", "style", "head", "title"})
    _BREAK: frozenset[str] = frozenset(
        {"br", "p", "div", "li", "tr", "blockquote", "h1", "h2", "h3"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._quote = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        classes = " ".join(v or "" for k, v in attrs if k == "class")
        if tag == "blockquote" or "gmail_quote" in classes:
            self._quote += 1
        if tag in self._BREAK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and not self._quote:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    return "".join(parser.out)


# Default "sent from" lines that phone mail apps append (DEV 15.11.2026: expect them).
# Only the LAST non-empty line is cut, only when it matches one of these exactly (after
# direction marks and spacing are removed), so real content can never be cut by it.
_DEVICE_LINES = re.compile(
    r"(sent from my (iphone|ipad|android( device)?|samsung( galaxy)?( smartphone)?"
    r"|galaxy|mobile( device)?)"
    r"|get outlook for (ios|android)"
    r"|sent from (outlook|yahoo mail|mail) for (iphone|ios|android)"
    r"|נשלח מה-?(iphone|ipad) שלי"
    r"|נשלח מ-?(iphone|ipad|android|samsung( galaxy)?|galaxy)( שלי)?"
    r"|נשלח מהנייד( שלי)?|נשלח מהטלפון( הנייד)?( שלי)?)",
    re.IGNORECASE,
)


def _device_line(line: str) -> bool:
    plain = " ".join(_BIDI.sub("", line).replace("\u2011", "-").split()).rstrip(".")
    return _DEVICE_LINES.fullmatch(plain) is not None


_HEADER_LINES = 3  # Gmail wraps a long quote header in text/plain (observed 15.11.2026)


def _quote_header(lines: list[str], i: int) -> bool:
    """A reply's quote header starting at line i, possibly wrapped over up to 3 lines."""
    first = _BIDI.sub("", lines[i]).strip()
    if not (first.startswith("בתאריך") or first.startswith("On ")):
        return False
    for n in range(1, _HEADER_LINES + 1):
        head = " ".join(_BIDI.sub("", x).strip() for x in lines[i : i + n])
        # the sender's address is always in the header: content that merely ends with ":" is kept
        if (
            head.endswith(":")
            and "@" in head
            and ((head.startswith("בתאריך") and "מאת" in head) or _EN_QUOTE.match(head))
        ):
            return True
    return False


def strip_quoted(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        signature = line.rstrip("\r") in ("--", "-- ")  # RFC 3676 signature delimiter
        if _quote_header(lines, i) or signature:
            lines = lines[:i]
            break
    while lines and (lines[-1].lstrip().startswith(">") or not lines[-1].strip()):
        lines.pop()
    if lines and _device_line(lines[-1]):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def _walk(part: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [part]
    for child in part.get("parts", []) or []:
        parts.extend(_walk(child))
    return parts


def fetch_message(
    service: Any, gmail_id: str, *, config: EmailChannelConfig, max_bytes: int
) -> ChannelMessage:
    res = service.users().messages().get(userId="me", id=gmail_id, format="full").execute()
    payload: dict[str, Any] = res.get("payload", {})
    headers = [(h["name"], h["value"]) for h in payload.get("headers", [])]
    verdict = verify_sender(headers, config)
    if not verdict.accepted or verdict.sender is None:
        raise SenderRejected(f"{gmail_id}: {verdict.reason}")

    parts = _walk(payload)
    plain = [p for p in parts if p.get("mimeType") == "text/plain" and not p.get("filename")]
    html = [p for p in parts if p.get("mimeType") == "text/html" and not p.get("filename")]
    if plain:
        body = _decode(plain[0])
    elif html:
        body = html_to_text(_decode(html[0]))
    else:
        body = ""
    text = strip_quoted(body)[:_MAX_TEXT_CHARS]

    attachments: list[Attachment] = []
    for p in parts:
        name = p.get("filename")
        if not name:
            continue
        body_info = p.get("body", {})
        size = int(body_info.get("size", 0))
        data: bytes | None = None
        if size <= max_bytes:
            if "attachmentId" in body_info:
                got = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=gmail_id, id=body_info["attachmentId"])
                    .execute()
                )
                data = _b64(got["data"])
            elif "data" in body_info:
                data = _b64(body_info["data"])
        attachments.append(
            Attachment(
                filename=name,
                mime_type=str(p.get("mimeType", "")),
                size_bytes=size,
                data=data if data is not None and len(data) <= max_bytes else None,
            )
        )

    def header(name: str) -> str | None:
        return next((v.strip() for n, v in headers if n.lower() == name), None)

    subject = header("subject")
    received = datetime.fromtimestamp(int(res.get("internalDate", "0")) / 1000, tz=UTC)
    return ChannelMessage(
        channel="email",
        message_id=gmail_id,
        thread_id=str(res["threadId"]),
        sender=verdict.sender,
        authenticated=True,
        received_at=received,
        subject=subject,
        text=text,
        attachments=attachments,
        rfc_message_id=header("message-id"),
        in_reply_to=header("in-reply-to"),
    )
