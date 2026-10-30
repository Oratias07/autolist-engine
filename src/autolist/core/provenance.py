"""Provenance helpers: every non-null value must say where it came from."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from autolist.core.errors import MissingProvenance
from autolist.core.models import Provenance, SourcedValue


def sourced[T](value: T, *provenance: Provenance) -> SourcedValue[T]:
    """A known value with at least one source."""
    if not provenance:
        raise MissingProvenance(["<value>"])
    return SourcedValue[T](value=value, provenance=list(provenance))


def unknown() -> SourcedValue[Any]:
    """An unknown value. It becomes a question to the owner, never a default."""
    return SourcedValue[Any](value=None, provenance=[])


def find_unsourced(obj: Any, path: str = "") -> list[str]:
    """Paths of every ``SourcedValue`` (at any depth) that has a value but no source."""
    found: list[str] = []
    if isinstance(obj, SourcedValue):
        if obj.value is not None and not obj.provenance:
            found.append(path or "<root>")
        found.extend(find_unsourced(obj.value, f"{path}.value" if path else "value"))
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            found.extend(find_unsourced(getattr(obj, name), f"{path}.{name}" if path else name))
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            found.extend(find_unsourced(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(obj, Sequence) and not isinstance(obj, str | bytes):
        for i, value in enumerate(obj):
            found.extend(find_unsourced(value, f"{path}[{i}]"))
    return found


def require_sources(obj: Any) -> None:
    """Raise ``MissingProvenance`` listing every value that has no source."""
    paths = find_unsourced(obj)
    if paths:
        raise MissingProvenance(paths)
