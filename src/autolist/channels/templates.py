"""Reply templates from ``$AUTOLIST_STORE_DIR/agent/replies``.

Each template has front matter with ``subject`` and ``variables``. Rendering is strict:
a missing or undeclared variable is an error. The automated-agent signature
(``_signature.md``) is appended to every body; templates never include it themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from autolist.channels.errors import TemplateMissing, TemplateVariableError
from autolist.core.config import store_dir

_FRONT = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_NAME = re.compile(r"_?[a-z][a-z0-9_]*")
_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


@dataclass(frozen=True)
class RenderedReply:
    subject: str
    body: str


@dataclass(frozen=True)
class ReplyTemplate:
    name: str
    subject: str
    variables: tuple[str, ...]
    body: str


def _read(name: str, folder: Path | None) -> tuple[dict[str, object], str]:
    if not _NAME.fullmatch(name):
        raise TemplateMissing(f"invalid template name: {name!r}")
    base = folder if folder is not None else store_dir() / "agent" / "replies"
    file = base / f"{name}.md"
    if not file.is_file():
        raise TemplateMissing(f"reply template not found: {name}.md")
    match = _FRONT.match(file.read_text(encoding="utf-8"))
    if match is None:
        raise TemplateMissing(f"reply template {name}: no front matter")
    meta = yaml.safe_load(match["meta"]) or {}
    if not isinstance(meta, dict):
        raise TemplateMissing(f"reply template {name}: front matter is not a mapping")
    if meta.get("status") == "not-written":
        raise TemplateMissing(f"reply template {name}: status is not-written")
    body = _COMMENT.sub("", match["body"]).strip()
    if not body:
        raise TemplateMissing(f"reply template {name}: empty body")
    return meta, body


def load_reply(name: str, folder: Path | None = None) -> ReplyTemplate:
    meta, body = _read(name, folder)
    subject = meta.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise TemplateMissing(f"reply template {name}: no subject")
    raw_vars = meta.get("variables") or []
    if not isinstance(raw_vars, list):
        raise TemplateMissing(f"reply template {name}: variables must be a list")
    variables = tuple(str(v) for v in raw_vars)
    return ReplyTemplate(name, subject, variables, body)


def render_reply(name: str, variables: dict[str, str], folder: Path | None = None) -> RenderedReply:
    tpl = load_reply(name, folder)
    declared, given = set(tpl.variables), set(variables)
    if missing := declared - given:
        raise TemplateVariableError(f"reply {name}: missing variables {sorted(missing)}")
    if extra := given - declared:
        raise TemplateVariableError(f"reply {name}: undeclared variables {sorted(extra)}")
    try:
        subject = _env.from_string(tpl.subject).render(**variables).strip()
        body = _env.from_string(tpl.body).render(**variables).strip()
    except TemplateError as exc:
        raise TemplateVariableError(f"reply {name}: {exc}") from exc
    _, signature = _read("_signature", folder)
    return RenderedReply(subject=subject, body=f"{body}\n\n{signature}\n")
