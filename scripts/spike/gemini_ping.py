"""P0-10 pre-check: the Gemini key works (free call; no generation, no cost).

Reads the key from Secret Manager, lists the models it can see, and records whether the
image-model candidates from the official pricing page (read 2026-11-02) are visible and
which generation methods they declare. Writes a report to OUT. Never logs the key.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from autolist.core.config import get_secret, load_store_config
from autolist.core.logging import configure_logging

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# candidates from https://ai.google.dev/gemini-api/docs/pricing (read 2026-11-02); open question #10
CANDIDATES = ("gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image")


def _list_models(key: str) -> tuple[int, list[dict[str, Any]], str]:
    models: list[dict[str, Any]] = []
    token: str | None = None
    with httpx.Client(timeout=30) as http:
        while True:
            params: dict[str, str | int] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            r = http.get(MODELS_URL, params=params, headers={"x-goog-api-key": key})
            if r.status_code != 200:
                # error body has no secret; keep only status and the error message
                msg = str(r.json().get("error", {}).get("message", ""))[:300]
                return r.status_code, models, msg
            body = r.json()
            models.extend(body.get("models", []))
            token = body.get("nextPageToken")
            if not token:
                return 200, models, ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    configure_logging()
    cfg = load_store_config()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    from google.cloud import secretmanager

    name = f"projects/{cfg.gcp.project_id}/secrets/{cfg.require('secrets.gemini_api_key')}"
    labels = dict(secretmanager.SecretManagerServiceClient().get_secret(name=name).labels)
    status, models, error = _list_models(get_secret(cfg, "gemini_api_key"))

    lines = [f"# P0-10 Gemini key check - {stamp}", "", f"- secret labels: {labels}"]
    lines.append(f"- HTTP {status}")
    if error:
        lines.append(f"- error: {error}")
    lines.append(f"- models visible: {len(models)}")
    by_id = {str(m.get("name", "")).removeprefix("models/"): m for m in models}
    lines += ["", "## Image-model candidates (pricing page, 2026-11-02)", ""]
    for cid in CANDIDATES:
        m = by_id.get(cid)
        if m is None:
            lines.append(f"- `{cid}`: not listed")
        else:
            methods = ", ".join(m.get("supportedGenerationMethods", []))
            lines.append(f"- `{cid}`: listed; methods: {methods}; display: {m.get('displayName')}")
    image_like = sorted(i for i in by_id if "image" in i or "banana" in i)
    lines += ["", "## All model ids containing 'image' or 'banana'", ""]
    lines += [f"- `{i}`" for i in image_like] or ["- none"]

    out = args.out / "docs" / "live-runs" / f"{stamp}-p0-10-gemini-ping.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
