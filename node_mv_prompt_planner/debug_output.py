"""Opt-in diagnostic bundle writer for MV planner requests and responses."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
import uuid


_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")


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


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=lambda item: repr(item),
    ) + "\n"


def _safe_label(value: Any) -> str:
    label = _SAFE_LABEL_RE.sub("_", str(value).strip()).strip("_")
    return label[:80] or "request"


def save_planner_debug_bundle(
    *,
    prompt_segments: str,
    planning_markdown: str,
    model_name: str,
    settings: dict[str, Any],
    events: list[dict[str, Any]],
    final_state: dict[str, Any],
    planned_markdown: str | None = None,
    planner_json: str | None = None,
    error: Exception | None = None,
    output_directory: Path | None = None,
) -> Path:
    """Save exact planner calls plus the last recoverable planning state."""

    root = (
        output_directory
        if output_directory is not None
        else _comfy_output_directory()
    )
    debug_root = root / "cl_mv_prompt_planner_debug"
    debug_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    target = debug_root / f"{timestamp}_{uuid.uuid4().hex[:8]}"
    target.mkdir()

    _write_exact(target / "prompt_segments.md", prompt_segments)
    _write_exact(target / "planning_markdown.md", planning_markdown)
    _write_exact(target / "final_state.json", _json_text(final_state))
    if planned_markdown is not None:
        _write_exact(target / "planned_markdown.md", planned_markdown)
    if planner_json is not None:
        _write_exact(target / "planner.json", planner_json)
    if error is not None:
        _write_exact(target / "error.txt", f"{type(error).__name__}: {error}\n")

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "model_name": model_name,
        "settings": settings,
        "status": "error" if error is not None else "success",
        "event_count": len(events),
    }
    _write_exact(target / "manifest.json", _json_text(manifest))

    for index, event in enumerate(events, start=1):
        prefix = f"event_{index:03d}_{_safe_label(event.get('label'))}"
        system_prompt = event.get("system_prompt")
        if isinstance(system_prompt, str):
            _write_exact(target / f"{prefix}_system_prompt.txt", system_prompt)
        request_payload = event.get("request_payload")
        if request_payload is not None:
            _write_exact(
                target / f"{prefix}_request.json", _json_text(request_payload)
            )
        response_content = event.get("response_content")
        if isinstance(response_content, str):
            _write_exact(
                target / f"{prefix}_response.txt", response_content
            )
        parsed_response = event.get("parsed_response")
        if parsed_response is not None:
            _write_exact(
                target / f"{prefix}_parsed_response.json",
                _json_text(parsed_response),
            )
        metadata = {
            key: value
            for key, value in event.items()
            if key
            not in {
                "system_prompt",
                "request_payload",
                "response_content",
                "parsed_response",
            }
        }
        _write_exact(target / f"{prefix}_metadata.json", _json_text(metadata))

    return target
