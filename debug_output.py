"""Opt-in diagnostic bundle writer for ComfyUI's output directory."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
import uuid


def _comfy_output_directory() -> Path:
    try:
        import folder_paths  # type: ignore
    except Exception as exc:
        raise RuntimeError("ComfyUI folder_paths is unavailable") from exc
    getter = getattr(folder_paths, "get_output_directory", None)
    if not callable(getter):
        raise RuntimeError("ComfyUI does not provide get_output_directory()")
    output_directory = Path(getter())
    if not output_directory.is_dir():
        raise RuntimeError(
            f"ComfyUI output directory does not exist: {output_directory}"
        )
    return output_directory


def _write_exact(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def save_debug_bundle(
    *,
    plain_text: str,
    system_prompt: str,
    model_name: str,
    settings: dict[str, Any],
    events: list[dict[str, Any]],
    canonical_markdown: str | None = None,
    json_text: str | None = None,
    error: Exception | None = None,
    output_directory: Path | None = None,
) -> Path:
    """Write exact text artifacts without placing generated data in ComfyUI/input."""

    root = (
        output_directory
        if output_directory is not None
        else _comfy_output_directory()
    )
    debug_root = root / "cl_japanese2json_debug"
    debug_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    target = debug_root / f"{timestamp}_{uuid.uuid4().hex[:8]}"
    target.mkdir()

    _write_exact(target / "source.md", plain_text)
    _write_exact(target / "system_prompt.txt", system_prompt)
    if canonical_markdown is not None:
        _write_exact(target / "canonical.md", canonical_markdown)
    if json_text is not None:
        _write_exact(target / "result.json", json_text)
    if error is not None:
        _write_exact(target / "error.txt", f"{type(error).__name__}: {error}\n")

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "model_name": model_name,
        "settings": settings,
        "status": "error" if error is not None else "success",
        "event_count": len(events),
    }
    _write_exact(
        target / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )

    for index, event in enumerate(events, start=1):
        prefix = (
            f"event_{index:02d}_batch_{int(event.get('batch', 0)):02d}"
            f"_attempt_{int(event.get('attempt', 0)):02d}"
        )
        protected_stream = event.get("protected_stream")
        if isinstance(protected_stream, str):
            _write_exact(target / f"{prefix}_protected_stream.txt", protected_stream)
        user_request = event.get("user_request")
        if isinstance(user_request, str):
            _write_exact(target / f"{prefix}_request.txt", user_request)
        response_content = event.get("response_content")
        if isinstance(response_content, str):
            _write_exact(target / f"{prefix}_response.txt", response_content)
        metadata = {
            key: value
            for key, value in event.items()
            if key not in {"protected_stream", "user_request", "response_content"}
        }
        _write_exact(
            target / f"{prefix}_metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        )

    return target
