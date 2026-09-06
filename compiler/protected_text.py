"""Deterministic protection for references and direct speech."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from .errors import ProtectedTextError, TranslationError


LOGGER = logging.getLogger("cl_japanese2json")

REFERENCE_RE = re.compile(r"<(Picture|Video|Audio|Subject) ([0-9]+)>")
COMPACT_REFERENCE_RE = re.compile(r"<(Picture|Video|Audio|Subject)([0-9]+)>")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")

REFERENCE_LIMITS = {
    "Picture": (1, 9),
    "Video": (1, 9),
    "Audio": (1, 3),
    "Subject": (1, 4),
}


@dataclass(frozen=True)
class ProtectedPayload:
    """Text sent to the translator and its exact replacements."""

    text: str
    replacements: dict[str, str]

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(self.replacements)


class _TokenStore:
    def __init__(self, original: str, namespace: str | None = None) -> None:
        if namespace is None:
            safe_namespace = "0"
        else:
            safe_namespace = re.sub(r"[^A-Za-z0-9]", "_", namespace)
            match = re.fullmatch(r"R0*([0-9]+)", safe_namespace)
            if match:
                safe_namespace = str(int(match.group(1)))
        token_stem = f"CLJ{safe_namespace}C"
        prefix_number = 0
        while f"{token_stem}{prefix_number}P" in original:
            prefix_number += 1
        self._prefix = f"{token_stem}{prefix_number}P"
        self.replacements: dict[str, str] = {}

    def add(self, value: str) -> str:
        token = f"{self._prefix}{len(self.replacements)}X"
        self.replacements[token] = value
        return token


def _protect_existing_direct_speech(text: str, store: _TokenStore) -> str:
    parts: list[str] = []
    cursor = 0
    while cursor < len(text):
        opening = text.find("<d>", cursor)
        closing = text.find("</d>", cursor)
        if closing != -1 and (opening == -1 or closing < opening):
            raise ProtectedTextError("Unmatched </d> tag in a bullet record")
        if opening == -1:
            parts.append(text[cursor:])
            break

        parts.append(text[cursor:opening])
        end = text.find("</d>", opening + 3)
        if end == -1:
            raise ProtectedTextError("Unclosed <d> tag in a bullet record")
        nested = text.find("<d>", opening + 3, end)
        if nested != -1:
            raise ProtectedTextError("Nested <d> tags are not allowed")

        region = text[opening : end + 4]
        parts.append(store.add(region))
        cursor = end + 4

    return "".join(parts)


def _protect_references(text: str, store: _TokenStore) -> str:
    def replace_compact(match: re.Match[str]) -> str:
        LOGGER.warning(
            "[cl_japanese2json] Malformed reference tag %s is missing the required ASCII space; use <%s %s>",
            match.group(0),
            match.group(1),
            match.group(2),
        )
        return store.add(match.group(0))

    def replace(match: re.Match[str]) -> str:
        tag_type = match.group(1)
        number_text = match.group(2)
        number = int(number_text)
        lower, upper = REFERENCE_LIMITS[tag_type]
        if number_text != str(number) or not lower <= number <= upper:
            LOGGER.warning(
                "[cl_japanese2json] Out-of-range or non-canonical reference tag preserved: %s",
                match.group(0),
            )
        return store.add(match.group(0))

    text = COMPACT_REFERENCE_RE.sub(replace_compact, text)
    return REFERENCE_RE.sub(replace, text)


def escape_dialogue_text(text: str) -> str:
    """Escape only currently unescaped MiniMax direct-speech metacharacters."""

    return re.sub(r"(?<!\\)([<>\[\]])", r"\\\1", text)


def _protect_japanese_dialogue(text: str, store: _TokenStore) -> str:
    parts: list[str] = []
    plain_start = 0
    opening: int | None = None

    for index, char in enumerate(text):
        if char == "「":
            if opening is not None:
                raise ProtectedTextError("Nested Japanese corner brackets are not allowed")
            parts.append(text[plain_start:index])
            opening = index
        elif char == "」":
            if opening is None:
                raise ProtectedTextError("Unmatched Japanese closing corner bracket")
            dialogue = escape_dialogue_text(text[opening + 1 : index])
            region = f"<d>[Japanese]{dialogue}</d>"
            parts.append(store.add(region))
            opening = None
            plain_start = index + 1

    if opening is not None:
        raise ProtectedTextError("Unclosed Japanese corner bracket")
    parts.append(text[plain_start:])
    return "".join(parts)


def protect_text(text: str, *, namespace: str | None = None) -> ProtectedPayload:
    """Protect one bullet body in the order mandated by the specification."""

    store = _TokenStore(text, namespace=namespace)
    protected = _protect_existing_direct_speech(text, store)
    protected = _protect_references(protected, store)
    protected = _protect_japanese_dialogue(protected, store)
    return ProtectedPayload(protected, dict(store.replacements))


def validate_protected_translation(payload: ProtectedPayload, translated: str) -> None:
    """Require every placeholder byte-for-byte and exactly once."""

    for token in payload.tokens:
        count = translated.count(token)
        if count != 1:
            raise TranslationError(
                f"Protected placeholder {token!r} occurred {count} time(s); expected exactly once"
            )

    if payload.tokens:
        token_prefix = payload.tokens[0].rsplit("P", 1)[0] + "P"
        family_re = re.compile(re.escape(token_prefix) + r"[0-9]+X")
        expected = set(payload.tokens)
        unexpected = {
            token for token in family_re.findall(translated) if token not in expected
        }
        if unexpected:
            raise TranslationError("Translation contains an unexpected protected placeholder")


def restore_text(payload: ProtectedPayload, translated: str) -> str:
    validate_protected_translation(payload, translated)
    restored = translated
    for token, value in payload.replacements.items():
        restored = restored.replace(token, value)
    if payload.tokens:
        token_prefix = payload.tokens[0].rsplit("P", 1)[0] + "P"
        if re.search(re.escape(token_prefix) + r"[0-9]+X", restored):
            raise TranslationError("An unresolved protected placeholder remains after restoration")
    return restored


def remove_direct_speech(text: str) -> str:
    """Remove valid <d> regions for Japanese and Subject scans."""

    store = _TokenStore(text)
    protected = _protect_existing_direct_speech(text, store)
    return protected


def contains_unprotected_japanese(text: str) -> bool:
    return JAPANESE_RE.search(remove_direct_speech(text)) is not None
