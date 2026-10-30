"""Structured JSON logging with redaction of credentials and loaded secret values."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"token|key|secret|password|bearer", re.IGNORECASE)
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(\"?)[^\"\r\n,}]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]+")
_KV_IN_TEXT = re.compile(
    r"""(?P<q>["']?)(?P<k>[A-Za-z0-9_\-]*(?:token|key|secret|password|bearer)[A-Za-z0-9_\-]*)(?P=q)"""
    r"""(?P<sep>\s*[:=]\s*)(?P<vq>["']?)(?P<v>[^"'\s,}\]]+)(?P=vq)""",
    re.IGNORECASE,
)
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Remember a secret value so it is replaced wherever it appears in a log record."""
    if value:
        with _secrets_lock:
            _secrets.add(value)


def _clear_registered_secrets() -> None:  # test helper
    with _secrets_lock:
        _secrets.clear()


def redact_text(text: str) -> str:
    with _secrets_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for value in known:
        text = text.replace(value, REDACTED)
    text = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    return _KV_IN_TEXT.sub(
        lambda m: f"{m['q']}{m['k']}{m['q']}{m['sep']}{m['vq']}{REDACTED}{m['vq']}", text
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [redact_value(v) for v in value]
    return value


class RedactionFilter(logging.Filter):
    """Rewrites the record in place: message, args and extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        for name in list(vars(record)):
            if name in _STANDARD_ATTRS:
                continue
            if _SENSITIVE_KEY.search(name):
                setattr(record, name, REDACTED)
            else:
                setattr(record, name, redact_value(getattr(record, name)))
        if record.exc_info:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, value in vars(record).items():
            if name not in _STANDARD_ATTRS:
                payload[name] = value
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install one JSON handler with the redaction filter on the root logger."""
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
