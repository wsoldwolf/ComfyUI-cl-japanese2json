"""Strict parsing and validation of planner line-protocol responses."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import replace

from .camera_policy import fallback_arc_description, required_camera_sequence
from .errors import PlannerResponseError
from .placeholders import ReferenceProtector
from .structures import (
    AuxiliaryVisual,
    CameraPlan,
    LyricActionBlueprint,
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


LOGGER = logging.getLogger("cl_mv_prompt_planner")


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
LYRIC_RESPONSE_MODES = frozenset(
    {
        "direct_subject_action",
        "direct_object_action",
        "spatial_metaphor",
        "instrumental_continuity",
    }
)
_LOCALIZED_LYRIC_RESPONSE_MODE_ALIASES = {
    "人物直接動作": "direct_subject_action",
    "直接人物動作": "direct_subject_action",
    "主体直接動作": "direct_subject_action",
    "主体動作": "direct_subject_action",
    "物体直接動作": "direct_object_action",
    "直接物体動作": "direct_object_action",
    "物体動作": "direct_object_action",
    "空間的比喩": "spatial_metaphor",
    "空間比喩": "spatial_metaphor",
    "器楽継続": "instrumental_continuity",
    "インストゥルメンタル継続": "instrumental_continuity",
}
_AMBIGUOUS_DIRECT_ACTION_ALIASES = frozenset({"直接動作", "direct_action"})
_PENDING_DIRECT_ACTION_MODE = "__direct_action_pending__"
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
_INTERNAL_TIMELINE_REFERENCE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:scene|shot)\s*(?:number\s*)?#?\s*\d+(?!\d)|"
    r"(?:シーン|ショット)\s*(?:番号\s*)?#?\s*\d+(?!\d)|"
    r"第\s*\d+\s*(?:シーン|ショット)"
    r")"
)
_SCREEN_TEXT_CREATIVE_CUES = (
    "文字",
    "名前",
    "歌詞",
    "字幕",
    "文章",
    "単語",
    "字形",
    "記号",
    "標識",
    "ロゴ",
    "透かし",
    "タイポグラフィ",
    "キャプション",
    "テキスト",
)
_SCREEN_TEXT_CREATIVE_CUE_RE = re.compile(
    r"(?i)\b(?:text|letters?|words?|names?|glyphs?|captions?|subtitles?|"
    r"logos?|typography|inscriptions?|watermarks?|signage)\b"
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
_ARC_ONLY_TERMS = (
    "周回",
    "回り込",
    "背後を通",
    "周囲を回",
    "弧を描",
    "円弧",
    "半円",
    "アーク",
)
_ARC_VIEW_ZONES = (
    ("前方", "正面", "前面", "斜め前", "前左", "前右"),
    ("側面", "側方", "横"),
    ("後方", "背後", "後ろ", "斜め後", "後左", "後右"),
)
_DELIBERATE_SUBJECT_MOTION_RE = re.compile(
    r"(?:"
    r"歩|走|駆け|踏み|跳|飛び|落下|降下|上昇|前進|後退|横切|移動|"
    r"進む|退く|振り返|向き直|方向転換|ひね|捻|回転|旋回|しゃが|"
    r"屈|起き上|立ち上|重心を|体重を|姿勢を(?:変|低|高|立て直)|"
    r"身を(?:起|沈|伏|反|翻|乗り出|引|寄)|"
    r"(?:腕|手|指|脚|足)を(?:伸|引|上|下|振|差し|かざ|翳|握|開|閉|"
    r"交差|曲|広げ|戻|置|当て|動か|揺すぶ|集め)|"
    r"(?:身体|上半身|胴体|腰|肩|首|頭部|頭|顔)を"
    r"(?:傾|倒|起|ひね|捻|回|沈|引|押|振|動か|揺すぶ)|"
    r"掴|握|押し|引っ|引き抜|触れ|接触|離し|払|投げ|蹴|叩|刻|描|"
    r"彫|なぞ|集め|抱|受け止|持ち上|持ち替|乗り越|くぐ|跨"
    r")"
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


def _scene_intent_text(value: object, context: str) -> str:
    """Normalize harmless quotation marks in internal, non-rendered intent text."""

    if isinstance(value, str):
        value = value.replace("「", "").replace("」", "").replace('"', "")
    return _text(value, context)


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


def _reject_screen_text_creative_cues(value: str, context: str) -> None:
    """Reject prose that can make H3 synthesize letters or pseudo-letters.

    Lyrics remain available to the planner as protected source data. Creative
    fields, however, must describe only the physical operation and its
    irregular non-linguistic residue; naming language primes video models to
    draw typographic rows even when accompanied by "illegible".
    """

    for cue in _SCREEN_TEXT_CREATIVE_CUES:
        if cue in value:
            raise PlannerResponseError(
                f"{context} contains screen-text cue {cue!r}; describe only "
                "irregular non-linguistic scratches or grooves without a "
                "baseline, row, repeated glyph shape, or reflected lettering"
            )
    match = _SCREEN_TEXT_CREATIVE_CUE_RE.search(value)
    if match is not None:
        raise PlannerResponseError(
            f"{context} contains screen-text cue {match.group(0)!r}; describe "
            "only irregular non-linguistic scratches or grooves without a "
            "baseline, row, repeated glyph shape, or reflected lettering"
        )


def _lines(content: str, context: str) -> list[str]:
    if not isinstance(content, str) or not content.strip():
        raise PlannerResponseError(f"{context} line-protocol response is empty")
    return [line.strip() for line in content.splitlines() if line.strip()]


def _field(line: str, name: str, context: str) -> str:
    prefix = f"{name}\t"
    if not line.startswith(prefix):
        raise PlannerResponseError(f"{context} expected {name} line")
    return _text(line[len(prefix) :], context)


def _auxiliary_visual(
    line: str,
    context: str,
    protector: ReferenceProtector,
    visual_profile: VisualEnrichmentProfile,
) -> AuxiliaryVisual:
    """Parse one AUX_VISUAL line independently of its recoverable position."""

    parts = line.split("\t", 2)
    if len(parts) != 3:
        raise PlannerResponseError(
            f"{context} AUX_VISUAL requires kind and description"
        )
    if visual_profile.maximum_aux_visuals_per_scene == 0:
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} forbids AUX_VISUAL"
        )
    kind = _text(parts[1], f"{context}.auxiliary_visual.kind", maximum=100)
    if kind not in visual_profile.allowed_kinds:
        raise PlannerResponseError(
            f"{context} AUX_VISUAL uses unknown kind {kind!r} for "
            f"profile {visual_profile.profile_id!r}"
        )
    description = protector.restore(
        _text(parts[2], f"{context}.auxiliary_visual.description")
    )
    return AuxiliaryVisual(kind=kind, description=description)


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


def _lyric_blueprint_text(
    value: str,
    context: str,
    protector: ReferenceProtector,
) -> str:
    """Restore preplan text and recover exact known raw Subject tags.

    Small models sometimes decode ``CLMPSUB1X`` back to its user-facing
    ``<Subject 1>`` spelling.  That is lossless only when the exact Subject is
    already in the reference legend.  Unknown Subjects and every other raw
    reference kind remain hard errors.
    """

    raw_subjects = tuple(
        dict.fromkeys(re.findall(r"<Subject [1-9][0-9]*>", value))
    )
    restored = protector.restore(
        value,
        allow_known_raw_subjects=True,
    )
    if raw_subjects:
        LOGGER.warning(
            "[cl_mv_prompt_planner] %s normalized known raw Subject "
            "reference(s) %s back through the protected reference legend",
            context,
            ", ".join(raw_subjects),
        )
    return restored


def parse_song_bible_response(
    content: str,
    protector: ReferenceProtector,
    *,
    expected_sections: tuple[str, ...] = (),
) -> SongBible:
    lines = _lines(content, "Song bible")
    if lines[0] != "SONG_BIBLE":
        raise PlannerResponseError(
            "Song bible must start with SONG_BIBLE and end with END_SONG_BIBLE"
        )
    omitted_end_marker = False
    if lines[-1] == "END_SONG_BIBLE":
        body = lines[1:-1]
    elif "END_SONG_BIBLE" in lines:
        raise PlannerResponseError(
            "Song bible must start with SONG_BIBLE and end with END_SONG_BIBLE"
        )
    else:
        # Small models sometimes finish cleanly after the final expected motif
        # without emitting the redundant closing sentinel.  Parse every
        # remaining line strictly; only a fully valid and complete body can
        # reach the successful return below.
        omitted_end_marker = True
        body = lines[1:]
    if not body:
        raise PlannerResponseError("Song bible has no fields")
    synthesized_visual_arc = False
    body_index = 0
    if body[0].startswith("VISUAL_ARC\t"):
        visual_arc = _verified_reference_text(
            _field(body[0], "VISUAL_ARC", "song_bible.visual_arc"),
            "song_bible.visual_arc",
            protector,
            maximum=2000,
        )
        body_index = 1
    elif body[0].startswith("VISUAL_ENRICHMENT_STRATEGY\t"):
        # An 8B model can reproducibly skip only the first creative field while
        # returning a complete, ordered remainder.  Repeating the same full
        # request cannot repair that deterministic omission.  Use a deliberately
        # conservative arc which adds no object, action, style, or Subject state;
        # the remainder is still validated strictly before this result is used.
        visual_arc = (
            "全Sceneを通して各楽曲セクション固有の視覚変化を初出順に展開し、"
            "固定された人物、世界及び連続性を維持する。"
        )
        synthesized_visual_arc = True
    else:
        raise PlannerResponseError("Song bible expected VISUAL_ARC as its first field")
    if synthesized_visual_arc and omitted_end_marker:
        raise PlannerResponseError(
            "Song bible cannot recover both an omitted VISUAL_ARC and an "
            "omitted END_SONG_BIBLE"
        )
    if len(body) <= body_index or not body[body_index].startswith(
        "VISUAL_ENRICHMENT_STRATEGY\t"
    ):
        raise PlannerResponseError(
            "Song bible expected VISUAL_ENRICHMENT_STRATEGY after VISUAL_ARC"
        )
    visual_enrichment_strategy = _verified_reference_text(
        _field(
            body[body_index],
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
    for line_number, line in enumerate(body[body_index + 1 :], start=body_index + 3):
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
    result = SongBible(
        visual_arc=visual_arc,
        camera_strategy=tuple(camera),
        visual_enrichment_strategy=visual_enrichment_strategy,
        section_motifs=tuple(motifs),
    )
    if synthesized_visual_arc:
        LOGGER.warning(
            "[cl_mv_prompt_planner] Song bible omitted VISUAL_ARC; supplied a "
            "conservative continuity-only fallback after validating every "
            "remaining field"
        )
    if omitted_end_marker:
        LOGGER.warning(
            "[cl_mv_prompt_planner] Song bible omitted END_SONG_BIBLE; "
            "accepted the otherwise complete and structurally valid body"
        )
    return result


def _parse_lyric_action_record(
    lines: list[str],
    timeline: TimelineScene,
    protector: ReferenceProtector,
) -> LyricActionBlueprint:
    """Parse one compact semantic blueprint without accepting creative prose."""

    context = f"Scene {timeline.scene_id} lyric blueprint"
    if len(lines) < 7 or lines[-1] != "END_LYRIC_SCENE":
        raise PlannerResponseError(
            f"{context} must end with END_LYRIC_SCENE"
        )
    header = lines[0].split("\t")
    if len(header) != 2 or header[0] != "LYRIC_SCENE":
        raise PlannerResponseError(f"{context} expected LYRIC_SCENE header")
    try:
        scene_id = int(header[1])
    except ValueError as exc:
        raise PlannerResponseError(
            f"{context} scene_id must be an integer"
        ) from exc
    if scene_id != timeline.scene_id:
        raise PlannerResponseError(
            f"{context} returned mismatched Scene {scene_id}"
        )

    response = lines[1].split("\t")
    if len(response) != 3 or response[0] != "LYRIC_RESPONSE":
        raise PlannerResponseError(f"{context} expected LYRIC_RESPONSE")
    try:
        anchor_index = int(response[1])
    except ValueError as exc:
        raise PlannerResponseError(
            f"{context} lyric anchor must be an integer"
        ) from exc
    mode = response[2].strip()
    if not 1 <= anchor_index <= len(timeline.lyrics):
        raise PlannerResponseError(
            f"{context} lyric anchor {anchor_index} is outside lyric_lines"
        )
    if mode not in {
        "direct_subject_action",
        "direct_object_action",
        "spatial_metaphor",
    }:
        raise PlannerResponseError(
            f"{context} uses unknown response mode {mode!r}"
        )

    composition = _lyric_blueprint_text(
        _field(
            lines[2],
            "COMPOSITION_REQUIREMENT",
            f"{context}.composition_requirement",
        ),
        f"{context}.composition_requirement",
        protector,
    )
    cursor = 3
    actions: list[str] = []
    while cursor < len(lines) - 2 and lines[cursor].startswith("ACTION\t"):
        parts = lines[cursor].split("\t", 2)
        if len(parts) != 3:
            raise PlannerResponseError(
                f"{context} ACTION requires index and text"
            )
        expected = len(actions) + 1
        try:
            actual = int(parts[1])
        except ValueError as exc:
            raise PlannerResponseError(
                f"{context} ACTION index must be an integer"
            ) from exc
        if actual != expected:
            raise PlannerResponseError(
                f"{context} ACTION indices must be consecutive from 1"
            )
        action_context = f"{context}.action[{expected}]"
        actions.append(
            _lyric_blueprint_text(
                _text(parts[2], action_context),
                action_context,
                protector,
            )
        )
        cursor += 1
    if not 2 <= len(actions) <= 4:
        raise PlannerResponseError(f"{context} requires 2-4 ACTION lines")
    if cursor != len(lines) - 2:
        raise PlannerResponseError(
            f"{context} expected VISIBLE_RESULT after ACTION lines"
        )
    visible_result = _lyric_blueprint_text(
        _field(
            lines[cursor],
            "VISIBLE_RESULT",
            f"{context}.visible_result",
        ),
        f"{context}.visible_result",
        protector,
    )
    creative_values = [composition, *actions, visible_result]
    for value in creative_values:
        _reject_screen_text_creative_cues(value, context)
        match = _INTERNAL_TIMELINE_REFERENCE_RE.search(value)
        if match is not None:
            raise PlannerResponseError(
                f"{context} contains forbidden internal timeline reference "
                f"{match.group(0)!r}"
            )
        if _CAMERA_ACTION_RE.search(value):
            raise PlannerResponseError(
                f"{context} must not contain camera choreography"
            )
        for cue in _CREATIVE_VOCAL_CUES:
            if cue in value:
                raise PlannerResponseError(
                    f"{context} contains forbidden vocal cue {cue!r}"
                )
    if mode == "direct_subject_action" and not any(
        re.search(r"<Subject [1-9][0-9]*>", action) for action in actions
    ):
        raise PlannerResponseError(
            f"{context} direct_subject_action requires an ACTION naming <Subject N>"
        )
    return LyricActionBlueprint(
        scene_id=scene_id,
        lyric_anchor_index=anchor_index,
        lyric_response_mode=mode,
        composition_requirement=composition,
        subject_actions=tuple(actions),
        visible_result=visible_result,
    )


def parse_lyric_action_response(
    content: str,
    pending: dict[int, TimelineScene],
    protector: ReferenceProtector,
) -> tuple[dict[int, LyricActionBlueprint], dict[int, str]]:
    """Recover valid lyric blueprints independently from one batched response."""

    lines = _lines(content, "Lyric action planning")
    starts = [
        index for index, line in enumerate(lines) if line.startswith("LYRIC_SCENE\t")
    ]
    if not starts or starts[0] != 0:
        raise PlannerResponseError(
            "Lyric action response must start with a LYRIC_SCENE record"
        )
    recovered: dict[int, LyricActionBlueprint] = {}
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
            errors[scene_id] = f"Scene {scene_id} lyric blueprint appeared more than once"
            continue
        seen.add(scene_id)
        try:
            blueprint = _parse_lyric_action_record(
                block, pending[scene_id], protector
            )
            motion_issues = _subject_action_motion_issues(
                scene_id=scene_id,
                actions=blueprint.subject_actions,
                duration_seconds=pending[scene_id].duration_seconds,
            )
            if motion_issues:
                blueprint, added_actions = repair_lyric_action_motion(
                    blueprint,
                    duration_seconds=pending[scene_id].duration_seconds,
                )
                if not added_actions:
                    raise PlannerResponseError(
                        "; ".join(
                            str(issue["message"]) for issue in motion_issues
                        )
                    )
                LOGGER.warning(
                    "[cl_mv_prompt_planner] Scene %d lyric action blueprint "
                    "lacked purposeful whole-Subject motion; added %d "
                    "deterministic retention-safe phase(s) instead of "
                    "retrying the LLM",
                    scene_id,
                    len(added_actions),
                )
            recovered[scene_id] = blueprint
        except PlannerResponseError as exc:
            errors[scene_id] = str(exc)
    for scene_id in pending:
        if scene_id not in seen:
            errors[scene_id] = f"Scene {scene_id} lyric blueprint is missing"
    return recovered, errors


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

        if camera.type != "arc":
            arc_term = next(
                (term for term in _ARC_ONLY_TERMS if term in description),
                None,
            )
            if arc_term is not None:
                issues.append(
                    {
                        "code": "camera_non_arc_orbit_conflict",
                        "scene_id": scene.scene_id,
                        "shot_number": shot_number,
                        "camera_type": camera.type,
                        "description": camera.description,
                        "term": arc_term,
                        "message": (
                            f"Scene {scene.scene_id} Shot {shot_number} camera type "
                            f"{camera.type!r} describes an arc/orbit using "
                            f"{arc_term!r}"
                        ),
                    }
                )

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


def _subject_action_motion_issues(
    *,
    scene_id: int,
    actions: list[tuple[int, int, str]] | tuple[str, ...],
    duration_seconds: int,
) -> list[dict[str, object]]:
    """Validate purposeful Subject motion in either blueprint or Scene form."""

    if actions and isinstance(actions[0], str):
        indexed_actions = [
            (1, action_number, action)
            for action_number, action in enumerate(actions, start=1)
        ]
    else:
        indexed_actions = list(actions)
    named_actions = [
        value
        for value in indexed_actions
        if re.search(r"<Subject [1-9][0-9]*>", value[2])
    ]
    if not named_actions:
        return []
    deliberate = [
        value
        for value in named_actions
        if _DELIBERATE_SUBJECT_MOTION_RE.search(
            unicodedata.normalize("NFKC", value[2])
        )
    ]
    required = 2 if duration_seconds >= 10 else 1
    unique_deliberate = {
        " ".join(unicodedata.normalize("NFKC", value[2]).split())
        for value in deliberate
    }
    if len(unique_deliberate) >= required:
        return []
    return [
        {
            "code": "subject_deliberate_motion_missing",
            "scene_id": scene_id,
            "required_phases": required,
            "actual_phases": len(unique_deliberate),
            "message": (
                f"Scene {scene_id} names a Subject in ACTION but has "
                f"{len(unique_deliberate)}/{required} deliberate bodily motion "
                "phase(s); passive floating, bobbing, swaying, breathing, gaze, "
                "expression, mouth, hair, clothing, effects, or camera motion "
                "does not count"
            ),
        }
    ]


def subject_motion_issues(
    scene: PlannedScene,
    *,
    duration_seconds: int,
) -> list[dict[str, object]]:
    """Reject Subject performance made only of passive or facial motion.

    The check is intentionally activated only when ACTION lines themselves
    name a Subject.  Object-only and abstract cutaway Scenes therefore remain
    valid, while a visible performer cannot satisfy the contract by bobbing,
    swaying, blinking, or lip movement alone.
    """

    indexed_actions = [
        (shot_number, action_number, action)
        for shot_number, shot in enumerate(scene.shots, start=1)
        for action_number, action in enumerate(shot.subject_actions, start=1)
    ]
    return _subject_action_motion_issues(
        scene_id=scene.scene_id,
        actions=indexed_actions,
        duration_seconds=duration_seconds,
    )


def _motion_phase_additions(
    *,
    scene_id: int,
    subject: str,
    count: int,
) -> tuple[str, ...]:
    templates = (
        (
            "{subject}は現在の形状と外観を維持したまま、身体全体を連動させて"
            "画面内を斜め前方へ大きく移動し、その方向へ動き続ける。"
        ),
        (
            "{subject}は現在の形状と外観を維持したまま、身体全体の向きを変え"
            "ながら横方向へ加速し、別の位置へ移動を続ける。"
        ),
        (
            "{subject}は現在の形状と外観を維持したまま、身体全体を後方へ大きく"
            "退かせた直後に前方へ切り返し、異なる軌道で移動を続ける。"
        ),
    )
    return tuple(
        templates[(scene_id - 1 + index) % len(templates)].format(
            subject=subject
        )
        for index in range(count)
    )


def repair_lyric_action_motion(
    blueprint: LyricActionBlueprint,
    *,
    duration_seconds: int,
) -> tuple[LyricActionBlueprint, tuple[str, ...]]:
    """Complete a structurally valid lyric blueprint without another LLM call.

    The model-authored lyric decomposition remains in order.  Missing generic
    whole-Subject phases are appended up to the four-ACTION protocol limit. If
    the record is already full, the same phase is attached to an existing line
    so the original lyric operation is not discarded.
    """

    issues = _subject_action_motion_issues(
        scene_id=blueprint.scene_id,
        actions=blueprint.subject_actions,
        duration_seconds=duration_seconds,
    )
    if not issues:
        return blueprint, ()
    issue = issues[0]
    missing = max(
        0,
        int(issue["required_phases"]) - int(issue["actual_phases"]),
    )
    subject_match = next(
        (
            re.search(r"<Subject [1-9][0-9]*>", action)
            for action in blueprint.subject_actions
            if re.search(r"<Subject [1-9][0-9]*>", action)
        ),
        None,
    )
    if missing <= 0 or subject_match is None:
        return blueprint, ()
    additions = _motion_phase_additions(
        scene_id=blueprint.scene_id,
        subject=subject_match.group(0),
        count=missing,
    )
    actions = list(blueprint.subject_actions)
    remaining: list[str] = []
    for addition in additions:
        if len(actions) < 4:
            actions.append(addition)
        else:
            remaining.append(addition)
    if remaining:
        candidate_indices = [
            index
            for index, action in enumerate(actions)
            if not _DELIBERATE_SUBJECT_MOTION_RE.search(
                unicodedata.normalize("NFKC", action)
            )
        ]
        for addition, target_index in zip(
            remaining,
            reversed(candidate_indices),
        ):
            combined = f"{actions[target_index].rstrip('。')}。続いて{addition}"
            actions[target_index] = combined if len(combined) <= 1000 else addition
    repaired = replace(blueprint, subject_actions=tuple(actions))
    if _subject_action_motion_issues(
        scene_id=repaired.scene_id,
        actions=repaired.subject_actions,
        duration_seconds=duration_seconds,
    ):
        return blueprint, ()
    return repaired, additions


def repair_subject_motion(
    scene: PlannedScene,
    *,
    duration_seconds: int,
) -> tuple[PlannedScene, tuple[str, ...]]:
    """Add a bounded whole-Subject motion floor without another LLM call.

    This recovery is intentionally independent of lyric, medium, anatomy, and
    environment.  It preserves every model-authored line and appends only the
    minimum number of missing motion phases.  Lyric-action profiles do not use
    this path because their immutable ACTIONs are validated in the repairable
    blueprint stage.
    """

    issues = subject_motion_issues(
        scene,
        duration_seconds=duration_seconds,
    )
    if not issues:
        return scene, ()
    issue = issues[0]
    missing = max(
        0,
        int(issue["required_phases"]) - int(issue["actual_phases"]),
    )
    subject_match = next(
        (
            re.search(r"<Subject [1-9][0-9]*>", action)
            for shot in scene.shots
            for action in shot.subject_actions
            if re.search(r"<Subject [1-9][0-9]*>", action)
        ),
        None,
    )
    if missing <= 0 or subject_match is None:
        return scene, ()
    subject = subject_match.group(0)
    additions = _motion_phase_additions(
        scene_id=scene.scene_id,
        subject=subject,
        count=missing,
    )
    mutable_actions = [list(shot.subject_actions) for shot in scene.shots]
    for index, action in enumerate(additions):
        preferred = min(
            (index * len(scene.shots)) // len(additions),
            len(scene.shots) - 1,
        )
        candidates = [
            *range(preferred, len(scene.shots)),
            *range(0, preferred),
        ]
        target = next(
            (value for value in candidates if len(mutable_actions[value]) < 8),
            None,
        )
        if target is None:
            # Every Shot already has the protocol maximum.  Replacing the
            # final passive line is safer than creating an invalid 9th line.
            target = preferred
            mutable_actions[target][-1] = action
        else:
            mutable_actions[target].append(action)
    repaired_shots = tuple(
        replace(shot, subject_actions=tuple(mutable_actions[index]))
        for index, shot in enumerate(scene.shots)
    )
    repaired = replace(scene, shots=repaired_shots)
    if subject_motion_issues(
        repaired,
        duration_seconds=duration_seconds,
    ):
        return scene, ()
    return repaired, additions


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


def auxiliary_visual_signatures(scene: PlannedScene) -> tuple[str, ...]:
    """Return exact normalized descriptions used for repetition rejection.

    The kind is intentionally excluded: returning the same visible sentence
    under another label is still the same auxiliary event.  This remains an
    exact surface check and does not attempt semantic similarity.
    """

    return tuple(
        auxiliary_visual_description_signature(visual.description)
        for shot in scene.shots
        for visual in shot.auxiliary_visuals
    )


def auxiliary_visual_description_signature(description: str) -> str:
    """Normalize one AUX_VISUAL description for exact repetition checks."""

    return " ".join(unicodedata.normalize("NFKC", description).split())


def parse_auxiliary_visual_repair_response(
    content: str,
    protector: ReferenceProtector,
    *,
    expected_kind: str,
    visual_profile: VisualEnrichmentProfile,
) -> AuxiliaryVisual:
    """Parse the bounded one-line response used by targeted AUX repair."""

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) != 1:
        raise PlannerResponseError(
            "Targeted AUX_VISUAL repair must return exactly one non-empty line"
        )
    visual = _auxiliary_visual(
        lines[0], "Targeted AUX_VISUAL repair", protector, visual_profile
    )
    if visual.kind != expected_kind:
        raise PlannerResponseError(
            "Targeted AUX_VISUAL repair requires kind "
            f"{expected_kind!r}; got {visual.kind!r}"
        )
    match = _INTERNAL_TIMELINE_REFERENCE_RE.search(visual.description)
    if match is not None:
        raise PlannerResponseError(
            "Targeted AUX_VISUAL repair contains internal numbered reference "
            f"{match.group(0)!r}"
        )
    return visual


def _reject_internal_timeline_references(
    shots: tuple[PlannedShot, ...],
    *,
    scene_id: int,
) -> None:
    """Keep planner-only Scene/Shot identifiers out of H3-visible prose."""

    for shot_index, shot in enumerate(shots, start=1):
        fields = [
            ("COMPOSITION", shot.composition),
            *(
                (f"ACTION {action_index}", action)
                for action_index, action in enumerate(
                    shot.subject_actions, start=1
                )
            ),
            *(
                ("AUX_VISUAL", visual.description)
                for visual in shot.auxiliary_visuals
            ),
            ("ENVIRONMENT", shot.environment),
            ("CAMERA", shot.camera.description),
        ]
        for field_name, value in fields:
            match = _INTERNAL_TIMELINE_REFERENCE_RE.search(value)
            if match is not None:
                raise PlannerResponseError(
                    f"Scene {scene_id} Shot {shot_index} {field_name} contains "
                    f"internal numbered reference {match.group(0)!r}; restate the "
                    "inherited visible state directly without a Scene or Shot number"
                )


def _reject_screen_text_in_shots(
    shots: tuple[PlannedShot, ...],
    *,
    scene_id: int,
) -> None:
    """Keep typography-priming language out of every H3-visible field."""

    for shot_index, shot in enumerate(shots, start=1):
        fields = [
            ("COMPOSITION", shot.composition),
            *(
                (f"ACTION {action_index}", action)
                for action_index, action in enumerate(
                    shot.subject_actions, start=1
                )
            ),
            *(
                ("AUX_VISUAL", visual.description)
                for visual in shot.auxiliary_visuals
            ),
            ("ENVIRONMENT", shot.environment),
            ("CAMERA", shot.camera.description),
        ]
        for field_name, value in fields:
            _reject_screen_text_creative_cues(
                value,
                f"Scene {scene_id} Shot {shot_index} {field_name}",
            )


def _parse_scene_record(
    lines: list[str],
    timeline: TimelineScene,
    protector: ReferenceProtector,
    visual_profile: VisualEnrichmentProfile,
) -> PlannedScene:
    scene_id = timeline.scene_id
    context = f"Scene {scene_id}"
    visual_contract = visual_profile.scene_contract(scene_id)
    if lines[0] != f"SCENE\t{scene_id}":
        raise PlannerResponseError(f"{context} has an invalid SCENE header")
    if lines[-1] != "END_SCENE":
        raise PlannerResponseError(f"{context} is missing END_SCENE")
    cursor = 1
    if cursor >= len(lines) - 1:
        raise PlannerResponseError(f"{context} is empty")
    intent_line = lines[cursor]
    intent_prefix = "SCENE_INTENT\t"
    if not intent_line.startswith(intent_prefix):
        raise PlannerResponseError(f"{context}.scene_intent expected SCENE_INTENT line")
    intent = protector.restore(
        _scene_intent_text(
            intent_line[len(intent_prefix) :], f"{context}.scene_intent"
        )
    )
    cursor += 1
    if cursor >= len(lines) - 1:
        raise PlannerResponseError(f"{context} is missing LYRIC_RESPONSE")
    lyric_response_parts = lines[cursor].split("\t")
    if (
        len(lyric_response_parts) != 3
        or lyric_response_parts[0] != "LYRIC_RESPONSE"
    ):
        raise PlannerResponseError(
            f"{context}.lyric_response requires anchor index and mode"
        )
    try:
        lyric_anchor_index = int(lyric_response_parts[1])
    except ValueError as exc:
        raise PlannerResponseError(
            f"{context}.lyric_response anchor index must be an integer"
        ) from exc
    raw_lyric_response_mode = lyric_response_parts[2].strip()
    pending_mode_reason: str | None = None
    if raw_lyric_response_mode in LYRIC_RESPONSE_MODES:
        lyric_response_mode = raw_lyric_response_mode
    elif raw_lyric_response_mode in _LOCALIZED_LYRIC_RESPONSE_MODE_ALIASES:
        lyric_response_mode = _LOCALIZED_LYRIC_RESPONSE_MODE_ALIASES[
            raw_lyric_response_mode
        ]
        LOGGER.warning(
            "[cl_mv_prompt_planner] %s normalized localized LYRIC_RESPONSE "
            "mode %r to %r",
            context,
            raw_lyric_response_mode,
            lyric_response_mode,
        )
    elif raw_lyric_response_mode in _AMBIGUOUS_DIRECT_ACTION_ALIASES:
        lyric_response_mode = _PENDING_DIRECT_ACTION_MODE
        pending_mode_reason = "ambiguous localized mode"
    elif raw_lyric_response_mode == visual_contract["required_kind"]:
        lyric_response_mode = _PENDING_DIRECT_ACTION_MODE
        pending_mode_reason = "misbound AUX_VISUAL required_kind"
    else:
        raise PlannerResponseError(
            f"{context}.lyric_response uses unknown mode "
            f"{raw_lyric_response_mode!r}"
        )
    if timeline.lyrics:
        if not 1 <= lyric_anchor_index <= len(timeline.lyrics):
            raise PlannerResponseError(
                f"{context}.lyric_response anchor index must select one of "
                f"the {len(timeline.lyrics)} supplied lyric line(s)"
            )
        if lyric_response_mode == "instrumental_continuity":
            raise PlannerResponseError(
                f"{context}.lyric_response cannot use instrumental_continuity "
                "when lyric lines are supplied"
            )
    elif (
        lyric_anchor_index != 0
        or lyric_response_mode != "instrumental_continuity"
    ):
        raise PlannerResponseError(
            f"{context}.lyric_response must be "
            "'0 instrumental_continuity' when no lyric lines are supplied"
        )
    cursor += 1
    shots: list[PlannedShot] = []
    previous_start = -1
    scene_action_count = 0
    seen_auxiliary_visuals: set[tuple[str, str]] = set()
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
        action_index_origin: int | None = None
        while cursor < len(lines) - 1 and lines[cursor].startswith("ACTION\t"):
            parts = lines[cursor].split("\t", 2)
            if len(parts) != 3:
                raise PlannerResponseError(
                    f"{shot_context} ACTION requires index and text"
                )
            try:
                action_index = int(parts[1])
            except ValueError as exc:
                raise PlannerResponseError(
                    f"{shot_context} ACTION index must be an integer"
                ) from exc
            if action_index_origin is None:
                # Models commonly use either Shot-local numbering (1, 2, ...)
                # or one continuous ACTION sequence across a multi-Shot Scene.
                # Both preserve an unambiguous physical line order, so accept
                # either form and normalize to the tuple order below.
                allowed_origins = {1, scene_action_count + 1}
                if action_index not in allowed_origins:
                    allowed = " or ".join(
                        str(value) for value in sorted(allowed_origins)
                    )
                    raise PlannerResponseError(
                        f"{shot_context} ACTION indices must start at {allowed}"
                    )
                action_index_origin = action_index
            expected_index = action_index_origin + len(actions)
            if action_index != expected_index:
                raise PlannerResponseError(
                    f"{shot_context} ACTION indices must be consecutive from "
                    f"{action_index_origin}"
                )
            actions.append(
                protector.restore(
                    _text(parts[2], f"{shot_context}.action[{len(actions) + 1}]")
                )
            )
            cursor += 1
        if not 1 <= len(actions) <= 8:
            raise PlannerResponseError(f"{shot_context} requires 1-8 ACTION lines")
        scene_action_count += len(actions)
        auxiliary_visuals: list[AuxiliaryVisual] = []
        while cursor < len(lines) - 1 and lines[cursor].startswith(
            "AUX_VISUAL\t"
        ):
            visual = _auxiliary_visual(
                lines[cursor], shot_context, protector, visual_profile
            )
            signature = (visual.kind, visual.description)
            if signature not in seen_auxiliary_visuals:
                auxiliary_visuals.append(visual)
                seen_auxiliary_visuals.add(signature)
            else:
                LOGGER.warning(
                    "[cl_mv_prompt_planner] %s repeated an identical "
                    "AUX_VISUAL; retained its first occurrence",
                    context,
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
        raw_camera_parts = lines[cursor].split("\t")
        if (
            len(raw_camera_parts) == 6
            and raw_camera_parts[0] == "CAMERA"
            and raw_camera_parts[1] == "CAMERA"
            and raw_camera_parts[2] in CAMERA_TYPES
            and raw_camera_parts[3] in CAMERA_AMPLITUDES
            and raw_camera_parts[4] in CAMERA_SPEEDS
            and raw_camera_parts[5].strip()
        ):
            LOGGER.warning(
                "[cl_mv_prompt_planner] %s repeated the CAMERA field token "
                "before an otherwise unambiguous five-column record; removed "
                "the duplicate token without retrying the Scene",
                shot_context,
            )
            raw_camera_parts.pop(1)
        camera_parts = "\t".join(raw_camera_parts).split("\t", 4)
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
        recovered_after_end = 0
        while cursor < len(lines) - 1 and lines[cursor].startswith(
            "AUX_VISUAL\t"
        ):
            visual = _auxiliary_visual(
                lines[cursor], shot_context, protector, visual_profile
            )
            signature = (visual.kind, visual.description)
            if signature not in seen_auxiliary_visuals:
                auxiliary_visuals.append(visual)
                seen_auxiliary_visuals.add(signature)
            else:
                LOGGER.warning(
                    "[cl_mv_prompt_planner] %s repeated an identical "
                    "AUX_VISUAL; retained its first occurrence",
                    context,
                )
            recovered_after_end += 1
            cursor += 1
        if recovered_after_end:
            LOGGER.warning(
                "[cl_mv_prompt_planner] %s placed %d AUX_VISUAL line(s) "
                "after END_SHOT; attached them to the preceding Shot",
                shot_context,
                recovered_after_end,
            )
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
    if timeline.duration_seconds >= 10 and timeline.lyrics:
        minimum_shots = visual_profile.long_lyric_scene_minimum_shots
        maximum_shots = visual_profile.long_lyric_scene_maximum_shots
        if not minimum_shots <= len(shots) <= maximum_shots:
            raise PlannerResponseError(
                f"{context} profile {visual_profile.profile_id!r} requires "
                f"{minimum_shots}-{maximum_shots} Shots for a lyric Scene of "
                f"{timeline.duration_seconds} seconds; got {len(shots)}"
            )
    elif visual_profile.profile_id in {
        "lyric_visuals_light_8b",
        "lyric_visuals_full",
    } and len(shots) != 1:
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} requires exactly "
            "1 Shot for a short or non-lyric Scene; got "
            f"{len(shots)}"
        )
    camera_sequence = required_camera_sequence(
        scene_id=scene_id,
        profile_id=visual_profile.profile_id,
        shot_count=len(shots),
    )
    for shot, requirement in zip(shots, camera_sequence):
        actual = (shot.camera.type, shot.camera.amplitude, shot.camera.speed)
        expected = (
            requirement["type"],
            requirement["amplitude"],
            requirement["speed"],
        )
        if actual != expected:
            shot_number = requirement["shot_number"]
            raise PlannerResponseError(
                f"{context} Shot {shot_number} camera must use "
                f"type={expected[0]!r}, amplitude={expected[1]!r}, and "
                f"speed={expected[2]!r} from required_camera_sequence; got "
                f"type={actual[0]!r}, amplitude={actual[1]!r}, "
                f"speed={actual[2]!r}"
            )
        if requirement["type"] == "arc":
            normalized_description = unicodedata.normalize(
                "NFKC", shot.camera.description
            )
            has_arc_path = any(
                term in normalized_description for term in _ARC_ONLY_TERMS
            )
            visible_zones = sum(
                any(term in normalized_description for term in zone)
                for zone in _ARC_VIEW_ZONES
            )
            if not has_arc_path or visible_zones < 2:
                shot_number = requirement["shot_number"]
                fallback = fallback_arc_description(
                    scene_id=scene_id,
                    profile_id=visual_profile.profile_id,
                    shot_number=shot_number,
                )
                if fallback is None:
                    raise PlannerResponseError(
                        f"{context} Shot {shot_number} required arc camera must "
                        "describe a physical orbit path and at least two distinct "
                        "view zones among front, side, and rear; a generic approach, "
                        "lateral move, or one-angle view is not an arc"
                    )
                shots[shot_number - 1] = replace(
                    shot,
                    camera=replace(shot.camera, description=fallback),
                )
                LOGGER.warning(
                    "[cl_mv_prompt_planner] %s Shot %d used the scheduled arc "
                    "type but omitted a complete physical route; normalized only "
                    "its CAMERA description to the deterministic profile path",
                    context,
                    shot_number,
                )
    all_auxiliary_visuals = tuple(
        visual for shot in shots for visual in shot.auxiliary_visuals
    )
    visual_count = len(all_auxiliary_visuals)
    minimum = visual_profile.minimum_aux_visuals_per_scene
    maximum = visual_profile.maximum_aux_visuals_per_scene
    required_kind = visual_contract["required_kind"]
    if minimum == maximum == 1 and visual_count > 1:
        emitted_visual_count = visual_count
        matching = [
            (shot_index, visual)
            for shot_index, shot in enumerate(shots)
            for visual in shot.auxiliary_visuals
            if required_kind is None or visual.kind == required_kind
        ]
        if matching:
            selected_shot_index, selected_visual = matching[-1]
            shots = [
                PlannedShot(
                    start_ms=shot.start_ms,
                    composition=shot.composition,
                    subject_actions=shot.subject_actions,
                    environment=shot.environment,
                    camera=shot.camera,
                    auxiliary_visuals=(
                        (selected_visual,)
                        if shot_index == selected_shot_index
                        else ()
                    ),
                )
                for shot_index, shot in enumerate(shots)
            ]
            all_auxiliary_visuals = (selected_visual,)
            visual_count = 1
            LOGGER.warning(
                "[cl_mv_prompt_planner] %s emitted %d AUX_VISUAL lines for an "
                "exact-one contract; retained the final matching line in Shot %d",
                context,
                emitted_visual_count,
                selected_shot_index + 1,
            )
    if not minimum <= visual_count <= maximum:
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} requires "
            f"{minimum}-{maximum} AUX_VISUAL line(s) across the Scene; got {visual_count}"
        )
    if required_kind is not None and (
        visual_count != 1 or all_auxiliary_visuals[0].kind != required_kind
    ):
        actual_kinds = [value.kind for value in all_auxiliary_visuals]
        raise PlannerResponseError(
            f"{context} profile {visual_profile.profile_id!r} requires AUX_VISUAL "
            f"kind {required_kind!r}; got {actual_kinds}"
        )
    has_named_subject_action = any(
        re.search(r"<Subject [1-9][0-9]*>", action)
        for shot in shots
        for action in shot.subject_actions
    )
    if lyric_response_mode == _PENDING_DIRECT_ACTION_MODE:
        lyric_response_mode = (
            "direct_subject_action"
            if has_named_subject_action
            else "direct_object_action"
        )
        LOGGER.warning(
            "[cl_mv_prompt_planner] %s normalized %s LYRIC_RESPONSE value %r "
            "to %r from the parsed ACTION subject binding",
            context,
            pending_mode_reason,
            raw_lyric_response_mode,
            lyric_response_mode,
        )
    if lyric_response_mode == "direct_subject_action" and not has_named_subject_action:
        raise PlannerResponseError(
            f"{context}.lyric_response mode direct_subject_action requires "
            "at least one ACTION naming <Subject N>"
        )
    _reject_internal_timeline_references(
        tuple(shots),
        scene_id=scene_id,
    )
    _reject_screen_text_in_shots(
        tuple(shots),
        scene_id=scene_id,
    )
    return PlannedScene(
        scene_id=scene_id,
        scene_intent=intent,
        shots=tuple(shots),
        lyric_anchor_index=lyric_anchor_index,
        lyric_response_mode=lyric_response_mode,
    )


def _merge_split_scene_fragments(
    fragments: list[list[str]],
    *,
    scene_id: int,
) -> list[str] | None:
    """Reassemble one Scene that a small model closed between Shot blocks.

    A valid recovery has exactly one metadata-bearing first fragment.  Every
    later fragment must contain only one or more complete Shot blocks.  Full
    duplicate records and conflicting metadata remain ambiguous and are not
    merged.
    """

    if len(fragments) < 2:
        return None
    header = f"SCENE\t{scene_id}"
    for fragment in fragments:
        if (
            len(fragment) < 3
            or fragment[0] != header
            or fragment[-1] != "END_SCENE"
        ):
            return None

    first_body = fragments[0][1:-1]
    if (
        sum(line.startswith("SCENE_INTENT\t") for line in first_body) != 1
        or sum(line.startswith("LYRIC_RESPONSE\t") for line in first_body) != 1
    ):
        return None

    merged = [header, *first_body]
    for fragment in fragments[1:]:
        body = fragment[1:-1]
        if (
            not body
            or not body[0].startswith("SHOT\t")
            or any(
                line.startswith(("SCENE_INTENT\t", "LYRIC_RESPONSE\t"))
                for line in body
            )
        ):
            return None
        merged.extend(body)
    merged.append("END_SCENE")
    return merged


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
    fragments_by_scene: dict[int, list[list[str]]] = {}
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
        fragments_by_scene.setdefault(scene_id, []).append(block)

    recovered: dict[int, PlannedScene] = {}
    errors: dict[int, str] = {}
    for scene_id, timeline in pending.items():
        fragments = fragments_by_scene.get(scene_id, [])
        if not fragments:
            errors[scene_id] = f"Scene {scene_id} was omitted from the response"
            continue
        block = fragments[0]
        if len(fragments) > 1:
            merged = _merge_split_scene_fragments(fragments, scene_id=scene_id)
            if merged is None:
                errors[scene_id] = f"Scene {scene_id} appeared more than once"
                continue
            block = merged
            LOGGER.warning(
                "[cl_mv_prompt_planner] Scene %d was closed between Shot "
                "blocks and repeated as %d SCENE records; reassembled the "
                "structural fragments before validation",
                scene_id,
                len(fragments),
            )
        try:
            recovered[scene_id] = _parse_scene_record(
                block, timeline, protector, visual_profile
            )
        except PlannerResponseError as exc:
            errors[scene_id] = str(exc)
    return recovered, errors
