"""Data structures shared by the parser and JSON generator."""

from dataclasses import dataclass, field


@dataclass
class Scene:
    duration: int = 5
    is_continue: bool = False
    shots: list[str] = field(default_factory=list)


@dataclass
class Emd:
    common_prompt: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
