"""Strict parser for the Vision analyzer's line-oriented protocol."""

from __future__ import annotations

import json
import re
from .errors import VisionObservationError
from .structures import (
    FEATURE_CATEGORIES,
    HINT_ALIGNMENTS,
    VISIBILITIES,
    CompositionObservation,
    HintAssessment,
    PrimarySubject,
    SceneObservation,
    StyleObservation,
    SubjectFeature,
    VisionObservation,
)


_REFERENCE_TAG_RE = re.compile(
    r"<(?:Picture|Subject|Video|Audio)\s+\d+>", re.IGNORECASE
)
_THINKING_RE = re.compile(r"</?(?:think|thinking)>", re.IGNORECASE)
_COMPOSITION_FIELDS = (
    "shot_size",
    "viewpoint",
    "subject_placement",
    "depth",
)
_STYLE_FIELDS = ("medium", "rendering", "palette")
_VISIBILITY_ALIASES = {"visible": "clear"}
_GLUED_VISIBILITY_RE = re.compile(
    r"^(.*?)(?:[. ]?)(clear|partial|uncertain|visible)\s*$",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_RE = re.compile(
    r"^(?:image|picture|photo|画像|写真|人物|人|キャラクター)$",
    re.IGNORECASE,
)
_GENERIC_FEATURE_RE = re.compile(
    r"^(?:顔|髪(?:の毛)?|目|瞳|眉(?:毛)?|耳|身体|体|全身|衣装|服|装飾|"
    r"アクセサリー|尾|尻尾)(?:が|を)?(?:見える|確認できる|写っている|映っている)$"
)


def _clean_lines(content: str) -> list[str]:
    if not isinstance(content, str) or not content:
        raise VisionObservationError("Planner response is empty")
    if "\x00" in content:
        raise VisionObservationError("Planner response contains NUL")
    if _THINKING_RE.search(content):
        raise VisionObservationError("Planner response contains thinking markup")
    if "```" in content:
        raise VisionObservationError("Planner response contains a code fence")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines:
        raise VisionObservationError("Planner response is empty")
    if any(not line.strip() for line in lines):
        raise VisionObservationError(
            "Observation protocol must not contain blank physical lines"
        )
    return lines


def _value(value: str, context: str, *, allow_empty: bool = True) -> str:
    text = value.strip()
    if not text and not allow_empty:
        raise VisionObservationError(f"{context} must not be empty")
    if "\t" in text:
        raise VisionObservationError(f"{context} contains TAB")
    if _REFERENCE_TAG_RE.search(text):
        raise VisionObservationError(
            f"{context} contains an unprotected reference tag"
        )
    if text.startswith("#") or "\n#" in text:
        raise VisionObservationError(f"{context} contains a Markdown heading")
    if text in {"OBSERVATION_V2", "END_OBSERVATION"}:
        raise VisionObservationError(f"{context} contains a protocol marker")
    return text


def parse_observation_response(
    content: str,
    *,
    allow_missing_end: bool = True,
) -> tuple[VisionObservation, tuple[str, ...]]:
    """Parse one complete canonical observation response."""

    lines = _clean_lines(content)
    if lines[0] != "OBSERVATION_V2":
        raise VisionObservationError(
            "Observation response must start with OBSERVATION_V2"
        )
    cursor = 1

    def take(prefix: str, *, allow_empty: bool = True) -> str:
        nonlocal cursor
        if cursor >= len(lines):
            raise VisionObservationError(f"Observation expected {prefix}")
        parts = lines[cursor].split("\t")
        if len(parts) != 2 or parts[0] != prefix:
            raise VisionObservationError(
                f"Observation expected {prefix} at line {cursor + 1}"
            )
        cursor += 1
        return _value(
            parts[1], prefix, allow_empty=allow_empty
        )

    overview = take("OVERVIEW", allow_empty=False)
    identity = take("PRIMARY_SUBJECT")
    if identity and _GENERIC_IDENTITY_RE.fullmatch(identity):
        raise VisionObservationError(
            "PRIMARY_SUBJECT must describe the visible subject, not a generic image label"
        )
    warnings: list[str] = []

    if cursor >= len(lines):
        raise VisionObservationError("Observation expected HINT_ASSESSMENT")
    hint_parts = lines[cursor].split("\t")
    if len(hint_parts) != 3 or hint_parts[0] != "HINT_ASSESSMENT":
        raise VisionObservationError(
            f"Observation expected HINT_ASSESSMENT at line {cursor + 1}"
        )
    hint_alignment = hint_parts[1]
    if hint_alignment not in HINT_ALIGNMENTS:
        raise VisionObservationError(
            f"HINT_ASSESSMENT uses unknown alignment {hint_alignment!r}"
        )
    hint_explanation = _value(hint_parts[2], "HINT_ASSESSMENT explanation")
    if hint_alignment == "not_used" and hint_explanation:
        raise VisionObservationError(
            "HINT_ASSESSMENT not_used must have an empty explanation"
        )
    if hint_alignment != "not_used" and not hint_explanation:
        raise VisionObservationError(
            f"HINT_ASSESSMENT {hint_alignment} requires an explanation"
        )
    cursor += 1

    features: list[SubjectFeature] = []
    while cursor < len(lines) and lines[cursor].startswith("SUBJECT_FEATURE\t"):
        parts = lines[cursor].split("\t")
        if len(parts) < 3:
            raise VisionObservationError(
                f"SUBJECT_FEATURE at line {cursor + 1} has too few TAB fields"
            )
        category = parts[1].strip()
        if category not in FEATURE_CATEGORIES:
            raise VisionObservationError(
                f"SUBJECT_FEATURE uses unknown category {category!r}"
            )
        description_parts = [part.strip() for part in parts[2:-1]]
        raw_visibility = parts[-1]
        visibility = raw_visibility.strip()
        repaired = False
        recognized = set(VISIBILITIES) | set(_VISIBILITY_ALIASES)
        if (
            visibility not in recognized
            and len(parts) >= 4
            and parts[2].strip().casefold() in recognized
        ):
            # Small VL models sometimes emit category, visibility,
            # description instead of category, description, visibility. The
            # known enum makes this inversion unambiguous, so preserve the
            # description and repair the columns without another inference.
            raw_visibility = parts[2]
            visibility = raw_visibility.strip().casefold()
            description_parts = [part.strip() for part in parts[3:]]
            repaired = True
        if visibility not in recognized:
            glued = _GLUED_VISIBILITY_RE.fullmatch(visibility)
            if glued and glued.group(1).strip():
                description_parts.append(glued.group(1).strip())
                visibility = glued.group(2).casefold()
                repaired = True
            else:
                raise VisionObservationError(
                    f"SUBJECT_FEATURE uses unknown visibility {visibility!r}"
                )
        if (
            len(description_parts) > 1
            and description_parts[0] in FEATURE_CATEGORIES
        ):
            description_parts.pop(0)
            repaired = True
        description = "、".join(
            part for part in description_parts if part
        )
        if not description:
            raise VisionObservationError(
                f"SUBJECT_FEATURE at line {cursor + 1} has no description"
            )
        if _GENERIC_FEATURE_RE.fullmatch(description):
            raise VisionObservationError(
                "SUBJECT_FEATURE at line "
                f"{cursor + 1} must describe color, shape, count, material, or "
                "another visible attribute instead of merely saying it is visible"
            )
        if len(parts) != 4 or raw_visibility != raw_visibility.strip():
            repaired = True
        if repaired:
            warnings.append(
                f"Repaired SUBJECT_FEATURE columns at line {cursor + 1}"
            )
        normalized_visibility = _VISIBILITY_ALIASES.get(visibility, visibility)
        if normalized_visibility != visibility:
            warnings.append(
                f"Normalized SUBJECT_FEATURE visibility {visibility!r} "
                f"to {normalized_visibility!r}"
            )
        if normalized_visibility not in VISIBILITIES:
            raise VisionObservationError(
                f"SUBJECT_FEATURE uses unknown visibility {visibility!r}"
            )
        features.append(
            SubjectFeature(
                category=category,
                description=_value(
                    description, "SUBJECT_FEATURE description", allow_empty=False
                ),
                visibility=normalized_visibility,
            )
        )
        cursor += 1

    pose = take("SUBJECT_POSE")
    setting = take("SCENE_SETTING")
    elements: list[str] = []
    while cursor < len(lines) and lines[cursor].startswith("SCENE_ELEMENT\t"):
        parts = lines[cursor].split("\t")
        if len(parts) != 2:
            raise VisionObservationError(
                f"SCENE_ELEMENT at line {cursor + 1} requires two TAB fields"
            )
        elements.append(
            _value(parts[1], "SCENE_ELEMENT", allow_empty=False)
        )
        cursor += 1
    lighting = take("LIGHTING")
    time_weather = take("TIME_WEATHER")

    composition_values: dict[str, str] = {}
    for field in _COMPOSITION_FIELDS:
        if cursor >= len(lines):
            raise VisionObservationError(
                f"Observation expected COMPOSITION {field}"
            )
        parts = lines[cursor].split("\t")
        if len(parts) != 3 or parts[:2] != ["COMPOSITION", field]:
            raise VisionObservationError(
                f"Observation expected COMPOSITION {field} at line {cursor + 1}"
            )
        composition_values[field] = _value(
            parts[2], f"COMPOSITION {field}"
        )
        cursor += 1

    style_values: dict[str, str] = {}
    for field in _STYLE_FIELDS:
        if cursor >= len(lines):
            raise VisionObservationError(f"Observation expected STYLE {field}")
        parts = lines[cursor].split("\t")
        if len(parts) != 3 or parts[:2] != ["STYLE", field]:
            raise VisionObservationError(
                f"Observation expected STYLE {field} at line {cursor + 1}"
            )
        style_values[field] = _value(parts[2], f"STYLE {field}")
        cursor += 1

    visible_text: list[str] = []
    while cursor < len(lines) and lines[cursor].startswith("VISIBLE_TEXT\t"):
        parts = lines[cursor].split("\t")
        if len(parts) != 2:
            raise VisionObservationError(
                f"VISIBLE_TEXT at line {cursor + 1} requires two TAB fields"
            )
        visible_text.append(
            _value(parts[1], "VISIBLE_TEXT", allow_empty=False)
        )
        cursor += 1

    uncertainties: list[str] = []
    while cursor < len(lines) and lines[cursor].startswith("UNCERTAINTY\t"):
        parts = lines[cursor].split("\t")
        if len(parts) != 2:
            raise VisionObservationError(
                f"UNCERTAINTY at line {cursor + 1} requires two TAB fields"
            )
        uncertainties.append(
            _value(parts[1], "UNCERTAINTY", allow_empty=False)
        )
        cursor += 1

    if cursor < len(lines) and lines[cursor] == "END_OBSERVATION":
        cursor += 1
    elif allow_missing_end and cursor == len(lines):
        warnings.append("Appended one missing END_OBSERVATION marker")
    else:
        found = lines[cursor] if cursor < len(lines) else "end of response"
        raise VisionObservationError(
            f"Observation expected END_OBSERVATION; found {found!r}"
        )
    if cursor != len(lines):
        raise VisionObservationError(
            f"Observation contains content after END_OBSERVATION at line {cursor + 1}"
        )

    observation = VisionObservation(
        overview=overview,
        primary_subject=PrimarySubject(
            identity=identity,
            features=tuple(features),
            pose=pose,
        ),
        hint_assessment=HintAssessment(
            alignment=hint_alignment,
            explanation=hint_explanation,
        ),
        scene=SceneObservation(
            setting=setting,
            elements=tuple(elements),
            lighting=lighting,
            time_weather=time_weather,
        ),
        composition=CompositionObservation(**composition_values),
        style=StyleObservation(**style_values),
        visible_text=tuple(visible_text),
        uncertainties=tuple(uncertainties),
    )
    return observation, tuple(warnings)


def validate_hint_assessment(
    observation: VisionObservation,
    *,
    subject_hint: str,
    hint_mode: str,
) -> None:
    hint_active = bool(subject_hint) and hint_mode in {"assist", "lock_identity"}
    alignment = observation.hint_assessment.alignment
    if hint_active and alignment == "not_used":
        raise VisionObservationError(
            "Vision response did not assess the active subject_hint"
        )
    if not hint_active and alignment != "not_used":
        raise VisionObservationError(
            "Vision response assessed a subject_hint that was not active"
        )


def validate_cached_observation(value: object) -> VisionObservation:
    try:
        observation = VisionObservation.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise VisionObservationError(
            f"Cached observation is invalid: {exc}"
        ) from exc
    return observation


def validate_rendered_result(profile: str, result: str) -> None:
    if not isinstance(result, str) or not result.strip():
        raise VisionObservationError("Rendered result is empty")
    if profile == "structured_json":
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as exc:
            raise VisionObservationError(
                "structured_json renderer produced invalid JSON"
            ) from exc
        validate_cached_observation(parsed)
        return
    if profile in {"subject_only", "planner_brief"}:
        try:
            from ..node_mv_prompt_planner.brief_parser import parse_planning_brief

            brief = parse_planning_brief(result)
        except Exception as exc:
            raise VisionObservationError(
                f"Rendered PlanningBrief is invalid: {exc}"
            ) from exc
        if len(brief.subjects) != 1 or len(brief.retention) != 1:
            raise VisionObservationError(
                "Rendered Subject and Retention must each contain one bullet"
            )
        return
    expected_heading = (
        "## 画像概要" if profile == "general" else "# 共通プロンプト"
    )
    if not result.startswith(expected_heading + "\n"):
        raise VisionObservationError(
            f"{profile} renderer did not start with {expected_heading}"
        )


def response_content(response: object) -> str:
    if not isinstance(response, dict):
        raise VisionObservationError(
            "llama-cpp-python returned a non-object response"
        )
    choices = response.get("choices")
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], dict)
    ):
        raise VisionObservationError(
            "llama-cpp-python response contains no choices"
        )
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise VisionObservationError(
            "llama-cpp-python response contains no text content"
        )
    return message["content"]
