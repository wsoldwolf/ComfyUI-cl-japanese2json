"""Data structures shared by the parser and JSON generator."""

from dataclasses import dataclass, field


SOUND_NONE = "NONE"
VOCALIZATION_EXPLICIT_DIALOGUE_ONLY = "EXPLICIT_DIALOGUE_ONLY"


@dataclass
class Soundscape:
    environment: str | None = None
    sound_effects: str | None = None
    vocalization: str | None = None


@dataclass
class Scene:
    duration: int = 5
    is_continue: bool = False
    shots: list[str] = field(default_factory=list)
    soundscape: Soundscape = field(default_factory=Soundscape)


@dataclass
class Emd:
    common_prompt: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
