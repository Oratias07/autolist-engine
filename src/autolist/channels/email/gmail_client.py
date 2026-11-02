"""Gmail API client for the agent mailbox, built from secrets in Secret Manager."""

from __future__ import annotations

import json
from typing import Any

from autolist.channels.errors import ChannelAuthError

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def build_service(client_json: str, refresh_token: str) -> Any:  # pragma: no cover - live only
    """Authorised Gmail service. Raises ``ChannelAuthError`` if the token cannot refresh."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    installed = json.loads(client_json)["installed"]
    creds = Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=installed["token_uri"],
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())  # type: ignore[no-untyped-call]
    except RefreshError as exc:
        raise ChannelAuthError("agent mailbox token could not be refreshed") from exc
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
