"""Data structures shared by the parser and JSON generator."""

from dataclasses import dataclass, field


SOUND_NONE = "NONE"
VOCALIZATION_EXPLICIT_DIALOGUE_ONLY = "EXPLICIT_DIALOGUE_ONLY"
VOCALIZATION_REFERENCE_AUDIO_ONLY = "REFERENCE_AUDIO_ONLY"
AUDIO_FULLY_COPY = "fully_copy"
AUDIO_PARTIALLY_COPY = "partially_copy"
AUDIO_COPY_RELATIONSHIPS = frozenset({AUDIO_FULLY_COPY, AUDIO_PARTIALLY_COPY})

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
class BackgroundMusicReuse:
    audio_number: int
    relationship: str
    source_start_ms: int | None = None
    source_end_ms: int | None = None


@dataclass
class Soundscape:
    environment: str | None = None
    sound_effects: str | None = None
    vocalization: str | None = None
    background_music: str | None = None
    background_music_reuse: BackgroundMusicReuse | None = None


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
    common_prompt: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
