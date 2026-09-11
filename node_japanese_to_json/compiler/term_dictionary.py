"""Reloadable deterministic term dictionary for Japanese prompt translation."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
import re
import threading
from typing import Iterable

from .errors import TranslationError


LOGGER = logging.getLogger("cl_japanese2json")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
DICTIONARY_FILENAME = "prompt_terms.csv"
BUNDLED_DICTIONARY_PATH = (
    Path(__file__).resolve().parents[1] / "dictionaries" / DICTIONARY_FILENAME
)
ADJACENT_USER_DICTIONARY_PATH = (
    Path(__file__).resolve().parents[1]
    / "dictionaries"
    / "prompt_terms.user.csv"
)

PromptTermEntries = tuple[tuple[str, str], ...]

_CACHE_LOCK = threading.Lock()
_CACHE_SIGNATURE: tuple[tuple[str, bool, int, int], ...] | None = None
_CACHE_ENTRIES: PromptTermEntries | None = None


def _comfy_user_dictionary_path() -> Path | None:
    """Return the optional ComfyUI user override without requiring ComfyUI."""

    try:
        import folder_paths  # type: ignore[import-not-found]
    except ImportError:
        return None

    getter = getattr(folder_paths, "get_user_directory", None)
    if not callable(getter):
        return None
    try:
        user_directory = getter()
    except Exception as exc:  # pragma: no cover - depends on the ComfyUI host
        raise TranslationError(
            f"Could not resolve the ComfyUI user directory for the prompt term dictionary: {exc}"
        ) from exc
    if not user_directory:
        return None
    return Path(user_directory) / "cl_japanese2json" / DICTIONARY_FILENAME


def prompt_term_dictionary_paths() -> tuple[Path, ...]:
    """Return dictionary layers in increasing override priority."""

    candidates: list[Path | None] = [
        BUNDLED_DICTIONARY_PATH,
        ADJACENT_USER_DICTIONARY_PATH,
        _comfy_user_dictionary_path(),
    ]
    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        identity = str(candidate.resolve(strict=False)).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        paths.append(candidate)
    return tuple(paths)


def _dictionary_signature(paths: Iterable[Path]) -> tuple[tuple[str, bool, int, int], ...]:
    signature: list[tuple[str, bool, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            signature.append((str(path), False, 0, 0))
        except OSError as exc:
            raise TranslationError(
                f"Could not inspect prompt term dictionary {path}: {exc}"
            ) from exc
        else:
            signature.append((str(path), True, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _read_dictionary(path: Path) -> PromptTermEntries:
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise TranslationError(f"Could not read prompt term dictionary {path}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        if fieldnames != ("source", "target"):
            raise TranslationError(
                f"Prompt term dictionary {path} must have exactly the CSV header source,target"
            )

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if None in row and row[None]:
                raise TranslationError(
                    f"Prompt term dictionary {path} row {row_number} must contain exactly two CSV fields"
                )
            source = (row.get("source") or "").strip()
            target = (row.get("target") or "").strip()
            if not source and not target:
                continue
            if source.startswith("#"):
                continue
            if not source or not target:
                raise TranslationError(
                    f"Prompt term dictionary {path} row {row_number} requires non-empty source and target values"
                )
            if "\n" in source or "\r" in source or "\n" in target or "\r" in target:
                raise TranslationError(
                    f"Prompt term dictionary {path} row {row_number} cannot contain a line break"
                )
            if JAPANESE_RE.search(source) is None:
                raise TranslationError(
                    f"Prompt term dictionary {path} row {row_number} source must contain Japanese text"
                )
            if JAPANESE_RE.search(target) is not None:
                raise TranslationError(
                    f"Prompt term dictionary {path} row {row_number} target must not contain Japanese text"
                )
            if source in seen:
                raise TranslationError(
                    f"Prompt term dictionary {path} contains duplicate source {source!r} at row {row_number}"
                )
            seen.add(source)
            entries.append((source, target))
    return tuple(entries)


def load_prompt_term_entries(paths: Iterable[Path]) -> PromptTermEntries:
    """Load and merge dictionary layers; later files override earlier files."""

    merged: dict[str, str] = {}
    loaded_paths: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        for source, target in _read_dictionary(path):
            merged[source] = target
        loaded_paths.append(path)

    if not loaded_paths:
        raise TranslationError(
            f"Bundled prompt term dictionary is missing: {BUNDLED_DICTIONARY_PATH}"
        )

    # Match compound terms before their shorter prefixes (for example,
    # 画面全体 before 画面).  Lexical ordering makes the result deterministic.
    entries = tuple(
        sorted(merged.items(), key=lambda item: (-len(item[0]), item[0]))
    )
    return entries


def _default_prompt_term_entries() -> PromptTermEntries:
    global _CACHE_ENTRIES, _CACHE_SIGNATURE

    paths = prompt_term_dictionary_paths()
    if not BUNDLED_DICTIONARY_PATH.is_file():
        raise TranslationError(
            f"Bundled prompt term dictionary is missing: {BUNDLED_DICTIONARY_PATH}"
        )
    signature = _dictionary_signature(paths)
    with _CACHE_LOCK:
        if _CACHE_ENTRIES is not None and signature == _CACHE_SIGNATURE:
            return _CACHE_ENTRIES
        entries = load_prompt_term_entries(paths)
        _CACHE_ENTRIES = entries
        _CACHE_SIGNATURE = signature
        loaded = [str(path) for path in paths if path.is_file()]
        LOGGER.info(
            "[cl_japanese2json] Loaded prompt term dictionary: %d mapping(s) from %s",
            len(entries),
            ", ".join(loaded),
        )
        return entries


def normalize_prompt_terms(
    text: str,
    *,
    entries: PromptTermEntries | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Apply deterministic terms with safe spacing around adjacent ASCII words.

    Callers must invoke this only after direct speech has been replaced by opaque
    placeholders.  The default dictionary is automatically reloaded after an
    on-disk layer changes.
    """

    normalized = text
    replaced: list[str] = []
    active_entries = entries if entries is not None else _default_prompt_term_entries()
    for source, target in active_entries:
        while source in normalized:
            start = normalized.index(source)
            end = start + len(source)
            replacement = target
            if (
                start > 0
                and normalized[start - 1].isascii()
                and normalized[start - 1].isalnum()
            ):
                replacement = " " + replacement
            if (
                end < len(normalized)
                and normalized[end].isascii()
                and normalized[end].isalnum()
            ):
                replacement += " "
            normalized = normalized[:start] + replacement + normalized[end:]
            replaced.append(source)
    return normalized, tuple(dict.fromkeys(replaced))
