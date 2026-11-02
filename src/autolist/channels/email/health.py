"""Daily health check of the agent mailbox connection (alerts go via Cloud Monitoring)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from autolist.core.models import EmailChannelConfig, HealthStatus

MIN_WATCH_REMAINING = timedelta(hours=24)


def check(
    service: Any,
    config: EmailChannelConfig,
    watch_expiration_ms: int | None,
    now: datetime | None = None,
) -> HealthStatus:
    """Token works (profile call), it is the agent mailbox, and the watch is not expiring.

    ``watch_expiration_ms`` is None when no watch is known; that is reported as a problem.
    """
    now = now or datetime.now(UTC)
    problems: list[str] = []
    details: dict[str, str] = {}
    try:
        profile = service.users().getProfile(userId="me").execute()
        email = str(profile.get("emailAddress", "")).lower()
        details["mailbox"] = email
        if email != config.agent_mailbox.lower():
            problems.append("token belongs to a different mailbox")
    except Exception as exc:
        problems.append(f"profile call failed: {type(exc).__name__}")
    if watch_expiration_ms is None:
        problems.append("no active watch recorded")
    else:
        expires = datetime.fromtimestamp(watch_expiration_ms / 1000, tz=UTC)
        details["watch_expires"] = expires.isoformat()
        if expires - now < MIN_WATCH_REMAINING:
            problems.append("watch expires within 24h: renew it")
    return HealthStatus(ok=not problems, checked_at=now, problems=problems, details=details)
