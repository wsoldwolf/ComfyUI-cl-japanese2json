"""External system-prompt loading and fingerprinting."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading

from .compiler.errors import SystemPromptError


NODE_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = NODE_DIR / "prompts" / "llmj2e_qwen3_8b_system_prompt.txt"
_PROMPT_LOCK = threading.RLock()
_PROMPT_CACHE: tuple[int, int, str, str] | None = None


def system_prompt_fingerprint() -> tuple[object, ...]:
    try:
        data = SYSTEM_PROMPT_PATH.read_bytes()
        stat = SYSTEM_PROMPT_PATH.stat()
    except OSError as exc:
        return ("system-prompt-error", str(SYSTEM_PROMPT_PATH), type(exc).__name__)
    return (stat.st_size, stat.st_mtime_ns, hashlib.sha256(data).hexdigest())


def load_system_prompt() -> str:
    global _PROMPT_CACHE
    try:
        data = SYSTEM_PROMPT_PATH.read_bytes()
        stat = SYSTEM_PROMPT_PATH.stat()
    except OSError as exc:
        raise SystemPromptError(
            f"System prompt file is unavailable at: {SYSTEM_PROMPT_PATH}"
        ) from exc
    digest = hashlib.sha256(data).hexdigest()

    with _PROMPT_LOCK:
        if (
            _PROMPT_CACHE is not None
            and _PROMPT_CACHE[0] == stat.st_mtime_ns
            and _PROMPT_CACHE[1] == stat.st_size
            and _PROMPT_CACHE[2] == digest
        ):
            return _PROMPT_CACHE[3]
        try:
            prompt = data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise SystemPromptError(
                f"System prompt must be readable UTF-8 at: {SYSTEM_PROMPT_PATH}"
            ) from exc
        if prompt.strip() == "":
            raise SystemPromptError(f"System prompt is empty at: {SYSTEM_PROMPT_PATH}")
        _PROMPT_CACHE = (stat.st_mtime_ns, stat.st_size, digest, prompt)
        return prompt
