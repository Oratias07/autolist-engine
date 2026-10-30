"""Errors raised by the llm module. All inherit from ``AutolistError``."""

from __future__ import annotations

from autolist.core.errors import AutolistError


class PromptVariableError(AutolistError):
    code = "prompt_variable_error"


class PromptMissing(AutolistError):
    """The prompt file does not exist, has no front matter, or is not written yet."""

    code = "prompt_missing"


class LLMOutputInvalid(AutolistError):
    code = "llm_output_invalid"

    def __init__(self, message: str, raw_text: str) -> None:
        self.raw_text = raw_text
        super().__init__(message)


class LLMUnavailable(AutolistError):
    code = "llm_unavailable"
