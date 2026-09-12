"""Discovery and pairing of local Vision GGUF models and MTMD projectors."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
from typing import Any

from ..common.gguf.discovery import model_roots
from .errors import VisionModelDiscoveryError


LOGGER = logging.getLogger("cl_vision_analyzer")
NO_VISION_MODELS_PLACEHOLDER = "(no Vision GGUF pairs found)"
SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent
    / "prompts"
    / "core"
    / "observation_system_prompt.txt"
)
_QUANT_SUFFIX_RE = re.compile(
    r"(?:[-.](?:IQ|Q|F|BF)\d[^.]*)$", re.IGNORECASE
)


@dataclass(frozen=True)
class VisionModelPair:
    model_path: Path
    projector_path: Path


def _gguf_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"GGUF"
    except OSError:
        return False


def _resolved_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=True)))


def _series_name(model_path: Path) -> str:
    stem = _QUANT_SUFFIX_RE.sub("", model_path.stem)
    return stem.casefold().strip("-._ ")


def _projector_score(model_path: Path, projector_path: Path) -> tuple[int, int]:
    projector_name = projector_path.stem.casefold()
    series = _series_name(model_path)
    directory_series = model_path.parent.name.casefold()
    series_match = int(
        bool(series and series in projector_name)
        or bool(directory_series and directory_series in projector_name)
    )
    if "f16" in projector_name and "bf16" not in projector_name:
        precision = 4
    elif "bf16" in projector_name:
        precision = 3
    elif "q8_0" in projector_name or "q8-0" in projector_name:
        precision = 2
    else:
        precision = 1
    return series_match, precision


def _select_projector(
    model_path: Path, projectors: list[Path]
) -> Path | None:
    if len(projectors) == 1:
        return projectors[0]
    scored = [(projector, _projector_score(model_path, projector)) for projector in projectors]
    best_score = max(score for _, score in scored)
    best = [projector for projector, score in scored if score == best_score]
    return best[0] if len(best) == 1 else None


def discover_vision_model_map(
    folder_paths_module: Any | None = None,
) -> dict[str, VisionModelPair]:
    discovered: list[tuple[str, Path, Path, str]] = []
    seen_models: set[str] = set()

    for root in model_roots(folder_paths_module, log_name="cl_vision_analyzer"):
        if not root.path.is_dir():
            continue
        try:
            paths = [
                path
                for path in root.path.rglob("*")
                if path.suffix.casefold() == ".gguf"
            ]
        except OSError as exc:
            LOGGER.warning(
                "[cl_vision_analyzer] Could not scan Vision GGUF root %s: %s",
                root.path,
                exc,
            )
            continue
        directories: dict[Path, list[Path]] = {}
        for candidate in paths:
            try:
                resolved = candidate.resolve(strict=True)
                if not resolved.is_file():
                    continue
            except (OSError, RuntimeError):
                continue
            directories.setdefault(resolved.parent, []).append(resolved)

        for directory, files in directories.items():
            projectors = sorted(
                (
                    path
                    for path in files
                    if "mmproj" in path.name.casefold() and _gguf_magic(path)
                ),
                key=lambda path: path.name.casefold(),
            )
            if not projectors:
                continue
            for model_path in sorted(files, key=lambda path: path.name.casefold()):
                if "mmproj" in model_path.name.casefold() or not _gguf_magic(model_path):
                    continue
                try:
                    model_key = _resolved_key(model_path)
                    relative = model_path.relative_to(root.path.resolve()).as_posix()
                except (OSError, RuntimeError, ValueError):
                    continue
                if model_key in seen_models:
                    continue
                projector = _select_projector(model_path, projectors)
                if projector is None:
                    LOGGER.warning(
                        "[cl_vision_analyzer] Excluded %s because its projector "
                        "cannot be selected uniquely from: %s",
                        model_path.name,
                        ", ".join(path.name for path in projectors),
                    )
                    continue
                seen_models.add(model_key)
                discovered.append(
                    (root.identifier, model_path, projector, relative)
                )

    relative_counts: dict[str, int] = {}
    for _, _, _, relative in discovered:
        key = relative.casefold()
        relative_counts[key] = relative_counts.get(key, 0) + 1

    result: dict[str, VisionModelPair] = {}
    for root_id, model_path, projector_path, relative in sorted(
        discovered,
        key=lambda item: (item[3].casefold(), item[0].casefold()),
    ):
        display = (
            relative
            if relative_counts[relative.casefold()] == 1
            else f"[{root_id}] {relative}"
        )
        result[display] = VisionModelPair(model_path, projector_path)
    LOGGER.info(
        "[cl_vision_analyzer] Discovered %d Vision GGUF model pair(s)",
        len(result),
    )
    return result


def discover_vision_model_names(
    folder_paths_module: Any | None = None,
) -> list[str]:
    names = list(discover_vision_model_map(folder_paths_module))
    return names or [NO_VISION_MODELS_PLACEHOLDER]


def resolve_vision_model_name(
    model_name: str,
    folder_paths_module: Any | None = None,
) -> VisionModelPair:
    if model_name == NO_VISION_MODELS_PLACEHOLDER:
        raise VisionModelDiscoveryError(
            "No Vision GGUF pair was found. Put a model GGUF and its mmproj "
            "GGUF in the same directory below ComfyUI/models/LLM/GGUF."
        )
    pair = discover_vision_model_map(folder_paths_module).get(model_name)
    if pair is None:
        raise VisionModelDiscoveryError(
            f"Selected Vision GGUF model {model_name!r} is no longer available"
        )
    return pair


def file_fingerprint(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


def load_observation_system_prompt() -> str:
    try:
        prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise VisionModelDiscoveryError(
            f"Vision observation system prompt cannot be read: {SYSTEM_PROMPT_PATH}"
        ) from exc
    if not prompt.strip():
        raise VisionModelDiscoveryError(
            "Vision observation system prompt is empty"
        )
    return prompt.rstrip() + "\n"


def observation_system_prompt_fingerprint() -> tuple[str, int, int, str]:
    try:
        resolved = SYSTEM_PROMPT_PATH.resolve(strict=True)
        stat = resolved.stat()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return str(resolved), stat.st_size, stat.st_mtime_ns, digest
    except OSError as exc:
        digest = hashlib.sha256(
            f"{type(exc).__name__}|{exc}".encode("utf-8", "replace")
        ).hexdigest()
        return str(SYSTEM_PROMPT_PATH), -1, -1, digest
