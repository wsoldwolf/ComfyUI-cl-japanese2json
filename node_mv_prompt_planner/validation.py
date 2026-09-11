"""Strict parsing and validation of planner line-protocol responses."""

from __future__ import annotations

import re
import unicodedata

from .errors import PlannerResponseError
from .placeholders import ReferenceProtector
from .structures import (
    AuxiliaryVisual,
    CameraPlan,
    PlannedScene,
    PlannedShot,
    SectionMotif,
    SongBible,
    TimelineScene,
)
from .visual_profiles import (
    DEFAULT_VISUAL_PROFILE_ID,
    VisualEnrichmentProfile,
    load_visual_profile,
)


CAMERA_TYPES = frozenset(
    {
        "zoom",
        "push",
        "pull",
        "pan",
        "truck",
        "tilt",
        "pedestal",
        "arc",
        "tracking",
        "static",
        "shake",
        "pov",
        "roll",
    }
)
CAMERA_AMPLITUDES = frozenset({"none", "small", "medium", "large"})
CAMERA_SPEEDS = frozenset({"none", "slow", "moderate", "fast"})
_FORBIDDEN_TEXT_MARKERS = (
    "\n",
    "\r",
    "\t",
    "# ",
    "## ",
    "//",
    "<d>",
    "</d>",
    "「",
    "」",
    '"',
)
_CREATIVE_VOCAL_CUES = (
    "歌う",
    "歌い",
    "歌唱",
    "口ずさむ",
    "口パク",
    "発声",
    "台詞",
    "セリフ",
    "叫ぶ",
    "囁く",
    "うめく",
    "語る",
    "語り",
    "声を出",
)
_LOCKED_SOURCE_VOCAL_VISUAL_CUES = frozenset(
    {
        "歌う",
        "歌い",
        "歌唱",
        "口パク",
    }
)

_CAMERA_ACTION_RE = re.compile(
    r"(?:カメラ|視点|レンズ).{0,40}"
    r"(?:移動|接近|後退|遠ざか|回り込|旋回|パン|ズーム|追従|"
    r"上昇|下降|横滑り|振る|回転)"
)
_CAMERA_CONFLICT_TERMS = {
    "push": ("後退", "遠ざか"),
    "pull": ("接近", "近づ", "前進"),
    "static": (
        "接近",
        "近づ",
        "後退",
        "遠ざか",
        "移動",
        "回り込",
        "追従",
        "上昇",
        "下降",
        "横滑り",
    ),
}
_ROLL_DIRECTION_TERMS = (
    "時計回り",
    "反時計回り",
    "右回り",
    "左回り",
    "右方向",
    "左方向",
    "右へ回転",
    "左へ回転",
    "clockwise",
    "counterclockwise",
)
_EXPLICIT_SUBJECT_BEFORE_TERM_RE = re.compile(
    r"(?:被写体|人物|対象|<Subject [1-9][0-9]*>)が[^。、]{0,32}$"
)


def _text(value: object, context: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlannerResponseError(f"{context} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise PlannerResponseError(f"{context} exceeds {maximum} characters")
    for marker in _FORBIDDEN_TEXT_MARKERS:
        if marker in result:
            raise PlannerResponseError(f"{context} contains forbidden marker {marker!r}")
    return result


def _camera_description(value: object, context: str) -> str:
    """Normalize a harmless subject prefix before deterministic rendering."""

    result = _text(value, context)
    for prefix in ("カメラは", "カメラが"):
        if result.startswith(prefix):
            result = result[len(prefix) :].lstrip(" 、,：:")
            if not result:
                raise PlannerResponseError(
                    f"{context} must describe the camera framing or motion"
                )
            break
    if result.startswith("カメラ"):
        raise PlannerResponseError(
            f"{context} must be a predicate following 'カメラは'"
        )
    return result


def _lines(content: str, context: str) -> list[str]:
    if not isinstance(content, str) or not content.strip():
        raise PlannerResponseError(f"{context} line-protocol response is empty")
    return [line.strip() for line in content.splitlines() if line.strip()]


def _field(line: str, name: str, context: str) -> str:
    prefix = f"{name}\t"
    if not line.startswith(prefix):
        raise PlannerResponseError(f"{context} expected {name} line")
    return _text(line[len(prefix) :], context)


def _verified_reference_text(
    value: str,
    context: str,
    protector: ReferenceProtector,
    *,
    maximum: int = 1000,
) -> str:
    result = _text(value, context, maximum=maximum)
    protector.restore(result)
    return result


def parse_song_bible_response(
    content: str,
    protector: ReferenceProtector,
    *,
    expected_sections: tuple[str, ...] = (),
) -> SongBible:
    lines = _lines(content, "Song bible")
    if lines[0] != "SONG_BIBLE" or lines[-1] != "END_SONG_BIBLE":
        raise PlannerResponseError(
            "Song bible must start with SONG_BIBLE and end with END_SONG_BIBLE"
        )
    body = lines[1:-1]
    if not body or not body[0].startswith("VISUAL_ARC\t"):
        raise PlannerResponseError("Song bible expected VISUAL_ARC as its first field")
    visual_arc = _verified_reference_text(
        _field(body[0], "VISUAL_ARC", "song_bible.visual_arc"),
        "song_bible.visual_arc",
        protector,
        maximum=2000,
    )
    if len(body) < 2 or not body[1].startswith(
        "VISUAL_ENRICHMENT_STRATEGY\t"
    ):
        raise PlannerResponseError(
            "Song bible expected VISUAL_ENRICHMENT_STRATEGY after VISUAL_ARC"
        )
    visual_enrichment_strategy = _verified_reference_text(
        _field(
            body[1],
            "VISUAL_ENRICHMENT_STRATEGY",
            "song_bible.visual_enrichment_strategy",
        ),
        "song_bible.visual_enrichment_strategy",
        protector,
        maximum=2000,
    )
    camera: list[str] = []
    motifs: list[SectionMotif] = []
    phase = "camera"
    for line_number, line in enumerate(body[2:], start=4):
        if line.startswith("CONTINUITY_RULE\t"):
            raise PlannerResponseError(
                f"Song bible line {line_number} uses removed CONTINUITY_RULE field"
            )
        if line.startswith("CAMERA_STRATEGY\t"):
            if phase == "motif":
                raise PlannerResponseError(
                    f"Song bible line {line_number} places CAMERA_STRATEGY out of order"
                )
            phase = "camera"
            camera.append(
                _verified_reference_text(
                    _field(line, "CAMERA_STRATEGY", "song_bible.camera_strategy"),
                    "song_bible.camera_strategy",
                    protector,
                )
            )
            continue
        if line.startswith("SECTION_MOTIF\t"):
            phase = "motif"
            parts = line.split("\t", 2)
            if len(parts) != 3:
                raise PlannerResponseError(
                    f"Song bible line {line_number} has invalid SECTION_MOTIF fields"
                )
            section = _text(parts[1], "song_bible.section_motif.section", maximum=100)
            motif = _verified_reference_text(
                parts[2], "song_bible.section_motif.motif", protector
            )
            motifs.append(SectionMotif(section=section, motif=motif))
            continue
        raise PlannerResponseError(
            f"Song bible line {line_number} uses an unknown or misplaced field"
        )
    if not 1 <= len(camera) <= 16:
        raise PlannerResponseError("Song bible requires 1-16 CAMERA_STRATEGY lines")
    if len(motifs) > 64:
        raise PlannerResponseError("Song bible permits at most 64 SECTION_MOTIF lines")
    actual_sections = tuple(item.section for item in motifs)
    if actual_sections != expected_sections:
        raise PlannerResponseError(
            "Song bible SECTION_MOTIF lines must contain every supplied section "
            f"exactly once in first-appearance order; expected={expected_sections}, "
            f"got={actual_sections}"
        )
    return SongBible(
        visual_arc=visual_arc,
        camera_strategy=tuple(camera),
        visual_enrichment_strategy=visual_enrichment_strategy,
        section_motifs=tuple(motifs),
    )


def camera_guard_issues(scene: PlannedScene) -> list[dict[str, object]]:
    """Return conservative, high-confidence camera-field diagnostics.

    The check deliberately ignores ordinary verbs in composition and actions,
    except when an ACTION explicitly names the camera, viewpoint, or lens.
    """

    issues: list[dict[str, object]] = []
    for shot_number, shot in enumerate(scene.shots, start=1):
        camera = shot.camera
        description = unicodedata.normalize("NFKC", camera.description)

        for term in _CAMERA_CONFLICT_TERMS.get(camera.type, ()):
            term_index = description.find(term)
            if term_index >= 0 and not _EXPLICIT_SUBJECT_BEFORE_TERM_RE.search(
                description[:term_index]
            ):
                issues.append(
                    {
                        "code": f"camera_{camera.type}_semantic_conflict",
                        "scene_id": scene.scene_id,
                        "shot_number": shot_number,
                        "camera_type": camera.type,
                        "description": camera.description,
                        "term": term,
                        "message": (
                            f"Scene {scene.scene_id} Shot {shot_number} camera type "
                            f"{camera.type!r} may conflict with description containing "
                            f"{term!r}"
                        ),
                    }
                )
                break

        pan_translation = next(
            (
                term
                for term in ("平行移動", "横方向へ移動", "横移動", "横滑り")
                if term in description
                and not _EXPLICIT_SUBJECT_BEFORE_TERM_RE.search(
                    description[: description.find(term)]
                )
            ),
            None,
        )
        if camera.type == "pan" and pan_translation is not None:
            issues.append(
                {
                    "code": "camera_pan_translation_conflict",
                    "scene_id": scene.scene_id,
                    "shot_number": shot_number,
                    "camera_type": camera.type,
                    "description": camera.description,
                    "message": (
                        f"Scene {scene.scene_id} Shot {shot_number} camera type 'pan' "
                        "may conflict with a translating camera description"
                    ),
                }
            )
        if camera.type == "truck" and "固定位置" in description and any(
            term in description for term in ("振る", "旋回", "向きを変")
        ):
            issues.append(
                {
                    "code": "camera_truck_pivot_conflict",
                    "scene_id": scene.scene_id,
                    "shot_number": shot_number,
                    "camera_type": camera.type,
                    "description": camera.description,
                    "message": (
                        f"Scene {scene.scene_id} Shot {shot_number} camera type 'truck' "
                        "may conflict with a fixed-position pivot description"
                    ),
                }
            )
        if camera.type == "roll" and not any(
            term.casefold() in description.casefold()
            for term in _ROLL_DIRECTION_TERMS
        ):
            issues.append(
                {
                    "code": "camera_roll_direction_missing",
                    "scene_id": scene.scene_id,
                    "shot_number": shot_number,
                    "camera_type": camera.type,
                    "description": camera.description,
                    "message": (
                        f"Scene {scene.scene_id} Shot {shot_number} camera type 'roll' "
                        "does not specify a rotation direction"
                    ),
                }
            )

        for action_number, action in enumerate(shot.subject_actions, start=1):
            if _CAMERA_ACTION_RE.search(unicodedata.normalize("NFKC", action)):
                issues.append(
                    {
                        "code": "camera_motion_in_action",
                        "scene_id": scene.scene_id,
                        "shot_number": shot_number,
                        "action_number": action_number,
                        "camera_type": camera.type,
                        "description": camera.description,
                        "message": (
                            f"Scene {scene.scene_id} Shot {shot_number} ACTION "
                            f"{action_number} contains explicit camera motion while a "
                            "separate CAMERA field is present"
                        ),
                    }
                )
        for visual_number, visual in enumerate(shot.auxiliary_visuals, start=1):
            if _CAMERA_ACTION_RE.search(
                unicodedata.normalize("NFKC", visual.description)
            ):
                issues.append(
                    {
                        "code": "camera_motion_in_auxiliary_visual",
                        "scene_id": scene.scene_id,
                        "shot_number": shot_number,
                        "visual_number": visual_number,
                        "camera_type": camera.type,
                        "description": camera.description,
                        "message": (
                            f"Scene {scene.scene_id} Shot {shot_number} AUX_VISUAL "
                            f"{visual_number} contains explicit camera motion while a "
                            "separate CAMERA field is present"
                        ),
                    }
                )
    return issues


def vocal_guard_issues(
    scene: PlannedScene,
    timeline: TimelineScene,
) -> list[dict[str, object]]:
    """Return vocal cues that can create audio beyond the locked Timeline."""

    issues: list[dict[str, object]] = []
    for shot_number, shot in enumerate(scene.shots, start=1):
        joined = " ".join(
            (
                shot.composition,
                *shot.subject_actions,
                *(value.description for value in shot.auxiliary_visuals),
                shot.environment,
                shot.camera.description,
            )
        )
        for cue in _CREATIVE_VOCAL_CUES:
            if cue not in joined:
                continue
            if (
                timeline.state == "voiced"
                and cue in _LOCKED_SOURCE_VOCAL_VISUAL_CUES
            ):
                continue
            issues.append(
                {
                    "code": "unexpected_vocal_cue",
                    "scene_id": scene.scene_id,
                    "shot_number": shot_number,
                    "timeline_state": timeline.state,
                    "cue": cue,
                    "message": (
                        f"Scene {scene.scene_id} Shot {shot_number} introduces "
                        f"vocal cue {cue!r}; vocal behavior is locked by the "
                        "Timeline lip-sync and soundscape"
                    ),
                }
            )
    return issues


def scene_signature(scene: PlannedScene) -> tuple[object, ...]:
    """Create the exact normalized visual-plan signature used for deduplication."""

    def normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())

    signature: list[object] = []
    for shot in scene.shots:
        signature.extend(
            (
                shot.start_ms,
                normalize(shot.composition),
                tuple(normalize(value) for value in shot.subject_actions),
                tuple(
                    (value.kind, normalize(value.description))
                    for value in shot.auxiliary_visuals
                ),
                normalize(shot.environment),
                shot.camera.type,
                shot.camera.amplitude,
                shot.camera.speed,
                normalize(shot.camera.description),
            )
        )
    return tuple(signature)


def _parse_scene_record(
    lines: list[str],
    timeline: TimelineScene,
    protector: ReferenceProtector,
    visual_profile: VisualEnrichmentProfile,
) -> PlannedScene:
    scene_id = timeline.scene_id
    context = f"Scene {scene_id}"
    if lines[0] != f"SCENE\t{scene_id}":
        raise PlannerResponseError(f"{context} has an invalid SCENE header")
    if lines[-1] != "END_SCENE":
        raise PlannerResponseError(f"{context} is missing END_SCENE")
    cursor = 1
    if cursor >= len(lines) - 1:
        raise PlannerResponseError(f"{context} is empty")
    intent = protector.restore(
        _field(lines[cursor], "SCENE_INTENT", f"{context}.scene_intent")
    )
    cursor += 1
    shots: list[PlannedShot] = []
    previous_start = -1
    while cursor < len(lines) - 1:
        shot_number = len(shots) + 1
        shot_context = f"{context} Shot {shot_number}"
        header = lines[cursor].split("\t")
        if len(header) != 2 or header[0] != "SHOT":
            raise PlannerResponseError(f"{shot_context} expected SHOT line")
        try:
            start_ms = int(header[1])
        except ValueError as exc:
            raise PlannerResponseError(
                f"{shot_context}.start_ms must be an integer"
            ) from exc
        if shot_number == 1 and start_ms != 0:
            raise PlannerResponseError(f"{shot_context}.start_ms must be 0")
        if start_ms <= previous_start or start_ms >= timeline.duration_seconds * 1000:
            raise PlannerResponseError(
                f"{shot_context}.start_ms must increase and stay inside the Scene"
            )
        previous_start = start_ms
        cursor += 1
        if cursor >= len(lines) - 1:
            raise PlannerResponseError(f"{shot_context} is incomplete")
        composition = protector.restore(
            _field(lines[cursor], "COMPOSITION", f"{shot_context}.composition")
        )
        cursor += 1
        actions: list[str] = []
        while cursor < len(lines) - 1 and lines[cursor].startswith("ACTION\t"):
            parts = lines[cursor].split("\t", 2)
            expected_index = len(actions) + 1
            if len(parts) != 3 or parts[1] != str(expected_index):
                raise PlannerResponseError(
                    f"{shot_context} ACTION indices must be consecutive from 1"
                )
            actions.append(
                protector.restore(
                    _text(parts[2], f"{shot_context}.action[{expected_index}]")
                )
            )
            cursor += 1
        if not 1 <= len(actions) <= 8:
            raise PlannerResponseError(f"{shot_context} requires 1-8 ACTION lines")
        auxiliary_visuals: list[AuxiliaryVisual] = []
        while cursor < len(lines) - 1 and lines[cursor].startswith(
            "AUX_VISUAL\t"
        ):
            parts = lines[cursor].split("\t", 2)
            if len(parts) != 3:
                raise PlannerResponseError(
                    f"{shot_context} AUX_VISUAL requires kind and description"
                )
            if visual_profile.maximum_aux_visuals_per_scene == 0:
                raise PlannerResponseError(
                    f"{shot_context} profile {visual_profile.profile_id!r} forbids AUX_VISUAL"
                )
            kind = _text(
                parts[1], f"{shot_context}.auxiliary_visual.kind", maximum=100
            )
            if kind not in visual_profile.allowed_kinds:
                raise PlannerResponseError(
                    f"{shot_context} AUX_VISUAL uses unknown kind {kind!r} for "
                    f"profile {visual_profile.profile_id!r}"
                )
            description = protector.restore(
                _text(
                    parts[2],
                    f"{shot_context}.auxiliary_visual.description",
                )
            )
            auxiliary_visuals.append(
                AuxiliaryVisual(kind=kind, description=description)
            )
            cursor += 1
        if cursor >= len(lines) - 1:
            raise PlannerResponseError(f"{shot_context} is incomplete")
        environment = protector.restore(
            _field(lines[cursor], "ENVIRONMENT", f"{shot_context}.environment")
        )
        cursor += 1
        if cursor >= len(lines) - 1:
            raise PlannerResponseError(f"{shot_context} is incomplete")
        camera_parts = lines[cursor].split("\t", 4)
        if len(camera_parts) != 5 or camera_parts[0] != "CAMERA":
            raise PlannerResponseError(
                f"{shot_context}.camera requires type, amplitude, speed, and description"
            )
        _, camera_type, amplitude, speed, raw_description = camera_parts
        if camera_type not in CAMERA_TYPES:
            raise PlannerResponseError(
                f"{shot_context} uses unknown camera type {camera_type!r}"
            )
        if amplitude not in CAMERA_AMPLITUDES:
            raise PlannerResponseError(
                f"{shot_context} uses invalid camera amplitude {amplitude!r}"
            )
        if speed not in CAMERA_SPEEDS:
            raise PlannerResponseError(
                f"{shot_context} uses invalid camera speed {speed!r}"
            )
        if camera_type == "static" and amplitude != "none":
            raise PlannerResponseError(
                f"{shot_context} static camera requires amplitude='none'"
            )
        if camera_type != "static" and amplitude == "none":
            raise PlannerResponseError(
                f"{shot_context} moving camera cannot use amplitude='none'"
            )
        if camera_type == "static":
            speed = "none"
        elif speed == "none":
            raise PlannerResponseError(
                f"{shot_context} moving camera cannot use speed='none'"
            )
        description = protector.restore(
            _camera_description(raw_description, f"{shot_context}.camera.description")
        )
        cursor += 1
        if cursor >= len(lines) - 1 or lines[cursor] != "END_SHOT":
            raise PlannerResponseError(f"{shot_context} is missing END_SHOT")
        cursor += 1
        shots.append(
            PlannedShot(
                start_ms=start_ms,
                composition=composition,
                subject_actions=tuple(actions),
                environment=environment,
                camera=CameraPlan(
                    type=camera_type,
                    amplitude=amplitude,
                    speed=speed,
                    description=description,
                ),
                auxiliary_visuals=tuple(auxiliary_visuals),
            )
        )
        if len(shots) > 6:
            raise PlannerResponseError(f"{context} permits at most 6 Shots")
    if not shots:
        raise PlannerResponseError(f"{context} requires 1-6 Shots")
    visual_contract = visual_profile.scene_contract(scene_id)
    all_auxiliary_visuals = tuple(
        visual for shot in shots for visual in shot.auxiliary_visuals
    )
    visual_count = len(all_auxiliary_visuals)
    minimum = visual_profile.minimum_aux_visuals_per_scene
    maximum = visual_profile.maximum_aux_visuals_per_scene
    if not minimum <= visual_count <= maximum:
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} requires "
            f"{minimum}-{maximum} AUX_VISUAL line(s) across the Scene; got {visual_count}"
        )
    required_kind = visual_contract["required_kind"]
    if required_kind is not None and (
        visual_count != 1 or all_auxiliary_visuals[0].kind != required_kind
    ):
        actual_kinds = [value.kind for value in all_auxiliary_visuals]
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} requires AUX_VISUAL "
            f"kind {required_kind!r}; got {actual_kinds}"
        )
    return PlannedScene(scene_id=scene_id, scene_intent=intent, shots=tuple(shots))


def parse_scene_response(
    content: str,
    pending: dict[int, TimelineScene],
    protector: ReferenceProtector,
    *,
    visual_profile: VisualEnrichmentProfile | None = None,
) -> tuple[dict[int, PlannedScene], dict[int, str]]:
    if visual_profile is None:
        visual_profile = load_visual_profile(DEFAULT_VISUAL_PROFILE_ID)
    lines = _lines(content, "Scene planning")
    starts = [index for index, line in enumerate(lines) if line.startswith("SCENE\t")]
    if not starts or starts[0] != 0:
        raise PlannerResponseError(
            "Scene planning response must start with a SCENE record"
        )
    recovered: dict[int, PlannedScene] = {}
    errors: dict[int, str] = {}
    seen: set[int] = set()
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        header = block[0].split("\t")
        if len(header) != 2:
            continue
        try:
            scene_id = int(header[1])
        except ValueError:
            continue
        if scene_id not in pending:
            continue
        if scene_id in seen:
            recovered.pop(scene_id, None)
            errors[scene_id] = f"Scene {scene_id} appeared more than once"
            continue
        seen.add(scene_id)
        try:
            recovered[scene_id] = _parse_scene_record(
                block, pending[scene_id], protector, visual_profile
            )
        except PlannerResponseError as exc:
            errors[scene_id] = str(exc)
    for scene_id in pending:
        if scene_id not in recovered and scene_id not in errors:
            errors[scene_id] = f"Scene {scene_id} was omitted from the response"
    return recovered, errors
