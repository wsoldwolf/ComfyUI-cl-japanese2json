"""A workflow-persistent seed source shared by 32-bit and 64-bit consumers."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from ..common.logging import log_node_success
from .errors import Seed32Error


LOGGER = logging.getLogger("cl_seed32")
MAX_SEED_32 = (1 << 31) - 1
MODES = ("fixed", "random")


def random_seed32() -> int:
    """Return an unbiased, nonzero signed 32-bit seed."""

    return secrets.randbelow(MAX_SEED_32) + 1


def resolve_seed32(seed: int, mode: str, hold_next: bool) -> int:
    """Resolve one execution without mutating workflow state."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise Seed32Error("seed must be an integer")
    if seed < -1 or seed > MAX_SEED_32:
        raise Seed32Error(f"seed must be -1 or an integer between 1 and {MAX_SEED_32}")
    if seed == 0:
        raise Seed32Error("seed must not be 0; use -1 to request a random seed")
    if mode not in MODES:
        raise Seed32Error("mode must be fixed or random")
    if not isinstance(hold_next, bool):
        raise Seed32Error("hold_next must be a boolean")
    if seed == -1 or (mode == "random" and not hold_next):
        return random_seed32()
    return seed


class CLSeed32:
    """Generate one common seed and expose it as a normal ComfyUI INT."""

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("seed",)
    FUNCTION = "generate_seed"
    CATEGORY = "MiniMax H3/Utilities"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "seed": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": MAX_SEED_32,
                        "step": 1,
                        "control_after_generate": False,
                        "tooltip": "-1 generates a random seed; valid shared seeds are 1..INT_MAX.",
                    },
                ),
                "mode": (list(MODES), {"default": "random"}),
                # Serialized workflow transport for the one-run hold created by
                # the random button. The browser extension hides this widget.
                "hold_next": ("BOOLEAN", {"default": False}),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, seed: int, mode: str, hold_next: bool) -> bool | str:
        try:
            # Validate without consuming entropy.
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise Seed32Error("seed must be an integer")
            if seed == 0:
                raise Seed32Error("seed must not be 0; use -1 to request a random seed")
            if seed < -1 or seed > MAX_SEED_32:
                raise Seed32Error(
                    f"seed must be -1 or an integer between 1 and {MAX_SEED_32}"
                )
            if mode not in MODES:
                raise Seed32Error("mode must be fixed or random")
            if not isinstance(hold_next, bool):
                raise Seed32Error("hold_next must be a boolean")
        except Seed32Error as exc:
            return str(exc)
        return True

    @classmethod
    def IS_CHANGED(
        cls, seed: int, mode: str, hold_next: bool = False, **_: Any
    ) -> Any:
        # NaN deliberately bypasses ComfyUI's execution cache. A fixed seed or
        # the random button's one-run hold remains cacheable and reproducible.
        if seed == -1 or (mode == "random" and not hold_next):
            return float("nan")
        return (seed, mode, hold_next)

    @staticmethod
    def generate_seed(seed: int, mode: str, hold_next: bool) -> dict[str, Any]:
        resolved = resolve_seed32(seed, mode, hold_next)
        log_node_success(LOGGER, "cl_seed32", "using seed %d (%s)", resolved, mode)
        return {
            "ui": {
                "seed": [resolved],
                "mode": [mode],
                "hold_next": [False],
            },
            "result": (resolved,),
        }


__all__ = ["CLSeed32", "MAX_SEED_32", "MODES", "random_seed32", "resolve_seed32"]
