from typing import Any

from autolist.channels.email.own_sent import sent_by_gmail_id, sent_by_rfc_id
from autolist.core.models import EmailChannelConfig
from tests.channels.conftest import FakeGmail


def _msg(cfg: EmailChannelConfig, labels: list[str], sender: str | None = None) -> dict[str, Any]:
    return {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1000",
        "labelIds": labels,
        "payload": {"headers": [{"name": "From", "value": sender or cfg.agent_mailbox}]},
    }


def test_own_sent_message(email_cfg: EmailChannelConfig) -> None:
    g = FakeGmail(
        {"history.list": [{"messages": [{"id": "m1"}]}], "get": [_msg(email_cfg, ["SENT"])]}
    )
    info = sent_by_rfc_id(g, "<a@example.com>", config=email_cfg)
    assert info is not None and (info.thread_id, info.sent_ms) == ("t1", 1000)
    assert g.calls[0][1]["q"] == "rfc822msgid:a@example.com in:sent"


def test_not_sent_or_other_sender_is_none(email_cfg: EmailChannelConfig) -> None:
    assert (
        sent_by_gmail_id(FakeGmail({"get": [_msg(email_cfg, ["INBOX"])]}), "m1", config=email_cfg)
        is None
    )
    other = _msg(email_cfg, ["SENT"], "x@example.org")
    assert sent_by_gmail_id(FakeGmail({"get": [other]}), "m1", config=email_cfg) is None


def test_malformed_id_never_reaches_the_query(email_cfg: EmailChannelConfig) -> None:
    g = FakeGmail({})
    for bad in ("a@example.com", "<a b@x>", '<a"@x>', "<a@x> OR in:inbox"):
        assert sent_by_rfc_id(g, bad, config=email_cfg) is None
    assert g.calls == []
