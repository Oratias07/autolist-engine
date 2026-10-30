"""Load the store config (``$AUTOLIST_STORE_DIR/store.yaml``) and read secrets.

No defaults for client values: a missing key raises ``ConfigMissing``; a value that is
present but null is "unknown" and raises ``ConfigMissing`` only when code requires it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import ValidationError

from autolist.core.errors import ConfigInvalid, ConfigMissing
from autolist.core.logging import register_secret
from autolist.core.models import StoreConfig

STORE_DIR_ENV = "AUTOLIST_STORE_DIR"


def store_dir() -> Path:
    raw = os.environ.get(STORE_DIR_ENV)
    if not raw:
        raise ConfigMissing(STORE_DIR_ENV)
    return Path(raw)


def load_store_config(path: Path | None = None) -> StoreConfig:
    """Parse and validate ``store.yaml``. Raises ``ConfigMissing`` / ``ConfigInvalid``."""
    file = path if path is not None else store_dir() / "store.yaml"
    if not file.is_file():
        raise ConfigMissing(str(file))
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigInvalid(str(file), "top level must be a mapping")
    return parse_store_config(data)


def parse_store_config(data: dict[str, Any]) -> StoreConfig:
    try:
        return StoreConfig.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        missing = [e for e in errors if e["type"] == "missing"]
        first = missing[0] if missing else errors[0]
        key = ".".join(str(p) for p in first["loc"])
        if missing:
            raise ConfigMissing(key) from None
        raise ConfigInvalid(key, first["msg"]) from None


class _SecretPayload(Protocol):
    data: bytes


class _SecretResponse(Protocol):
    payload: _SecretPayload


class SecretClient(Protocol):
    def access_secret_version(self, *, name: str) -> _SecretResponse: ...


def get_secret(config: StoreConfig, key: str, client: SecretClient | None = None) -> str:
    """Read the latest version of the secret named at ``secrets.<key>`` in store.yaml.

    The value is registered with the log redaction filter before it is returned and is
    never logged.
    """
    secret_name: str = config.require(f"secrets.{key}")
    if client is None:  # pragma: no cover - needs real GCP credentials
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{config.gcp.project_id}/secrets/{secret_name}/versions/latest"
    value = client.access_secret_version(name=resource).payload.data.decode("utf-8")
    register_secret(value)
    return value
