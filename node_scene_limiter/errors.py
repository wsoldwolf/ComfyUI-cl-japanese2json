"""Errors raised by the reduced-Markdown Scene limiter."""

from ..common.errors import CLNodeError


class SceneLimiterError(CLNodeError):
    """The Scene limit or reduced-Markdown input is invalid."""
