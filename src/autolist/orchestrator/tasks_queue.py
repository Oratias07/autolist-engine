"""Enqueue one job task on Cloud Tasks. The task calls this service back with an OIDC token."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class TaskQueue(Protocol):
    def enqueue(self, path: str, payload: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class QueueTarget:
    project: str
    region: str
    queue: str
    service_url: str  # https URL of this service
    invoker_email: str  # service account whose OIDC token the task carries
    dispatch_deadline_s: int


class CloudTasksQueue:
    def __init__(self, client: Any, target: QueueTarget) -> None:
        if not target.service_url.startswith("https://"):
            raise ValueError("service_url must be an https URL")
        self._client = client
        self._t = target

    def enqueue(self, path: str, payload: dict[str, Any]) -> str:
        t = self._t
        parent = f"projects/{t.project}/locations/{t.region}/queues/{t.queue}"
        task = {
            "http_request": {
                "http_method": "POST",
                "url": t.service_url.rstrip("/") + path,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode("utf-8"),
                "oidc_token": {
                    "service_account_email": t.invoker_email,
                    "audience": t.service_url.rstrip("/"),
                },
            },
            "dispatch_deadline": {"seconds": t.dispatch_deadline_s},
        }
        created = self._client.create_task(request={"parent": parent, "task": task})
        return str(created.name)
