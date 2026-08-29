"""GGUF model discovery below ComfyUI model roots."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any

from .compiler.errors import ModelDiscoveryError


LOGGER = logging.getLogger("cl_japanese2json")
NO_MODELS_PLACEHOLDER = "(no GGUF models found)"


@dataclass(frozen=True)
class ModelRoot:
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


def model_roots(folder_paths_module: Any | None = None) -> list[ModelRoot]:
    folder_paths = _folder_paths_module(folder_paths_module)
    if folder_paths is None:
        return []

    candidates: list[ModelRoot] = []
    models_dir = getattr(folder_paths, "models_dir", None)
    if models_dir:
        candidates.append(ModelRoot("models", Path(models_dir) / "LLM" / "GGUF"))

    registered = getattr(folder_paths, "folder_names_and_paths", {})
    if isinstance(registered, dict) and "LLM" in registered:
        try:
            llm_paths = folder_paths.get_folder_paths("LLM")
        except Exception as exc:
            LOGGER.warning(
                "[cl_japanese2json] Could not read additional ComfyUI LLM model paths: %s",
                exc,
            )
            llm_paths = []
        for index, raw_root in enumerate(llm_paths, start=1):
            root = Path(raw_root)
            candidates.append(ModelRoot(f"LLM{index}-GGUF", root / "GGUF"))
            candidates.append(ModelRoot(f"LLM{index}", root))

    unique: list[ModelRoot] = []
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


def _safe_files(root: ModelRoot) -> list[tuple[ModelRoot, Path, str]]:
    if not root.path.is_dir():
        return []
    results: list[tuple[ModelRoot, Path, str]] = []
    try:
        paths = root.path.rglob("*")
        for path in paths:
            try:
                if not path.is_file():
                    continue
                if path.suffix.lower() != ".gguf" or "mmproj" in path.name.lower():
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
            "[cl_japanese2json] Could not scan GGUF root %s: %s", root.path, exc
        )
    return results


def discover_model_map(folder_paths_module: Any | None = None) -> dict[str, Path]:
    discovered: list[tuple[ModelRoot, Path, str]] = []
    seen_paths: set[str] = set()
    for root in model_roots(folder_paths_module):
        for entry in _safe_files(root):
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
        discovered, key=lambda item: (item[2].casefold(), item[0].identifier.casefold())
    ):
        if relative_counts[relative.casefold()] == 1:
            display_id = relative
        else:
            display_id = f"[{root.identifier}] {relative}"
        model_map[display_id] = path

    LOGGER.info("[cl_japanese2json] Discovered %d GGUF model(s)", len(model_map))
    return model_map


def discover_model_names(folder_paths_module: Any | None = None) -> list[str]:
    names = list(discover_model_map(folder_paths_module))
    return names or [NO_MODELS_PLACEHOLDER]


def search_locations(folder_paths_module: Any | None = None) -> str:
    roots = model_roots(folder_paths_module)
    if not roots:
        return "ComfyUI/models/LLM/GGUF (folder_paths is unavailable)"
    return ", ".join(str(root.path) for root in roots)


def resolve_model_name(model_name: str, folder_paths_module: Any | None = None) -> Path:
    if model_name == NO_MODELS_PLACEHOLDER:
        raise ModelDiscoveryError(
            f"No GGUF models were found. Place a text GGUF below: {search_locations(folder_paths_module)}"
        )
    model_map = discover_model_map(folder_paths_module)
    path = model_map.get(model_name)
    if path is None:
        raise ModelDiscoveryError(
            f"Selected GGUF model {model_name!r} is no longer available below: "
            f"{search_locations(folder_paths_module)}"
        )
    return path
