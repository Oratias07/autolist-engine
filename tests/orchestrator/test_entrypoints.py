"""Service entrypoints (infra SPEC acceptance 5 and 6): fake Gmail, fake Firestore, fake queue."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from autolist.core.config import parse_store_config
from autolist.core.state_store import ChannelStateStore, ProcessedMessages
from autolist.orchestrator.entrypoints import Deps, create_app
from autolist.orchestrator.tasks_queue import CloudTasksQueue, QueueTarget
from tests.channels.conftest import FakeGmail
from tests.core.conftest import VALID_STORE
from tests.fakes.firestore import FakeFirestore

NOW = datetime(2026, 11, 15, 10, tzinfo=UTC)
GOOD_AR = (
    "mx.google.com; dkim=pass header.i=@example.com header.s=s1 header.b=abc; "
    "dmarc=pass (p=NONE) header.from=example.com"
)
PUSH = {"message": {"data": "e30=", "messageId": "1"}, "subscription": "s"}


def _msg(sender: str, ar: str | None, thread: str = "t1") -> dict[str, Any]:
    headers = [{"name": "From", "value": f"X <{sender}>"}, {"name": "Message-ID", "value": "<m@x>"}]
    if ar:
        headers.insert(0, {"name": "Authentication-Results", "value": ar})
    return {"threadId": thread, "payload": {"headers": headers}}


class FakeQueue:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, path: str, payload: dict[str, Any]) -> str:
        self.tasks.append((path, payload))
        return f"task-{len(self.tasks)}"


class Rig:
    def __init__(
        self,
        gmail_results: dict[str, Any],
        cursor: str | None = "100",
        runtime: Any = None,
        caller_check: str = "log",
    ) -> None:
        raw = copy.deepcopy(VALID_STORE)
        raw["gcp"]["caller_check"] = caller_check
        cfg = parse_store_config(raw)
        self.owner = cfg.channels.email.allowed_senders[0]
        self.fs = FakeFirestore()
        self.gmail = FakeGmail(gmail_results)
        self.queue = FakeQueue()
        self.state = ChannelStateStore(self.fs, "email")
        if cursor:
            self.state.set_history_id(cursor)
        self.health: dict[str, Any] = {"ok": True}
        self.client = TestClient(
            create_app(
                Deps(
                    config=cfg,
                    gmail=lambda: self.gmail,
                    state=self.state,
                    processed=ProcessedMessages(self.fs),
                    queue=self.queue,
                    health=lambda: self.health,
                    runtime=lambda: runtime,
                    now=lambda: NOW,
                )
            )
        )


def _history(ids: list[str], latest: str) -> dict[str, Any]:
    return {
        "history": [{"messagesAdded": [{"message": {"id": i}} for i in ids]}],
        "historyId": latest,
    }


def test_accepted_email_is_recorded_and_queued_once() -> None:
    owner = parse_store_config(copy.deepcopy(VALID_STORE)).channels.email.allowed_senders[0]
    rig = Rig(
        {
            "history.list": [_history(["m1"], "101"), _history(["m1"], "101")],
            "get": [_msg(owner, GOOD_AR)],
        }
    )
    r = rig.client.post("/events/gmail", json=PUSH)
    assert r.status_code == 200
    assert r.json() == {"accepted": 1}
    assert rig.queue.tasks == [
        (
            "/tasks/job",
            {
                "event": "email_received",
                "gmail_id": "m1",
                "thread_id": "t1",
                "rfc_message_id": "<m@x>",
            },
        )
    ]
    doc = rig.fs.data["processed_messages"]["m1"]
    assert doc["outcome"] == "queued"
    assert doc["task"] == "task-1"
    assert rig.state.history_id() == "101"
    # the same push delivered again: no second task, no second header fetch
    r2 = rig.client.post("/events/gmail", json=PUSH)
    assert r2.json() == {"accepted": 0}
    assert len(rig.queue.tasks) == 1
    assert [c[0] for c in rig.gmail.calls].count("get") == 1


def test_unverified_sender_is_recorded_and_dropped() -> None:
    rig = Rig(
        {
            "history.list": [_history(["m2", "m3"], "102")],
            "get": [_msg("stranger@elsewhere.example", GOOD_AR), _msg("owner@example.com", None)],
        }
    )
    r = rig.client.post("/events/gmail", json=PUSH)
    assert r.json() == {"accepted": 0}
    assert rig.queue.tasks == []
    docs = rig.fs.data["processed_messages"]
    assert docs["m2"]["outcome"] == "rejected"
    assert docs["m3"]["outcome"] == "rejected"


def test_push_without_cursor_does_nothing() -> None:
    rig = Rig({}, cursor=None)
    r = rig.client.post("/events/gmail", json=PUSH)
    assert r.json() == {"accepted": 0}
    assert rig.gmail.calls == []


def test_bad_push_body_is_refused() -> None:
    rig = Rig({})
    assert rig.client.post("/events/gmail", json={"x": 1}).status_code == 400
    assert rig.client.post("/events/gmail", content=b"not json").status_code == 400


def test_renew_watch_moves_expiry_and_keeps_cursor() -> None:
    rig = Rig({"watch": [{"historyId": "500", "expiration": "1800000000000"}]})
    r = rig.client.post("/cron/renew-watch")
    assert r.status_code == 200
    assert r.json()["watch_expires"].startswith("2027-01-15")
    assert rig.state.watch_expires_ms() == 1800000000000
    assert rig.state.history_id() == "100"  # not overwritten
    name, kw = rig.gmail.calls[0]
    assert name == "watch"
    assert kw["body"]["topicName"].startswith("projects/")


def test_renew_watch_sets_cursor_when_none() -> None:
    rig = Rig({"watch": [{"historyId": "500", "expiration": "1800000000000"}]}, cursor=None)
    rig.client.post("/cron/renew-watch")
    assert rig.state.history_id() == "500"


def test_healthz_reports_problem_as_503() -> None:
    rig = Rig({})
    assert rig.client.get("/health").status_code == 200
    rig.health = {"ok": False, "problems": ["firestore"]}
    assert rig.client.get("/health").status_code == 503


class FakeTasksClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create_task(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return type("T", (), {"name": "projects/p/locations/r/queues/q/tasks/1"})()


def test_cloud_tasks_request_shape() -> None:
    fake = FakeTasksClient()
    target = QueueTarget("p", "r", "q", "https://svc.example.com/", "inv@example.com", 660)
    name = CloudTasksQueue(fake, target).enqueue("/tasks/job", {"a": 1})
    assert name.endswith("/tasks/1")
    req = fake.requests[0]
    assert req["parent"] == "projects/p/locations/r/queues/q"
    http = req["task"]["http_request"]
    assert http["url"] == "https://svc.example.com/tasks/job"
    assert http["oidc_token"] == {
        "service_account_email": "inv@example.com",
        "audience": "https://svc.example.com",
    }
    assert http["body"] == b'{"a": 1}'
    assert req["task"]["dispatch_deadline"] == {"seconds": 660}


def test_cloud_tasks_refuses_plain_http() -> None:
    import pytest

    with pytest.raises(ValueError, match="https"):
        CloudTasksQueue(FakeTasksClient(), QueueTarget("p", "r", "q", "http://x", "i", 60))


def test_job_task_runs_one_step() -> None:
    from tests.orchestrator.rig import Rig as JobRig
    from tests.orchestrator.rig import email, extraction, item

    jobs = JobRig(extraction([item()]))
    jobs.messages["g1"] = email("כיסא משרדי, דגם C1, 450, 3 יחידות")
    rig = Rig({}, runtime=jobs.rt)
    r = rig.client.post("/tasks/job", json={"event": "email_received", "gmail_id": "g1"})
    assert (r.status_code, r.json()) == (200, {"opened": ["T-1"]})


def test_job_task_failure_is_500_and_bad_body_400() -> None:
    from tests.orchestrator.rig import Rig as JobRig
    from tests.orchestrator.rig import extraction

    rig = Rig({}, runtime=JobRig(extraction([])).rt)
    assert rig.client.post("/tasks/job", json={"event": "nope"}).status_code == 500
    assert rig.client.post("/tasks/job", json={"x": 1}).status_code == 400
    assert rig.client.post("/tasks/job", content=b"not json").status_code == 400


def test_watchdog_route() -> None:
    from tests.orchestrator.rig import Rig as JobRig
    from tests.orchestrator.rig import extraction

    rig = Rig({}, runtime=JobRig(extraction([])).rt)
    r = rig.client.post("/cron/watchdog")
    assert (r.status_code, r.json()) == (200, {"failed": [], "expired": []})


def test_an_automatic_reply_is_dropped_without_any_answer() -> None:
    """Security review 22.11.2026 (M1): no mail loops with an auto-responder."""
    owner = parse_store_config(copy.deepcopy(VALID_STORE)).channels.email.allowed_senders[0]
    auto = _msg(owner, GOOD_AR)
    auto["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-replied"})
    bulk = _msg(owner, GOOD_AR)
    bulk["payload"]["headers"].append({"name": "Precedence", "value": "bulk"})
    rig = Rig({"history.list": [_history(["a1", "a2"], "103")], "get": [auto, bulk]})
    assert rig.client.post("/events/gmail", json=PUSH).json() == {"accepted": 0}
    assert rig.queue.tasks == []
    docs = rig.fs.data["processed_messages"]
    assert docs["a1"]["outcome"] == docs["a2"]["outcome"] == "auto_reply"


def test_a_header_fetch_error_loses_no_email() -> None:
    """Security review 22.11.2026 (L4): the claim is released and the cursor stays."""
    owner = parse_store_config(copy.deepcopy(VALID_STORE)).channels.email.allowed_senders[0]
    rig = Rig(
        {
            "history.list": [_history(["m1"], "101"), _history(["m1"], "101")],
            "get": [TimeoutError("timed out"), _msg(owner, GOOD_AR)],
        }
    )
    client = TestClient(rig.client.app, raise_server_exceptions=False)
    assert client.post("/events/gmail", json=PUSH).status_code == 500
    assert "m1" not in rig.fs.data.get("processed_messages", {})
    assert rig.state.history_id() == "100"
    assert rig.client.post("/events/gmail", json=PUSH).json() == {"accepted": 1}
    assert rig.state.history_id() == "101" and len(rig.queue.tasks) == 1


def test_a_queue_error_loses_no_email() -> None:
    owner = parse_store_config(copy.deepcopy(VALID_STORE)).channels.email.allowed_senders[0]
    rig = Rig(
        {
            "history.list": [_history(["m1"], "101"), _history(["m1"], "101")],
            "get": [_msg(owner, GOOD_AR), _msg(owner, GOOD_AR)],
        }
    )

    def broken(path: str, payload: dict[str, Any]) -> str:
        raise RuntimeError("queue down")

    real = rig.queue.enqueue
    rig.queue.enqueue = broken  # type: ignore[method-assign]
    client = TestClient(rig.client.app, raise_server_exceptions=False)
    assert client.post("/events/gmail", json=PUSH).status_code == 500
    assert "m1" not in rig.fs.data.get("processed_messages", {})
    assert rig.state.history_id() == "100"
    rig.queue.enqueue = real  # type: ignore[method-assign]
    assert rig.client.post("/events/gmail", json=PUSH).json() == {"accepted": 1}


def _token(email: str) -> dict[str, str]:
    """An ID token as Cloud Run hands it on: checked by Cloud Run, the signature replaced."""
    import base64
    import json

    def part(d: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return {"Authorization": f"Bearer {part({'alg': 'RS256'})}.{part({'email': email})}.SIG"}


INVOKER = "invoke@example.iam.gserviceaccount.com"
DEPLOY = "deploy@example.iam.gserviceaccount.com"


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("GET", "/health", _token(INVOKER), 200),
        ("GET", "/health", _token(DEPLOY), 200),
        ("POST", "/cron/watchdog", _token(DEPLOY), 403),  # the deploy check: /health only
        ("POST", "/events/gmail", _token("other@example.com"), 403),
        ("POST", "/events/gmail", {}, 403),
        ("POST", "/tasks/job", {"Authorization": "Bearer not-a-token"}, 403),
    ],
)
def test_enforce_lets_only_the_invoker_call_and_the_deploy_check_read_health(
    method: str, path: str, headers: dict[str, str], status: int
) -> None:
    rig = Rig({}, caller_check="enforce")
    assert rig.client.request(method, path, headers=headers).status_code == status


def test_enforce_passes_the_invoker_to_the_handler() -> None:
    rig = Rig({}, caller_check="enforce")
    r = rig.client.post("/events/gmail", json={"bad": 1}, headers=_token(INVOKER.upper()))
    assert r.status_code == 400  # reached the handler, which refused the body


def test_log_mode_records_the_caller_and_refuses_nothing(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig({})
    with caplog.at_level("INFO"):
        assert rig.client.get("/health", headers=_token("other@example.com")).status_code == 200
    rec = next(r for r in caplog.records if r.getMessage() == "caller")
    assert (rec.caller, rec.allowed) == ("other@example.com", False)  # type: ignore[attr-defined]
