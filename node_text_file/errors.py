"""Errors raised by the browser-backed text-file loader."""

from ..common.errors import CLNodeError


class TextFileLoadError(CLNodeError):
    """A browser-selected plain-text file could not be decoded safely."""
