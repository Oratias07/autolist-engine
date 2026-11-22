"""Send one test developer alert through the real sender (thin CLI; DEV request 22.11.2026).

Proves the alert path end to end: template, the sender's recipient allowlist (the developer is
the only extra recipient), Gmail delivery, and that Gmail keeps our own Message-ID (the send
timeout recovery depends on it). No paid calls, no store access.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from autolist.channels.email.gmail_client import build_service
from autolist.channels.email.mail_sender import GmailSender
from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging
from autolist.core.models import OutboundMessage


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    developer: str = cfg.require("agent.developer_contact")
    service = build_service(
        get_secret(cfg, "gmail_oauth_client"),
        get_secret(cfg, "gmail_refresh_token"),
        cfg.require("channels.email.api_timeout_s"),
    )
    sender = GmailSender(service, cfg.channels.email, extra_recipients=frozenset({developer}))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sent = sender.deliver(
        OutboundMessage(
            channel="email",
            to=developer,
            template="developer_alert",
            variables={
                "job_id": "TEST",
                "kind": "test",
                "state": "-",
                "detail": f"Test alert {stamp}: if you read this, developer alerts reach you.",
            },
        )
    )
    kept = sent.rfc_message_id.endswith(
        "@" + cfg.channels.email.agent_mailbox.rsplit("@", 1)[-1] + ">"
    )
    lines = [
        f"# developer alert test - {stamp}",
        "",
        f"- sent to the developer contact: gmail id {sent.gmail_id}",
        f"- Gmail kept our own Message-ID: {kept}",
    ]
    out = args.out / "docs" / "live-runs" / f"{stamp}-alert-test.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
