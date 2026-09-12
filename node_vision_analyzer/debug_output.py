"""Opt-in diagnostic bundle writer for Vision observations."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
import uuid


def _output_directory() -> Path:
    import folder_paths  # type: ignore

    path = Path(folder_paths.get_output_directory())
    if not path.is_dir():
        raise RuntimeError(f"ComfyUI output directory does not exist: {path}")
    return path


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", newline="\n")


def save_vision_debug_bundle(
    *,
    image_name: str,
    image_hash: str | None,
    model_name: str,
    projector_name: str | None,
    settings: dict[str, Any],
    system_prompt: str | None,
    events: list[dict[str, Any]],
    observation: dict[str, Any] | None,
    result: str | None,
    status: str | None,
    error: Exception | None,
    output_directory: Path | None = None,
) -> Path:
    root = output_directory or _output_directory()
    debug_root = root / "cl_vision_analyzer_debug"
    debug_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    target = debug_root / f"{stamp}_{uuid.uuid4().hex[:8]}"
    target.mkdir()

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "image_name": image_name,
        "image_hash": image_hash,
        "model_name": model_name,
        "projector_name": projector_name,
        "settings": settings,
        "event_count": len(events),
        "status": "error" if error is not None else "success",
    }
    _write(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if system_prompt is not None:
        _write(target / "system_prompt.txt", system_prompt)
    request_summary = [
        {
            "attempt": event.get("attempt"),
            "seed": event.get("seed"),
            "analysis_size": event.get("analysis_size"),
            "request": event.get("request"),
        }
        for event in events
        if isinstance(event.get("request"), str)
    ]
    if request_summary:
        _write(
            target / "request.json",
            json.dumps(request_summary, ensure_ascii=False, indent=2) + "\n",
        )
    responses = [
        event["response"]
        for event in events
        if isinstance(event.get("response"), str)
    ]
    if responses:
        _write(target / "response.txt", responses[-1])
    for index, event in enumerate(events, start=1):
        response = event.get("response")
        request = event.get("request")
        if isinstance(request, str):
            _write(target / f"event_{index:02d}_request.txt", request)
        if isinstance(response, str):
            _write(target / f"event_{index:02d}_response.txt", response)
        metadata = {key: value for key, value in event.items() if key not in {"request", "response"}}
        _write(target / f"event_{index:02d}_metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    if observation is not None:
        _write(target / "observation.json", json.dumps(observation, ensure_ascii=False, indent=2) + "\n")
    if result is not None:
        _write(target / "result.txt", result)
    if status is not None:
        _write(target / "status.txt", status)
    if error is not None:
        _write(target / "error.txt", f"{type(error).__name__}: {error}\n")
    return target
