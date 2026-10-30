"""Load prompt files from ``$AUTOLIST_STORE_DIR/agent/prompts`` and render them.

Front matter declares ``variables`` and ``untrusted``. Untrusted text (emails, web pages,
invoices) is wrapped in ``<untrusted name="...">`` delimiters with any closing tag inside it
escaped, so it can only ever be data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from autolist.core.config import store_dir
from autolist.llm.errors import PromptMissing, PromptVariableError

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CLOSE_TAG = re.compile(r"</\s*untrusted", re.IGNORECASE)
_NAME = re.compile(r"[a-z][a-z0-9_]*")

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: Any
    variables: tuple[str, ...]
    untrusted: frozenset[str]
    body: str


def wrap_untrusted(name: str, text: str) -> str:
    safe = _CLOSE_TAG.sub("&lt;/untrusted", text)
    return f'<untrusted name="{name}">\n{safe}\n</untrusted>'


def parse_prompt(name: str, raw: str) -> Prompt:
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise PromptMissing(f"prompt {name}: no front matter")
    meta = yaml.safe_load(match["meta"]) or {}
    if not isinstance(meta, dict):
        raise PromptMissing(f"prompt {name}: front matter is not a mapping")
    if meta.get("status") == "not-written":
        raise PromptMissing(f"prompt {name}: status is not-written")
    body = match["body"]
    if not _HTML_COMMENT.sub("", body).strip():
        raise PromptMissing(f"prompt {name}: empty body")
    variables = tuple(meta.get("variables") or [])
    untrusted = frozenset(meta.get("untrusted") or [])
    if not untrusted <= set(variables):
        raise PromptVariableError(f"prompt {name}: untrusted names must also be variables")
    return Prompt(name, meta.get("version"), variables, untrusted, body)


def load_prompt(name: str, prompts_dir: Path | None = None) -> Prompt:
    if not _NAME.fullmatch(name):
        raise PromptMissing(f"invalid prompt name: {name!r}")
    folder = prompts_dir if prompts_dir is not None else store_dir() / "agent" / "prompts"
    file = folder / f"{name}.md"
    if not file.is_file():
        raise PromptMissing(f"prompt file not found: {name}.md")
    return parse_prompt(name, file.read_text(encoding="utf-8"))


def render(prompt: Prompt, variables: dict[str, str]) -> str:
    declared, given = set(prompt.variables), set(variables)
    if missing := declared - given:
        raise PromptVariableError(f"prompt {prompt.name}: missing variables {sorted(missing)}")
    if extra := given - declared:
        raise PromptVariableError(f"prompt {prompt.name}: undeclared variables {sorted(extra)}")
    values = {
        k: (wrap_untrusted(k, v) if k in prompt.untrusted else v) for k, v in variables.items()
    }
    try:
        return _env.from_string(prompt.body).render(**values)
    except TemplateError as exc:
        raise PromptVariableError(f"prompt {prompt.name}: {exc}") from exc
