"""Validated memory and persistent caches for canonical Vision observations."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable

from .discovery import VisionModelPair, file_fingerprint
from .errors import VisionCacheError, VisionObservationError
from .runtime import llama_cpp_version
from .structures import OBSERVATION_PROTOCOL, VisionObservation
from .validation import validate_cached_observation


CACHE_SCHEMA = "cl-vision-analyzer-cache-v2"
PREPROCESSING_VERSION = "vision-preprocess-v1"
MEMORY_CACHE_LIMIT = 128


def _default_cache_root() -> Path:
    try:
        import folder_paths  # type: ignore

        root = Path(folder_paths.get_user_directory())
    except Exception as exc:
        raise VisionCacheError(
            "ComfyUI user directory is unavailable for the Vision cache"
        ) from exc
    return root / "cl_vision_analyzer_cache" / "v2"


def make_observation_cache_key(
    *,
    image_hash: str,
    image_mode: str,
    width: int,
    height: int,
    pair: VisionModelPair,
    analysis_max_edge: int,
    additional_instruction: str,
    subject_hint: str,
    hint_mode: str,
    inference_settings: dict[str, Any],
    system_prompt_hash: str,
) -> str:
    components: list[str] = [
        image_hash,
        image_mode,
        str(width),
        str(height),
        *(str(value) for value in file_fingerprint(pair.model_path)),
        *(str(value) for value in file_fingerprint(pair.projector_path)),
        str(analysis_max_edge),
        additional_instruction,
        subject_hint,
        hint_mode,
        json.dumps(
            inference_settings,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        system_prompt_hash,
        OBSERVATION_PROTOCOL,
        PREPROCESSING_VERSION,
        llama_cpp_version(),
    ]
    digest = hashlib.sha256()
    for component in components:
        digest.update(component.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


class ObservationCache:
    _memory: "OrderedDict[str, tuple[VisionObservation, tuple[str, ...]]]" = (
        OrderedDict()
    )
    _memory_lock = threading.RLock()

    def __init__(
        self,
        root_provider: Callable[[], Path] | None = None,
    ) -> None:
        self.root_provider = root_provider or _default_cache_root

    @classmethod
    def clear_memory(cls) -> None:
        with cls._memory_lock:
            cls._memory.clear()

    @classmethod
    def _memory_get(
        cls, key: str
    ) -> tuple[VisionObservation, tuple[str, ...]] | None:
        with cls._memory_lock:
            entry = cls._memory.get(key)
            if entry is not None:
                cls._memory.move_to_end(key)
            return entry

    @classmethod
    def _memory_put(
        cls,
        key: str,
        observation: VisionObservation,
        warnings: tuple[str, ...],
    ) -> None:
        with cls._memory_lock:
            cls._memory[key] = (observation, warnings)
            cls._memory.move_to_end(key)
            while len(cls._memory) > MEMORY_CACHE_LIMIT:
                cls._memory.popitem(last=False)

    def _path(self, key: str) -> Path:
        if (
            not isinstance(key, str)
            or len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
        ):
            raise VisionCacheError("Vision cache key is invalid")
        return self.root_provider() / key[:2] / f"{key}.json"

    def get(
        self, key: str
    ) -> tuple[VisionObservation, tuple[str, ...], str] | None:
        memory = self._memory_get(key)
        if memory is not None:
            return memory[0], memory[1], "memory"

        path = self._path(key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("entry must be an object")
            if value.get("schema") != CACHE_SCHEMA:
                raise ValueError("cache schema is incompatible")
            if value.get("key") != key:
                raise ValueError("cache key does not match its file name")
            observation = validate_cached_observation(value.get("observation"))
            raw_warnings = value.get("warnings", [])
            if not isinstance(raw_warnings, list) or not all(
                isinstance(item, str) for item in raw_warnings
            ):
                raise ValueError("cache warnings must be an array of strings")
            warnings = tuple(raw_warnings)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            VisionObservationError,
        ) as exc:
            raise VisionCacheError(
                f"Vision cache entry is corrupt and will be ignored: {path.name}: {exc}"
            ) from exc
        self._memory_put(key, observation, warnings)
        return observation, warnings, "disk"

    def put(
        self,
        key: str,
        observation: VisionObservation,
        warnings: tuple[str, ...],
        *,
        image_hash: str,
        width: int,
        height: int,
        pair: VisionModelPair,
    ) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "schema": CACHE_SCHEMA,
                "key": key,
                "created_at_unix": time.time(),
                "image": {
                    "sha256": image_hash,
                    "width": width,
                    "height": height,
                },
                "model": {
                    "name": pair.model_path.name,
                    "size": pair.model_path.stat().st_size,
                    "mtime_ns": pair.model_path.stat().st_mtime_ns,
                },
                "projector": {
                    "name": pair.projector_path.name,
                    "size": pair.projector_path.stat().st_size,
                    "mtime_ns": pair.projector_path.stat().st_mtime_ns,
                },
                "observation": observation.to_dict(),
                "warnings": list(warnings),
            }
            payload = json.dumps(
                entry,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n"
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{key}.",
                suffix=".tmp",
                dir=path.parent,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, path)
            except BaseException:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
                raise
        except (OSError, TypeError, ValueError) as exc:
            raise VisionCacheError(
                f"Could not write Vision cache entry {path.name}: {exc}"
            ) from exc
        self._memory_put(key, observation, warnings)
