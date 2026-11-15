from pathlib import Path

import pytest

from autolist.core.errors import ConfigMissing
from autolist.core.playbook import load_glossary, load_playbook, load_voice_principles, require


def _store(tmp: Path, voice: str) -> Path:
    (tmp / "playbook").mkdir()
    (tmp / "docs").mkdir()
    (tmp / "playbook" / "playbook.yaml").write_text("listing:\n  required: [name]\n  x: null\n")
    (tmp / "playbook" / "glossary.yaml").write_text("terms: []\n")
    (tmp / "docs" / "voice.md").write_text(voice, encoding="utf-8")
    return tmp


def test_loaders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voice = "intro\n**עקרונות הקול:**\n- ענייני\n\n**מילון מונחים.** rest"
    monkeypatch.setenv("AUTOLIST_STORE_DIR", str(_store(tmp_path, voice)))
    pb = load_playbook()
    assert require(pb, "listing.required") == ["name"]
    with pytest.raises(ConfigMissing):
        require(pb, "listing.x")
    with pytest.raises(ConfigMissing):
        require(pb, "nope.y")
    assert load_glossary() == {"terms": []}
    assert load_voice_principles() == "**עקרונות הקול:**\n- ענייני"


def test_voice_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOLIST_STORE_DIR", str(_store(tmp_path, "no markers")))
    with pytest.raises(ConfigMissing):
        load_voice_principles()


def test_non_mapping_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, "")
    (store / "playbook" / "playbook.yaml").write_text("- a\n")
    (store / "playbook" / "glossary.yaml").write_text("- a\n")
    monkeypatch.setenv("AUTOLIST_STORE_DIR", str(store))
    with pytest.raises(ConfigMissing):
        load_playbook()
    with pytest.raises(ConfigMissing):
        load_glossary()


def test_field_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autolist.core.playbook import load_field_labels

    store = _store(tmp_path, "")
    (store / "agent").mkdir()
    (store / "agent" / "field_labels.yaml").write_text("labels:\n  price: מחיר\n", encoding="utf-8")
    monkeypatch.setenv("AUTOLIST_STORE_DIR", str(store))
    assert load_field_labels() == {"price": "מחיר"}
    (store / "agent" / "field_labels.yaml").write_text("labels:\n  price: 1\n")
    with pytest.raises(ConfigMissing):
        load_field_labels()
