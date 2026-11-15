from pathlib import Path

import pytest

from autolist.orchestrator.main import PROMPTS_IN_USE, REPLIES_IN_USE, text_problems


def _write(folder: Path, name: str, status: str, subject: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    meta = f"status: {status}\nvariables: []\n" + ("subject: s\n" if subject else "")
    (folder / f"{name}.md").write_text(f"---\n{meta}---\nbody\n", encoding="utf-8")


def test_every_text_in_use_must_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOLIST_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOLIST_ALLOW_DRAFTS", raising=False)
    for name in PROMPTS_IN_USE:
        _write(tmp_path / "agent" / "prompts", name, "approved", subject=False)
    for name in REPLIES_IN_USE:
        _write(tmp_path / "agent" / "replies", name, "approved_dev", subject=True)
    _write(tmp_path / "agent" / "replies", "_signature", "approved_dev", subject=False)
    assert text_problems() == []
    _write(tmp_path / "agent" / "replies", "received", "draft", subject=True)
    (tmp_path / "agent" / "prompts" / "classify_reply.md").unlink()
    assert text_problems() == [
        "prompt classify_reply: PromptMissing",
        "reply received: TemplateMissing",
    ]
