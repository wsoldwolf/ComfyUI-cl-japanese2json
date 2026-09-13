"""Opt-in debug bundle for Prompt Enhancer requests and protected results."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import uuid
from typing import Any


def _output_root() -> Path:
    try:
        import folder_paths  # type: ignore
    except Exception as exc:
        raise RuntimeError("ComfyUI folder_paths is unavailable") from exc
    value = Path(folder_paths.get_output_directory())
    if not value.is_dir():
        raise RuntimeError(f"ComfyUI output directory does not exist: {value}")
    return value


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=repr) + "\n"


def save_enhancer_debug_bundle(
    *,
    source_markdown: str,
    user_prompt: str,
    settings: dict[str, Any],
    events: tuple[dict[str, Any], ...],
    enhanced_markdown: str | None,
    report: dict[str, Any] | None,
    error: Exception | None,
    output_directory: Path | None = None,
) -> Path:
    root = output_directory if output_directory is not None else _output_root()
    parent = root / "cl_prompt_enhancer_debug"
    parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    target = parent / f"{timestamp}_{uuid.uuid4().hex[:8]}"
    target.mkdir()
    _write(target / "source_markdown.md", source_markdown)
    _write(target / "user_prompt.md", user_prompt)
    _write(target / "settings.json", _json(settings))
    _write(target / "events.json", _json(events))
    if enhanced_markdown is not None:
        _write(target / "enhanced_markdown.md", enhanced_markdown)
    if report is not None:
        _write(target / "enhancement_report.json", _json(report))
    if error is not None:
        _write(target / "error.txt", f"{type(error).__name__}: {error}\n")
    return target
