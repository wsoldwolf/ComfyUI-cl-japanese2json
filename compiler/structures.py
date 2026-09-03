"""Data structures shared by the parser and JSON generator."""

from dataclasses import dataclass, field


SOUND_NONE = "NONE"
VOCALIZATION_EXPLICIT_DIALOGUE_ONLY = "EXPLICIT_DIALOGUE_ONLY"

RETENTION_FULLY_PRESERVED = "fully_preserved"
RETENTION_PARTIALLY_PRESERVED = "partially_preserved"
RETENTION_ATTRIBUTE_TRANSFER = "attribute_transfer"
RETENTION_WEAK_REFERENCE = "weak_reference"
RETENTION_RELATIONSHIPS = frozenset(
    {
        RETENTION_FULLY_PRESERVED,
        RETENTION_PARTIALLY_PRESERVED,
        RETENTION_ATTRIBUTE_TRANSFER,
        RETENTION_WEAK_REFERENCE,
    }
)


@dataclass
class Soundscape:
    environment: str | None = None
    sound_effects: str | None = None
    vocalization: str | None = None


@dataclass
class Shot:
    start_ms: int = 0
    lines: list[str] = field(default_factory=list)


@dataclass
class RetentionRule:
    subject_number: int
    relationship: str
    description: str
    target_subject_number: int | None = None


@dataclass
class Scene:
    duration: int = 5
    is_continue: bool = False
    preamble: list[str] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    soundscape: Soundscape = field(default_factory=Soundscape)


@dataclass
class Emd:
    subjects: list[str] = field(default_factory=list)
    retention_rules: list[RetentionRule] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
