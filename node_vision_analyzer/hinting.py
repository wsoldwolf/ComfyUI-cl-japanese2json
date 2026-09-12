"""Validation and policy helpers for user-supplied Subject hints."""

from __future__ import annotations

import re

from .errors import VisionAnalyzerError


HINT_MODES = ("observe_only", "assist", "lock_identity")
HINT_CONFLICT_POLICIES = ("warn", "strict")
MAX_SUBJECT_HINT_CHARACTERS = 2000
LEGACY_UPLOAD_SENTINELS = frozenset({"image"})
_HINT_LABEL_RE = re.compile(r"^subject_hint\s*[:：]\s*", re.IGNORECASE)

_REFERENCE_TAG_RE = re.compile(
    r"<(?:Picture|Subject|Video|Audio)\s+\d+>", re.IGNORECASE
)
_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s|[-*+]\s|//\s*シーン\b)", re.MULTILINE
)


def normalize_subject_hint(value: str) -> str:
    """Return one safe natural-language line for prompts and renderers."""

    if not isinstance(value, str):
        raise VisionAnalyzerError("subject_hint must be text")
    if "\x00" in value or "\t" in value:
        raise VisionAnalyzerError("subject_hint must not contain NUL or TAB")
    if len(value) > MAX_SUBJECT_HINT_CHARACTERS:
        raise VisionAnalyzerError(
            "subject_hint must be at most "
            f"{MAX_SUBJECT_HINT_CHARACTERS} characters"
        )
    if _REFERENCE_TAG_RE.search(value):
        raise VisionAnalyzerError(
            "subject_hint must not contain Picture, Subject, Video, or Audio tags"
        )
    if _STRUCTURAL_LINE_RE.search(value):
        raise VisionAnalyzerError(
            "subject_hint must be natural-language data, not Markdown directives"
        )
    return " ".join(value.replace("\r", "\n").split()).strip()


def normalize_subject_hint_compat(value: str) -> tuple[str, str | None]:
    """Normalize a hint while repairing the legacy upload-widget shift.

    Early workflow files stored the hidden IMAGEUPLOAD widget value immediately
    after ``save_debug_output``. When ``subject_hint`` was later inserted at
    that position, LiteGraph could deserialize the upload sentinel ``image`` as
    the hint. It is not a meaningful identity, so discard it deterministically
    and report the migration to the caller.
    """

    normalized = normalize_subject_hint(value)
    label_removed = bool(_HINT_LABEL_RE.match(normalized))
    if label_removed:
        normalized = _HINT_LABEL_RE.sub("", normalized, count=1).strip()
    if normalized.casefold() in LEGACY_UPLOAD_SENTINELS:
        return "", "Ignored legacy IMAGEUPLOAD value 'image' in subject_hint"
    if label_removed:
        return normalized, "Removed pasted 'subject_hint:' label from subject_hint"
    return normalized, None


def effective_subject_hint(subject_hint: str, hint_mode: str) -> str:
    if hint_mode not in HINT_MODES:
        raise VisionAnalyzerError(
            "hint_mode must be observe_only, assist, or lock_identity"
        )
    normalized = normalize_subject_hint(subject_hint)
    return normalized if hint_mode != "observe_only" else ""
