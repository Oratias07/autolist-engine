from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from autolist.core import logging as alog


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(alog.RedactionFilter())
    handler.setFormatter(alog.JsonFormatter())
    logger = logging.getLogger("autolist.test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    alog._clear_registered_secrets()
    yield logger, stream
    alog._clear_registered_secrets()


def test_authorization_header_redacted(capture: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = capture
    logger.info("request headers: Authorization: Bearer abc123xyz")
    logger.info("raw bearer token Bearer zzz999")
    out = stream.getvalue()
    assert "abc123xyz" not in out and "zzz999" not in out
    assert "[REDACTED]" in out


def test_sensitive_keys_redacted(capture: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = capture
    logger.info('response body {"api_token": "x-value-1", "name": "ok"}')
    logger.info("call", extra={"payload": {"api_token": "x-value-2", "items": [{"secret": "x3"}]}})
    logger.info("call", extra={"refresh_token": "x-value-4"})
    logger.info("args %s", {"password": "x-value-5"})
    out = stream.getvalue()
    for leaked in ("x-value-1", "x-value-2", "x3", "x-value-4", "x-value-5"):
        assert leaked not in out
    first = json.loads(out.splitlines()[0])
    assert '"name": "ok"' in first["message"]


def test_registered_secret_value_redacted_everywhere(
    capture: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = capture
    alog.register_secret("LiteralSecretValue42")
    alog.register_secret("")  # ignored
    logger.info("message with LiteralSecretValue42 inside")
    logger.info("extra", extra={"note": "LiteralSecretValue42", "rows": ("LiteralSecretValue42",)})
    try:
        raise RuntimeError("failed with LiteralSecretValue42")
    except RuntimeError:
        logger.exception("boom")
    out = stream.getvalue()
    assert "LiteralSecretValue42" not in out
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert lines[-1]["severity"] == "ERROR" and "exception" in lines[-1]


def test_configure_logging_installs_filtered_json_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alog.configure_logging()
    alog.get_logger("autolist.test").info("token=abc Authorization: Bearer q")
    err = capsys.readouterr().err
    record = json.loads(err.strip().splitlines()[-1])
    assert "abc" not in record["message"] and "Bearer q" not in record["message"]
    logging.getLogger().handlers = []
