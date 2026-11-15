"""P1-05 live check: read the owner's latest email in the agent mailbox, as the service will.

Read-only and free: lists the newest message from the allowlisted owner (last 2 hours),
fetches it with `channels.email.reader.fetch_message` (sender verified again), runs the
intake attachment guard, and writes what was read to the report. No LLM call, no reply,
no label change, no store access. The report holds client text: store repo only.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from autolist.channels.email.gmail_client import build_service
from autolist.channels.email.reader import fetch_message
from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.intake.attachment_guard import guard


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    email = cfg.channels.email
    service = build_service(
        get_secret(cfg, "gmail_oauth_client"), get_secret(cfg, "gmail_refresh_token")
    )
    owner = email.allowed_senders[0]
    listing = (
        service.users()
        .messages()
        .list(userId="me", q=f"from:{owner} newer_than:2h", maxResults=1)
        .execute()
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# P1-05 read email - {stamp}", ""]
    ids = [m["id"] for m in listing.get("messages", [])]
    if not ids:
        lines.append("**No email from the owner in the last 2 hours. Nothing read.**")
    else:
        msg = fetch_message(
            service, ids[0], config=email, max_bytes=cfg.intake.max_attachment_bytes
        )
        res = guard(msg.attachments, cfg.intake)
        lines += [
            f"- gmail id: {msg.message_id}; thread: {msg.thread_id}",
            f"- sender verified: {msg.authenticated}",
            f"- received: {msg.received_at.isoformat()}",
            f"- subject: {msg.subject}",
            f"- attachments: {[(a.filename, a.mime_type, a.size_bytes) for a in msg.attachments]}",
            f"- accepted photos: {[(i.name, len(i.data)) for i in res.accepted]}",
            f"- rejected: {res.rejected}",
            "",
            "## Text as the pipeline will see it",
            "",
            "```",
            msg.text,
            "```",
        ]
    out = args.out / "docs" / "live-runs" / f"{stamp}-p1-05-read-email.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
