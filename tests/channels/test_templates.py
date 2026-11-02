from __future__ import annotations

from pathlib import Path

import pytest

from autolist.channels.errors import TemplateMissing, TemplateVariableError
from autolist.channels.templates import load_reply, render_reply
from autolist.core import config as config_mod
from tests.channels.conftest import REPLIES


def test_render_strict_and_signature_appended() -> None:
    r = render_reply("cost_alert", {"month_spend": "31.20", "monthly_cap": "60"}, REPLIES)
    assert r.subject == "[Store] spend update - 31.20 USD"
    assert r.body.startswith("Hello,") and "31.20 USD of 60 USD" in r.body
    assert r.body.rstrip().endswith("Example Store agent (automated)")
    with pytest.raises(TemplateVariableError, match="missing"):
        render_reply("cost_alert", {"month_spend": "1"}, REPLIES)
    with pytest.raises(TemplateVariableError, match="undeclared"):
        render_reply("cost_alert", {"month_spend": "1", "monthly_cap": "2", "x": "3"}, REPLIES)


def test_unusable_templates(tmp_path: Path) -> None:
    for name in ("absent", "not_ready", "../x"):
        with pytest.raises(TemplateMissing):
            load_reply(name, REPLIES)
    (tmp_path / "nosubject.md").write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
    (tmp_path / "nofront.md").write_text("body only\n", encoding="utf-8")
    (tmp_path / "badvars.md").write_text("---\nsubject: s\nvariables: x\n---\nb\n", "utf-8")
    (tmp_path / "listmeta.md").write_text("---\n- a\n---\nb\n", encoding="utf-8")
    (tmp_path / "empty.md").write_text("---\nsubject: s\n---\n<!-- c -->\n", encoding="utf-8")
    for name in ("nosubject", "nofront", "badvars", "listmeta", "empty"):
        with pytest.raises(TemplateMissing):
            load_reply(name, tmp_path)


def test_template_error_and_env_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "agent" / "replies").mkdir(parents=True)
    folder = tmp_path / "agent" / "replies"
    (folder / "bad.md").write_text("---\nsubject: s\nvariables: []\n---\n{{ x.y }}\n", "utf-8")
    (folder / "_signature.md").write_text("---\nname: s\n---\n--\nsig\n", "utf-8")
    monkeypatch.setenv(config_mod.STORE_DIR_ENV, str(tmp_path))
    with pytest.raises(TemplateVariableError):
        render_reply("bad", {})
