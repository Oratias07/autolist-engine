"""Error hierarchy for autolist. Every error carries a machine-readable ``code``."""

from __future__ import annotations

from collections.abc import Sequence


class AutolistError(Exception):
    """Base class for all autolist errors."""

    code: str = "autolist_error"


class ConfigMissing(AutolistError):
    """A required config key is absent, or a needed value is null (unknown)."""

    code = "config_missing"

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"config value missing: {key}")


class ConfigInvalid(AutolistError):
    """A config key is present but its value has the wrong type or range."""

    code = "config_invalid"

    def __init__(self, key: str, problem: str) -> None:
        self.key = key
        self.problem = problem
        super().__init__(f"config value invalid: {key}: {problem}")


class MissingProvenance(AutolistError):
    """One or more fields carry a value without a recorded source."""

    code = "missing_provenance"

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = list(paths)
        super().__init__(f"fields without provenance: {', '.join(self.paths)}")


class IllegalTransition(AutolistError):
    """A job state transition that the state machine does not allow (phase 1)."""

    code = "illegal_transition"


class CostCapExceeded(AutolistError):
    """A paid call was refused because it would exceed a cost cap."""

    code = "cost_cap_exceeded"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class JobLocked(AutolistError):
    """The job is locked by another worker (phase 1)."""

    code = "job_locked"
