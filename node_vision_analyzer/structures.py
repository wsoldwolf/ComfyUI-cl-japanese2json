"""Typed canonical structures for local image observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


OBSERVATION_SCHEMA = "cl-vision-observation-v2"
OBSERVATION_PROTOCOL = "cl-vision-observation-line-v2"

FEATURE_CATEGORIES = (
    "face",
    "hair",
    "eyes",
    "eyebrows",
    "ears",
    "body",
    "clothing",
    "accessory",
    "tail",
    "distinctive_feature",
)
VISIBILITIES = ("clear", "partial", "uncertain")
HINT_ALIGNMENTS = ("not_used", "consistent", "ambiguous", "conflict")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return tuple(_string(item, f"{path}[]") for item in value)


@dataclass(frozen=True)
class SubjectFeature:
    category: str
    description: str
    visibility: str

    @classmethod
    def from_dict(cls, value: Any) -> "SubjectFeature":
        data = _mapping(value, "primary_subject.features[]")
        category = _string(data.get("category"), "feature.category")
        visibility = _string(data.get("visibility"), "feature.visibility")
        if category not in FEATURE_CATEGORIES:
            raise ValueError(f"Unsupported feature category: {category!r}")
        if visibility not in VISIBILITIES:
            raise ValueError(f"Unsupported feature visibility: {visibility!r}")
        description = _string(data.get("description"), "feature.description")
        if not description.strip():
            raise ValueError("feature.description must not be empty")
        return cls(category, description, visibility)


@dataclass(frozen=True)
class PrimarySubject:
    identity: str
    features: tuple[SubjectFeature, ...]
    pose: str

    @classmethod
    def from_dict(cls, value: Any) -> "PrimarySubject":
        data = _mapping(value, "primary_subject")
        raw_features = data.get("features")
        if not isinstance(raw_features, list):
            raise ValueError("primary_subject.features must be an array")
        return cls(
            identity=_string(data.get("identity"), "primary_subject.identity"),
            features=tuple(SubjectFeature.from_dict(item) for item in raw_features),
            pose=_string(data.get("pose"), "primary_subject.pose"),
        )


@dataclass(frozen=True)
class HintAssessment:
    alignment: str
    explanation: str

    @classmethod
    def from_dict(cls, value: Any) -> "HintAssessment":
        data = _mapping(value, "hint_assessment")
        alignment = _string(data.get("alignment"), "hint_assessment.alignment")
        if alignment not in HINT_ALIGNMENTS:
            raise ValueError(f"Unsupported hint alignment: {alignment!r}")
        return cls(
            alignment=alignment,
            explanation=_string(
                data.get("explanation"), "hint_assessment.explanation"
            ),
        )


@dataclass(frozen=True)
class SceneObservation:
    setting: str
    elements: tuple[str, ...]
    lighting: str
    time_weather: str

    @classmethod
    def from_dict(cls, value: Any) -> "SceneObservation":
        data = _mapping(value, "scene")
        return cls(
            setting=_string(data.get("setting"), "scene.setting"),
            elements=_string_list(data.get("elements"), "scene.elements"),
            lighting=_string(data.get("lighting"), "scene.lighting"),
            time_weather=_string(data.get("time_weather"), "scene.time_weather"),
        )


@dataclass(frozen=True)
class CompositionObservation:
    shot_size: str
    viewpoint: str
    subject_placement: str
    depth: str

    @classmethod
    def from_dict(cls, value: Any) -> "CompositionObservation":
        data = _mapping(value, "composition")
        return cls(
            shot_size=_string(data.get("shot_size"), "composition.shot_size"),
            viewpoint=_string(data.get("viewpoint"), "composition.viewpoint"),
            subject_placement=_string(
                data.get("subject_placement"), "composition.subject_placement"
            ),
            depth=_string(data.get("depth"), "composition.depth"),
        )


@dataclass(frozen=True)
class StyleObservation:
    medium: str
    rendering: str
    palette: str

    @classmethod
    def from_dict(cls, value: Any) -> "StyleObservation":
        data = _mapping(value, "style")
        return cls(
            medium=_string(data.get("medium"), "style.medium"),
            rendering=_string(data.get("rendering"), "style.rendering"),
            palette=_string(data.get("palette"), "style.palette"),
        )


@dataclass(frozen=True)
class VisionObservation:
    overview: str
    primary_subject: PrimarySubject
    hint_assessment: HintAssessment
    scene: SceneObservation
    composition: CompositionObservation
    style: StyleObservation
    visible_text: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema"] = OBSERVATION_SCHEMA
        return {
            "schema": value["schema"],
            "overview": value["overview"],
            "primary_subject": value["primary_subject"],
            "hint_assessment": value["hint_assessment"],
            "scene": value["scene"],
            "composition": value["composition"],
            "style": value["style"],
            "visible_text": list(value["visible_text"]),
            "uncertainties": list(value["uncertainties"]),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "VisionObservation":
        data = _mapping(value, "observation")
        if data.get("schema") != OBSERVATION_SCHEMA:
            raise ValueError(
                f"observation.schema must be {OBSERVATION_SCHEMA!r}"
            )
        return cls(
            overview=_string(data.get("overview"), "overview"),
            primary_subject=PrimarySubject.from_dict(data.get("primary_subject")),
            hint_assessment=HintAssessment.from_dict(data.get("hint_assessment")),
            scene=SceneObservation.from_dict(data.get("scene")),
            composition=CompositionObservation.from_dict(data.get("composition")),
            style=StyleObservation.from_dict(data.get("style")),
            visible_text=_string_list(data.get("visible_text"), "visible_text"),
            uncertainties=_string_list(data.get("uncertainties"), "uncertainties"),
        )
