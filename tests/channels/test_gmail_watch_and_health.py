from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autolist.channels.email.gmail_watch import message_headers, new_message_ids, start_watch
from autolist.channels.email.health import check
from autolist.core.models import EmailChannelConfig
from tests.channels.conftest import FakeGmail

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_watch_history_and_headers() -> None:
    gmail = FakeGmail(
        {
            "watch": {"historyId": 100, "expiration": "1767000000000"},
            "history.list": [
                {
                    "history": [{"messagesAdded": [{"message": {"id": "a"}}]}],
                    "historyId": 105,
                    "nextPageToken": "p2",
                },
                {
                    "history": [
                        {"messagesAdded": [{"message": {"id": "b"}}, {"message": {"id": "a"}}]}
                    ],
                    "historyId": 107,
                },
            ],
            "get": {
                "threadId": "t1",
                "payload": {"headers": [{"name": "From", "value": "x@example.com"}]},
            },
        }
    )
    assert start_watch(gmail, "projects/p/topics/t") == {
        "historyId": "100",
        "expiration": "1767000000000",
    }
    assert gmail.calls[0][1]["body"] == {"topicName": "projects/p/topics/t", "labelIds": ["INBOX"]}
    assert new_message_ids(gmail, "100") == (["a", "b"], "107")
    headers, thread = message_headers(gmail, "a")
    assert headers == [("From", "x@example.com")] and thread == "t1"
    assert gmail.calls[-1][1]["format"] == "metadata"  # never the body


def test_health_ok_and_problems(email_cfg: EmailChannelConfig) -> None:
    far = int((NOW + timedelta(days=5)).timestamp() * 1000)
    near = int((NOW + timedelta(hours=3)).timestamp() * 1000)
    ok = check(
        FakeGmail({"getProfile": {"emailAddress": "agent@example.com"}}), email_cfg, far, NOW
    )
    assert ok.ok and ok.details["mailbox"] == "agent@example.com"
    wrong = check(
        FakeGmail({"getProfile": {"emailAddress": "x@example.com"}}), email_cfg, near, NOW
    )
    assert not wrong.ok and len(wrong.problems) == 2
    broken = check(FakeGmail({"getProfile": RuntimeError("revoked")}), email_cfg, None)
    assert broken.problems == ["profile call failed: RuntimeError", "no active watch recorded"]
