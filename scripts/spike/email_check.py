"""P0-08 live check of the agent mailbox (thin CLI over channels/email).

1. Build the Gmail client from Secret Manager (client JSON + refresh token).
2. Health check (token refresh, profile = agent mailbox).
3. Start/renew users.watch to the Pub/Sub topic.
4. Wait up to --wait-minutes for a push on the pull subscription (DEV sends one email from
   the owner address meanwhile), then read history and verify the sender of each new message.
5. Send a plain-text `cost_alert` test to the owner, in the thread of the test email.
Writes a report to OUT. Never logs message bodies or tokens.
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

from autolist.channels.email.gmail_client import build_service
from autolist.channels.email.gmail_watch import message_headers, new_message_ids, start_watch
from autolist.channels.email.health import check
from autolist.channels.email.mail_sender import GmailSender
from autolist.channels.email.sender_auth import verify_sender
from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.core.models import OutboundMessage

PUBSUB = "https://pubsub.googleapis.com/v1"


def _pull(session: AuthorizedSession, sub: str) -> list[dict[str, Any]]:
    r = session.post(f"{PUBSUB}/{sub}:pull", json={"maxMessages": 10}, timeout=30)
    r.raise_for_status()
    received: list[dict[str, Any]] = r.json().get("receivedMessages", [])
    if received:
        ack = [m["ackId"] for m in received]
        session.post(f"{PUBSUB}/{sub}:acknowledge", json={"ackIds": ack}, timeout=30)
    return received


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wait-minutes", type=int, default=10)
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    email_cfg = cfg.channels.email
    project = cfg.gcp.project_id
    topic = f"projects/{project}/topics/{email_cfg.pubsub_topic}"
    sub = f"projects/{project}/subscriptions/{email_cfg.pubsub_subscription}"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# P0-08 email check - {stamp}", ""]

    service = build_service(
        get_secret(cfg, "gmail_oauth_client"), get_secret(cfg, "gmail_refresh_token")
    )
    watch = start_watch(service, topic)
    health = check(service, email_cfg, int(watch["expiration"]))
    lines += [
        f"- health: ok={health.ok} problems={health.problems} details={health.details}",
        f"- watch: historyId={watch['historyId']} expires={health.details.get('watch_expires')}",
    ]

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
    session = AuthorizedSession(creds)  # type: ignore[no-untyped-call]
    _pull(session, sub)  # drop anything older than this run
    started = time.monotonic()
    pushes = 0
    reply_to: tuple[str, str] | None = None
    seen: set[str] = set()
    # Any mailbox change triggers a push (labels, our own sends), so keep waiting until a
    # newly added message from an accepted sender shows up in history, or until timeout.
    while reply_to is None and time.monotonic() - started < args.wait_minutes * 60:
        if not _pull(session, sub):
            time.sleep(10)
            continue
        pushes += 1
        ids, latest = new_message_ids(service, watch["historyId"])
        for mid in ids:
            if mid in seen:
                continue
            seen.add(mid)
            headers, thread = message_headers(service, mid)
            verdict = verify_sender(headers, email_cfg)
            waited = time.monotonic() - started
            lines.append(
                f"- push #{pushes} after {waited:.0f}s: message {mid} "
                f"accepted={verdict.accepted} ({verdict.reason})"
            )
            if verdict.accepted and reply_to is None:
                msg_id = next((v for n, v in headers if n.lower() == "message-id"), "")
                reply_to = (thread, msg_id)
    lines.append(f"- pushes received: {pushes}; new messages seen: {len(seen)}")
    if reply_to is None:
        lines.append(f"- no accepted message within {args.wait_minutes} min")

    if reply_to is not None:
        test = OutboundMessage(
            channel="email",
            to=email_cfg.owner_address,
            template="cost_alert",
            thread_id=reply_to[0],
            in_reply_to=reply_to[1] or None,
            variables={
                "month_spend": "0.15",
                "monthly_cap": str(cfg.costs.monthly_cap),
                "llm_spend": "0.15",
                "image_spend": "0",
                "search_spend": "0",
                "jobs_count": "0",
                "next_threshold": str(cfg.costs.alert_thresholds[0]),
            },
        )
        sent = GmailSender(service, email_cfg).send(test)
        lines.append(f"- test cost_alert sent to the owner in the same thread (gmail id {sent})")
    else:
        lines.append("- no accepted message: nothing sent")

    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-08-email.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
