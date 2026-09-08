"""Local OpenAI Whisper checkpoint discovery below ComfyUI model roots."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any

from .compiler.errors import WhisperModelDiscoveryError


LOGGER = logging.getLogger("cl_vocal2promptseg")
NO_WHISPER_MODELS_PLACEHOLDER = "(no Whisper models found)"


@dataclass(frozen=True)
class WhisperModelRoot:
    identifier: str
    path: Path


def _folder_paths_module(folder_paths_module: Any | None = None) -> Any | None:
    if folder_paths_module is not None:
        return folder_paths_module
    try:
        import folder_paths  # type: ignore
    except Exception:
        return None
    return folder_paths


def whisper_model_roots(
    folder_paths_module: Any | None = None,
) -> list[WhisperModelRoot]:
    folder_paths = _folder_paths_module(folder_paths_module)
    if folder_paths is None:
        return []

    candidates: list[WhisperModelRoot] = []
    models_dir = getattr(folder_paths, "models_dir", None)
    if models_dir:
        candidates.append(
            WhisperModelRoot("models", Path(models_dir) / "whisper")
        )

    registered = getattr(folder_paths, "folder_names_and_paths", {})
    if isinstance(registered, dict) and "whisper" in registered:
        try:
            registered_paths = folder_paths.get_folder_paths("whisper")
        except Exception as exc:
            LOGGER.warning(
                "[cl_vocal2promptseg] Could not read additional ComfyUI "
                "Whisper model paths: %s",
                exc,
            )
            registered_paths = []
        for index, raw_root in enumerate(registered_paths, start=1):
            candidates.append(
                WhisperModelRoot(f"whisper{index}", Path(raw_root))
            )

    unique: list[WhisperModelRoot] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.path.resolve(strict=False)
        except (OSError, RuntimeError):
            resolved = candidate.path.absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _safe_model_files(
    root: WhisperModelRoot,
) -> list[tuple[WhisperModelRoot, Path, str]]:
    if not root.path.is_dir():
        return []
    results: list[tuple[WhisperModelRoot, Path, str]] = []
    try:
        for path in root.path.rglob("*"):
            try:
                if not path.is_file() or path.suffix.lower() != ".pt":
                    continue
                resolved = path.resolve(strict=True)
                if not resolved.is_file():
                    continue
                relative = path.relative_to(root.path).as_posix()
                results.append((root, resolved, relative))
            except (OSError, RuntimeError, ValueError):
                continue
    except OSError as exc:
        LOGGER.warning(
            "[cl_vocal2promptseg] Could not scan Whisper root %s: %s",
            root.path,
            exc,
        )
    return results


def discover_whisper_model_map(
    folder_paths_module: Any | None = None,
) -> dict[str, Path]:
    discovered: list[tuple[WhisperModelRoot, Path, str]] = []
    seen_paths: set[str] = set()
    for root in whisper_model_roots(folder_paths_module):
        for entry in _safe_model_files(root):
            real_key = os.path.normcase(str(entry[1]))
            if real_key in seen_paths:
                continue
            seen_paths.add(real_key)
            discovered.append(entry)

    relative_counts: dict[str, int] = {}
    for _, _, relative in discovered:
        key = relative.casefold()
        relative_counts[key] = relative_counts.get(key, 0) + 1

    model_map: dict[str, Path] = {}
    for root, path, relative in sorted(
        discovered,
        key=lambda item: (item[2].casefold(), item[0].identifier.casefold()),
    ):
        display_id = (
            relative
            if relative_counts[relative.casefold()] == 1
            else f"[{root.identifier}] {relative}"
        )
        model_map[display_id] = path

    LOGGER.info(
        "[cl_vocal2promptseg] Discovered %d Whisper model(s)",
        len(model_map),
    )
    return model_map


def discover_whisper_model_names(
    folder_paths_module: Any | None = None,
) -> list[str]:
    names = list(discover_whisper_model_map(folder_paths_module))
    return names or [NO_WHISPER_MODELS_PLACEHOLDER]


def whisper_search_locations(folder_paths_module: Any | None = None) -> str:
    roots = whisper_model_roots(folder_paths_module)
    if not roots:
        return "ComfyUI/models/whisper (folder_paths is unavailable)"
    return ", ".join(str(root.path) for root in roots)


def resolve_whisper_model_name(
    model_name: str,
    folder_paths_module: Any | None = None,
) -> Path:
    if model_name == NO_WHISPER_MODELS_PLACEHOLDER:
        raise WhisperModelDiscoveryError(
            "No local Whisper models were found. Place an OpenAI Whisper .pt "
            f"checkpoint below: {whisper_search_locations(folder_paths_module)}"
        )
    model_map = discover_whisper_model_map(folder_paths_module)
    path = model_map.get(model_name)
    if path is None:
        raise WhisperModelDiscoveryError(
            f"Selected Whisper model {model_name!r} is no longer available below: "
            f"{whisper_search_locations(folder_paths_module)}"
        )
    return path
