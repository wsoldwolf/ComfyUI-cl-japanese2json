"""UTF-8 loader for planner system prompts."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from .errors import MVPlannerError


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_NAMES = (
    "song_bible_system_prompt.txt",
    "scene_plan_system_prompt.txt",
)


def _prompt_path(name: str) -> Path:
    if not name or Path(name).name != name or name not in _PROMPT_NAMES:
        raise MVPlannerError("Invalid planner prompt filename")
    return _PROMPT_DIR / name


def planner_prompts_fingerprint() -> tuple[object, ...]:
    values: list[object] = []
    for name in _PROMPT_NAMES:
        path = _prompt_path(name)
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            values.extend(("planner-prompt-error", name, type(exc).__name__))
            continue
        values.extend(
            (name, stat.st_size, stat.st_mtime_ns, hashlib.sha256(data).hexdigest())
        )
    return tuple(values)


@lru_cache(maxsize=16)
def _load_cached(name: str, mtime_ns: int, size: int, digest: str) -> str:
    del mtime_ns, size, digest
    path = _prompt_path(name)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise MVPlannerError(f"Could not load planner prompt {name!r}: {exc}") from exc
    if not text.strip():
        raise MVPlannerError(f"Planner prompt {name!r} is empty")
    return text.strip()


def load_planner_prompt(name: str) -> str:
    path = _prompt_path(name)
    try:
        data = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise MVPlannerError(f"Could not load planner prompt {name!r}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    return _load_cached(name, stat.st_mtime_ns, stat.st_size, digest)
