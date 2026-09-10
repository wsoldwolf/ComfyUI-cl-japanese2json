"""Suno-style section heading classification shared by audio and MV nodes."""

from __future__ import annotations

import re
import unicodedata


SECTION_HEADING_RE = re.compile(r"^\[[^\]\r\n]+\]$")
_SECTION_KIND_RULES = (
    ("final_chorus", re.compile(r"^final\s+chorus(?:\s+\d+)?$")),
    ("pre_chorus", re.compile(r"^pre[\s-]*chorus(?:\s+\d+)?$")),
    ("post_chorus", re.compile(r"^post[\s-]*chorus(?:\s+\d+)?$")),
    ("verse", re.compile(r"^verse(?:\s+\d+)?$")),
    ("chorus", re.compile(r"^chorus(?:\s+\d+)?$")),
    ("intro", re.compile(r"^intro(?:\s+\d+)?$")),
    ("hook", re.compile(r"^hook(?:\s+\d+)?$")),
    ("refrain", re.compile(r"^refrain(?:\s+\d+)?$")),
    ("bridge", re.compile(r"^bridge(?:\s+\d+)?$")),
    ("breakdown", re.compile(r"^breakdown(?:\s+\d+)?$")),
    ("interlude", re.compile(r"^interlude(?:\s+\d+)?$")),
    ("instrumental", re.compile(r"^instrumental(?:\s+\d+)?$")),
    ("solo", re.compile(r"^(?:guitar\s+)?solo(?:\s+\d+)?$")),
    ("outro", re.compile(r"^outro(?:\s+\d+)?$")),
)


def classify_suno_section(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).strip()
    if not SECTION_HEADING_RE.fullmatch(normalized):
        raise ValueError(f"Invalid Suno section heading: {label!r}")
    content = normalized[1:-1].strip().casefold().replace("_", " ")
    content = re.sub(r"\s+", " ", content)
    for kind, pattern in _SECTION_KIND_RULES:
        if pattern.fullmatch(content):
            return kind
    return "custom"
