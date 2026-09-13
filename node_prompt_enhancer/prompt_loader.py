"""Load and fingerprint the external Prompt Enhancer system prompt."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import PromptEnhancerError
from .profiles import BackgroundProfile, StyleProfile, enhancer_profiles_fingerprint
from ..common.semantic_review import review_fingerprint


_CORE_PATH = Path(__file__).resolve().parent / "prompts" / "core" / "enhancer_system_prompt.txt"


def _read_core() -> str:
    try:
        value = _CORE_PATH.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise PromptEnhancerError(f"Could not load Prompt Enhancer system prompt: {exc}") from exc
    if not value:
        raise PromptEnhancerError("Prompt Enhancer system prompt is empty")
    return value


def load_enhancer_system_prompt(
    style: StyleProfile,
    background: BackgroundProfile,
) -> str:
    return (
        f"{_read_core()}\n\n"
        f"SELECTED STYLE PROFILE: {style.profile_id}\n"
        f"{style.system_instruction}\n\n"
        f"SELECTED BACKGROUND PROFILE: {background.profile_id}\n"
        f"{background.system_instruction}"
    )


def enhancer_prompts_fingerprint() -> tuple[object, ...]:
    try:
        data = _CORE_PATH.read_bytes()
        stat = _CORE_PATH.stat()
        core: tuple[object, ...] = (
            str(_CORE_PATH),
            stat.st_mtime_ns,
            stat.st_size,
            hashlib.sha256(data).hexdigest(),
        )
    except OSError as exc:
        core = ("core-prompt-error", type(exc).__name__)
    return core + enhancer_profiles_fingerprint() + (review_fingerprint(),)
