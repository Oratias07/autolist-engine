"""Inbound sender authentication for the agent mailbox (Gmail).

A message is accepted only if:
- it has exactly one ``From`` address, and it is on the allowlist;
- Gmail's own ``Authentication-Results`` header (authserv-id ``mx.google.com``, the first
  such header from the top) shows ``dkim=pass`` for the sender's domain and
  ``dmarc=pass`` with ``header.from`` equal to the sender's domain.

Headers lower in the message (added by the sender) are never trusted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import getaddresses

from autolist.core.models import EmailChannelConfig

TRUSTED_AUTHSERV_ID = "mx.google.com"
_RESULT = re.compile(r"(?P<method>dkim|dmarc|spf)\s*=\s*(?P<result>[a-z]+)(?P<props>[^;]*)", re.I)
_PROP = re.compile(r"(?P<key>header\.(?:i|d|from))\s*=\s*(?P<value>[^\s;()]+)", re.I)


@dataclass(frozen=True)
class SenderVerdict:
    accepted: bool
    sender: str | None
    reason: str


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def _trusted_results(headers: Sequence[tuple[str, str]]) -> str | None:
    for name, value in headers:
        if name.lower() != "authentication-results":
            continue
        authserv = value.split(";", 1)[0].strip().lower()
        if authserv == TRUSTED_AUTHSERV_ID:
            return value
    return None


def _passes(results: str, method: str, domain: str) -> bool:
    for m in _RESULT.finditer(results):
        if m["method"].lower() != method or m["result"].lower() != "pass":
            continue
        props = {
            p["key"].lower(): p["value"].lower().lstrip("@") for p in _PROP.finditer(m["props"])
        }
        if method == "dkim":
            signer = props.get("header.d") or _domain(props.get("header.i", ""))
            if signer == domain:
                return True
        elif method == "dmarc" and props.get("header.from") == domain:
            return True
    return False


def verify_sender(headers: Sequence[tuple[str, str]], config: EmailChannelConfig) -> SenderVerdict:
    froms = [v for n, v in headers if n.lower() == "from"]
    addresses = [a.lower() for _, a in getaddresses(froms) if a]
    if len(froms) != 1 or len(addresses) != 1:
        return SenderVerdict(False, None, "exactly one From address is required")
    sender = addresses[0]
    if sender not in {a.lower() for a in config.allowed_senders}:
        return SenderVerdict(False, sender, "sender not on the allowlist")
    results = _trusted_results(headers)
    if results is None:
        return SenderVerdict(False, sender, "no Authentication-Results from the receiving server")
    domain = _domain(sender)
    if not _passes(results, "dkim", domain):
        return SenderVerdict(False, sender, "dkim did not pass for the sender's domain")
    if not _passes(results, "dmarc", domain):
        return SenderVerdict(False, sender, "dmarc did not pass for the sender's domain")
    return SenderVerdict(True, sender, "dkim and dmarc pass for an allowlisted sender")
