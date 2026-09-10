"""Shared user-visible logging helpers for CL ComfyUI nodes."""

from __future__ import annotations

import logging
from typing import Any


ANSI_CYAN = "\x1b[96m"
ANSI_RESET = "\x1b[0m"


def log_cyan(logger: logging.Logger, message: str, *args: Any) -> None:
    """Write one INFO record whose complete user-visible message is cyan."""

    logger.info("%s" + message + "%s", ANSI_CYAN, *args, ANSI_RESET)


def log_node_success(
    logger: logging.Logger,
    component: str,
    message: str,
    *args: Any,
) -> None:
    """Write a consistent cyan completion record for a successful node run."""

    log_cyan(logger, f"[{component}] success: {message}", *args)
