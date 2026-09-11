"""Typed intermediate structures for MV planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlanningBrief:
    subjects: tuple[str, ...]
    retention: tuple[str, ...] = ()
    common: tuple[str, ...] = ()

    def to_markdown(self) -> str:
        blocks = ["# サブジェクト", *(f"* {line}" for line in self.subjects)]
        if self.retention:
            blocks.extend(
                ["", "# 保持分析", *(f"* {line}" for line in self.retention)]
            )
        if self.common:
            blocks.extend(
                ["", "# 共通プロンプト", *(f"* {line}" for line in self.common)]
            )
        return "\n".join(blocks)


@dataclass(frozen=True)
class TimelineLyric:
    text: str
    section_label: str | None = None
    section_kind: str | None = None


@dataclass(frozen=True)
class TimelineScene:
    scene_id: int
    duration_seconds: int
    is_continue: bool
    state: str
    source_start_ms: int
    source_end_ms: int
    lyrics: tuple[TimelineLyric, ...]
    lip_sync_lines: tuple[str, ...]
    soundscape_lines: tuple[str, ...]


@dataclass(frozen=True)
class TimelineDocument:
    scenes: tuple[TimelineScene, ...]


@dataclass(frozen=True)
class CameraPlan:
    type: str
    amplitude: str
    speed: str
    description: str


@dataclass(frozen=True)
class AuxiliaryVisual:
    kind: str
    description: str


@dataclass(frozen=True)
class PlannedShot:
    start_ms: int
    composition: str
    subject_actions: tuple[str, ...]
    environment: str
    camera: CameraPlan
    auxiliary_visuals: tuple[AuxiliaryVisual, ...] = ()


@dataclass(frozen=True)
class PlannedScene:
    scene_id: int
    scene_intent: str
    shots: tuple[PlannedShot, ...]


@dataclass(frozen=True)
class SectionMotif:
    section: str
    motif: str


@dataclass(frozen=True)
class SongBible:
    visual_arc: str
    camera_strategy: tuple[str, ...]
    visual_enrichment_strategy: str = ""
    section_motifs: tuple[SectionMotif, ...] = ()


@dataclass(frozen=True)
class MVPlan:
    song_bible: SongBible
    scenes: tuple[PlannedScene, ...]
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
