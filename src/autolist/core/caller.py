"""Who called the service: the ``email`` claim of the ID token Cloud Run already checked.

Cloud Run verifies the token before a request reaches the container (the service is not
public). This only reads the payload; it never trusts a token by itself, and it is never the
first gate (security review 22.11.2026, H1).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping


def caller_email(headers: Mapping[str, str]) -> str | None:
    """The token's ``email`` claim, lower-cased, or None when there is no readable token."""
    auth = headers.get("authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    email = payload.get("email") if isinstance(payload, dict) else None
    return email.lower() if isinstance(email, str) and email else None


def allowed(path: str, email: str | None, invoker: str, health_callers: list[str]) -> bool:
    """Every path: the invoker account. ``/health`` also: the deploy check's accounts."""
    if email is None:
        return False
    callers = {invoker.lower()}
    if path == "/health":
        callers |= {c.lower() for c in health_callers}
    return email in callers
