from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from autolist.core import config as config_mod
from autolist.core.config import get_secret, load_store_config, parse_store_config
from autolist.core.errors import ConfigInvalid, ConfigMissing
from autolist.core.logging import _clear_registered_secrets, redact_text
from tests.core.conftest import VALID_STORE, key_paths, with_value, without


def test_valid_store_parses(store_data: dict[str, Any]) -> None:
    cfg = parse_store_config(store_data)
    assert cfg.istores.company_id == 1
    assert cfg.publishing.unhide_enabled is False


@pytest.mark.parametrize("path", key_paths(VALID_STORE))
def test_every_required_key_missing_raises_naming_it(path: str) -> None:
    with pytest.raises(ConfigMissing) as exc:
        parse_store_config(without(VALID_STORE, path))
    assert exc.value.key == path
    assert exc.value.code == "config_missing"


def test_unknown_key_is_invalid(store_data: dict[str, Any]) -> None:
    store_data["istores"]["typo_key"] = 1
    with pytest.raises(ConfigInvalid) as exc:
        parse_store_config(store_data)
    assert exc.value.key == "istores.typo_key"


def test_wrong_type_is_invalid() -> None:
    with pytest.raises(ConfigInvalid) as exc:
        parse_store_config(with_value(VALID_STORE, "istores.company_id", "not-a-number"))
    assert exc.value.key == "istores.company_id"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("costs.alert_thresholds", [40, 30]),
        ("costs.alert_thresholds", [30, 60]),
        ("costs.monthly_cap", 0),
        ("channels.email.allowed_senders", []),
        ("channels.email.require", {"dkim": "fail", "dmarc": "pass"}),
    ],
)
def test_invalid_values(path: str, value: Any) -> None:
    with pytest.raises(ConfigInvalid):
        parse_store_config(with_value(VALID_STORE, path, value))


def test_publishing_switch_needs_who_and_when() -> None:
    data = with_value(VALID_STORE, "publishing.unhide_enabled", True)
    with pytest.raises(ConfigInvalid):
        parse_store_config(data)
    data = with_value(data, "publishing.enabled_by", "owner")
    data = with_value(data, "publishing.enabled_at", "2026-11-01")
    assert parse_store_config(data).publishing.unhide_enabled is True


def test_require_null_value_raises(store_data: dict[str, Any]) -> None:
    cfg = parse_store_config(store_data)
    with pytest.raises(ConfigMissing) as exc:
        cfg.require("llm.models.extract")
    assert exc.value.key == "llm.models.extract"


def test_require_present_value_and_dict_paths(store_data: dict[str, Any]) -> None:
    cfg = parse_store_config(store_data)
    assert cfg.require("secrets.istores_token") == "istores-token"
    assert str(cfg.require("costs.phase0_budget.gemini")) == "3"
    for bad in ("costs.phase0_budget.other", "nope", "store.name.deeper", "llm.prices.x"):
        with pytest.raises(ConfigMissing):
            cfg.require(bad)


def test_load_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "store.yaml").write_text(yaml.safe_dump(VALID_STORE), encoding="utf-8")
    monkeypatch.setenv(config_mod.STORE_DIR_ENV, str(tmp_path))
    assert load_store_config().store.name == "Example Store"


def test_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config_mod.STORE_DIR_ENV, raising=False)
    with pytest.raises(ConfigMissing) as exc:
        load_store_config()
    assert exc.value.key == config_mod.STORE_DIR_ENV


def test_file_missing_and_not_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigMissing):
        load_store_config(tmp_path / "store.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigInvalid):
        load_store_config(bad)


class _FakePayload:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.payload = _FakePayload(data)


class _FakeSecretClient:
    def __init__(self) -> None:
        self.names: list[str] = []

    def access_secret_version(self, *, name: str) -> _FakeResponse:
        self.names.append(name)
        return _FakeResponse(b"s3cr3t-value-for-test")


def test_get_secret_reads_latest_and_registers_for_redaction(store_data: dict[str, Any]) -> None:
    _clear_registered_secrets()
    cfg = parse_store_config(store_data)
    client = _FakeSecretClient()
    value = get_secret(cfg, "istores_token", client=client)
    assert value == "s3cr3t-value-for-test"
    assert client.names == ["projects/example-project/secrets/istores-token/versions/latest"]
    assert "s3cr3t-value-for-test" not in redact_text("leaked s3cr3t-value-for-test here")
    _clear_registered_secrets()


def test_get_secret_unknown_key(store_data: dict[str, Any]) -> None:
    cfg = parse_store_config(store_data)
    with pytest.raises(ConfigMissing):
        get_secret(cfg, "no_such_secret", client=_FakeSecretClient())
