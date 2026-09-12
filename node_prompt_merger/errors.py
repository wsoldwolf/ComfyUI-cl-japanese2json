"""Errors raised by the deterministic reduced-Markdown prompt merger."""

from ..common.errors import CLNodeError


class PromptMergerError(CLNodeError):
    """One of the prompt fragments or merge rules is invalid."""
