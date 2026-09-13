"""Errors raised by the connection-aware combo node."""

from __future__ import annotations


class ConnectedComboError(ValueError):
    """Raised when downstream enum metadata or the selection is invalid."""

