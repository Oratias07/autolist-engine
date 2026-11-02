"""Errors raised by channels. All inherit from ``AutolistError``."""

from __future__ import annotations

from autolist.core.errors import AutolistError


class SenderRejected(AutolistError):
    code = "sender_rejected"


class RecipientNotAllowed(AutolistError):
    code = "recipient_not_allowed"


class ChannelAuthError(AutolistError):
    """The channel's own credentials failed (e.g. a revoked or expired refresh token)."""

    code = "channel_auth_error"


class TemplateMissing(AutolistError):
    code = "template_missing"


class TemplateVariableError(AutolistError):
    code = "template_variable_error"


class AttachmentRejected(AutolistError):
    code = "attachment_rejected"
