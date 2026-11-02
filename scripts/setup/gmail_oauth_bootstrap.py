"""One-time Gmail OAuth bootstrap for the agent mailbox (run in Cloud Shell by DEV).

- Stores the OAuth client JSON and the agent's refresh token directly in Secret Manager
  (label stage=dev). Nothing secret is printed.
- Refuses to store anything unless the signed-in account is exactly the agent mailbox.
- Scope: gmail.modify only (read, label, send).

Usage (Cloud Shell):
  pip install --user --quiet google-auth-oauthlib google-cloud-secret-manager
  python3 gmail_oauth_bootstrap.py --project yts-autolist \
      --client-file client_secret.json --mailbox yts.products.agent@gmail.com
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path

from google.api_core.exceptions import AlreadyExists
from google.auth.transport.requests import AuthorizedSession
from google.cloud import secretmanager
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
REDIRECT = "http://localhost:8765/"
CLIENT_SECRET_NAME = "gmail-oauth-client"  # noqa: S105 - secret name, not a value
TOKEN_SECRET_NAME = "gmail-agent-refresh-token"  # noqa: S105 - secret name, not a value


def _store(
    sm: secretmanager.SecretManagerServiceClient, project: str, name: str, value: str
) -> None:
    parent = f"projects/{project}"
    with contextlib.suppress(AlreadyExists):
        sm.create_secret(
            request={
                "parent": parent,
                "secret_id": name,
                "secret": {"replication": {"automatic": {}}, "labels": {"stage": "dev"}},
            }
        )
    sm.add_secret_version(
        request={"parent": f"{parent}/secrets/{name}", "payload": {"data": value.encode()}}
    )
    print(f"stored: {name} (new version)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--client-file", type=Path, required=True)
    ap.add_argument("--mailbox", required=True)
    args = ap.parse_args()

    client_json = args.client_file.read_text(encoding="utf-8")
    if "installed" not in json.loads(client_json):
        raise SystemExit("The OAuth client must be of type 'Desktop app'.")

    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # loopback redirect only (localhost)
    flow = InstalledAppFlow.from_client_config(json.loads(client_json), SCOPES)
    flow.redirect_uri = REDIRECT
    url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", login_hint=args.mailbox
    )
    print("\n1) Open this link in your browser, signed in as", args.mailbox)
    print(url)
    print("\n2) Approve. The browser then shows an error page on localhost: that is expected.")
    print("3) Copy the FULL address from the browser's address bar and paste it below.\n")
    redirected = getpass.getpass("Paste the address (hidden), then Enter: ").strip()
    flow.fetch_token(authorization_response=redirected)
    creds = flow.credentials

    profile = (
        AuthorizedSession(creds)
        .get("https://gmail.googleapis.com/gmail/v1/users/me/profile")
        .json()
    )
    email = str(profile.get("emailAddress", "")).lower()
    if email != args.mailbox.lower():
        raise SystemExit(f"Signed in as {email or 'unknown'}, not {args.mailbox}. Nothing stored.")
    if not creds.refresh_token:
        raise SystemExit("No refresh token returned. Remove the app's access and run again.")

    sm = secretmanager.SecretManagerServiceClient()
    _store(sm, args.project, CLIENT_SECRET_NAME, client_json)
    _store(sm, args.project, TOKEN_SECRET_NAME, creds.refresh_token)
    print(f"\nDone. Verified mailbox: {email}. You can now delete {args.client_file}.")


if __name__ == "__main__":
    main()
