"""Deterministic camera variety contracts for MV Scene planning.

The policy fixes only camera grammar and broad spatial geometry.  The LLM
still decides how that geometry serves the current action, lyric, world, and
foreground/background elements.
"""

from __future__ import annotations

from typing import Any
import re

from .performance_policy import action_signature


_SCHEDULED_PROFILE_IDS = frozenset(
    {"lyric_visuals_light_8b", "lyric_visuals_full"}
)


# Each family contains three different translating camera moves.  Compact
# two-Shot profiles consume the first two entries.  Alternating families put a
# broad arc in half of those Scenes instead of turning every Scene into the
# same orbit.
_CAMERA_SEQUENCE_FAMILIES: tuple[tuple[dict[str, str], ...], ...] = (
    (
        {
            "type": "arc",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "travel on a broad roughly half-circle path from a front-left "
                "three-quarter view, past the Subject's side, to a rear-right "
                "three-quarter view; keep identity stable and show strong "
                "foreground/background parallax"
            ),
        },
        {
            "type": "pull",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "retreat from an oblique detail view to a wide view that reveals "
                "the complete action, foreground anchor, and distant world"
            ),
        },
        {
            "type": "pedestal",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "rise from a low oblique view to a high oblique view while the "
                "action and its visible result remain unobscured"
            ),
        },
    ),
    (
        {
            "type": "push",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "advance through a near foreground layer from a distant diagonal "
                "view to a close oblique view of the critical action"
            ),
        },
        {
            "type": "truck",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "translate laterally across foreground depth anchors from one "
                "diagonal side view to the opposite diagonal side view"
            ),
        },
        {
            "type": "pull",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "withdraw through depth from the result to a wide environmental "
                "reveal without losing the Subject or changed target"
            ),
        },
    ),
    (
        {
            "type": "pull",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "begin close beside the active body part and retreat diagonally "
                "until the Subject, target, foreground, and background are visible"
            ),
        },
        {
            "type": "arc",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "travel on a broad roughly half-circle path from a rear-left "
                "three-quarter view, past the Subject's side, to a front-right "
                "three-quarter view; preserve the same Subject and reveal the "
                "contact from multiple sides"
            ),
        },
        {
            "type": "truck",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "cross laterally behind a foreground occluder and emerge at a "
                "different diagonal view of the completed action"
            ),
        },
    ),
    (
        {
            "type": "truck",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "sweep laterally from a low diagonal view across foreground "
                "layers to an opposite-side diagonal view"
            ),
        },
        {
            "type": "push",
            "amplitude": "medium",
            "speed": "moderate",
            "spatial_goal": (
                "advance obliquely through depth from the full action toward its "
                "contact point while keeping the target recognizable"
            ),
        },
        {
            "type": "pedestal",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "drop from a high oblique overview to a low oblique result view "
                "with strong vertical parallax"
            ),
        },
    ),
    (
        {
            "type": "arc",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "travel on a rising broad arc from a low front-right three-quarter "
                "view, across the Subject's side, to a high rear-left "
                "three-quarter view; maintain identity and expose spatial depth"
            ),
        },
        {
            "type": "pedestal",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "descend from a high diagonal view through foreground depth to a "
                "low diagonal view of the action's physical result"
            ),
        },
        {
            "type": "push",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "drive forward from an environmental wide view to a different "
                "oblique close view at the moment of release or recovery"
            ),
        },
    ),
    (
        {
            "type": "pedestal",
            "amplitude": "large",
            "speed": "fast",
            "spatial_goal": (
                "rise from below the foreground action plane to an elevated "
                "diagonal view that exposes the surrounding depth"
            ),
        },
        {
            "type": "truck",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "translate laterally through distinct near, middle, and far depth "
                "layers to finish on the opposite diagonal side"
            ),
        },
        {
            "type": "arc",
            "amplitude": "large",
            "speed": "moderate",
            "spatial_goal": (
                "travel on a descending broad arc from a rear-right three-quarter "
                "view, past the Subject's side, to a front-left three-quarter view "
                "while the completed result stays visible"
            ),
        },
    ),
)


def required_camera_sequence(
    *,
    scene_id: int,
    profile_id: str,
    shot_count: int,
) -> list[dict[str, Any]]:
    """Return the binding sequence for one profile-scheduled Scene.

    The two lyric-visual profiles use this policy for every Scene so an intro,
    instrumental passage, or short vocal phrase cannot silently fall back to
    a frontal static tableau.  ``performance_only`` remains model-directed.
    """

    if (
        profile_id not in _SCHEDULED_PROFILE_IDS
        or shot_count <= 0
    ):
        return []
    family = _CAMERA_SEQUENCE_FAMILIES[(scene_id - 1) % len(
        _CAMERA_SEQUENCE_FAMILIES
    )]
    if shot_count > len(family):
        return []
    return [
        {
            "shot_number": index,
            **dict(requirement),
        }
        for index, requirement in enumerate(family[:shot_count], start=1)
    ]


def fallback_arc_description(
    *,
    scene_id: int,
    profile_id: str,
    shot_number: int,
) -> str | None:
    """Return a deterministic Japanese realization for a scheduled arc.

    This is a parser-side recovery path, not a creative planning template.
    It is used only when the model copied the required arc type/amplitude/speed
    but described its physical route too vaguely for H3 or the validator.
    Keeping it in the camera policy makes the fallback profile-independent of
    any particular lyric, subject design, or environment.
    """

    if profile_id not in _SCHEDULED_PROFILE_IDS or shot_number <= 0:
        return None
    family_index = (scene_id - 1) % len(_CAMERA_SEQUENCE_FAMILIES)
    shot_index = shot_number - 1
    family = _CAMERA_SEQUENCE_FAMILIES[family_index]
    if shot_index >= len(family) or family[shot_index]["type"] != "arc":
        return None

    descriptions = {
        (0, 0): (
            "主要被写体の左前方斜めから開始し、側面を通る広い半円軌道で"
            "右後方斜めまで回り込み、近い前景と遠い背景の視差を大きく変える。"
        ),
        (2, 1): (
            "主要被写体の左後方斜めから開始し、側面を通る広い半円軌道で"
            "右前方斜めまで回り込み、接触点と前景・背景の視差を保つ。"
        ),
        (4, 0): (
            "主要被写体の低い右前方斜めから開始し、側面を通って上昇する広い"
            "半円軌道で高い左後方斜めまで回り込み、前景と背景の奥行きを見せる。"
        ),
        (5, 2): (
            "主要被写体の高い右後方斜めから開始し、側面を通って下降する広い"
            "半円軌道で低い左前方斜めまで回り込み、動作結果と前景・背景の視差を保つ。"
        ),
    }
    return descriptions.get((family_index, shot_index))


def repair_scheduled_camera_description(
    description: str,
    *,
    requirement: dict[str, Any],
    actions: tuple[str, ...],
) -> str:
    """Repair only scheduled non-arc geometry; keep authored usable routes."""

    kind = requirement["type"]
    route_terms = {
        "push": r"接近|近づ|前進|進み|進む",
        "pull": r"後退|遠ざ|離れ|引いて|退く",
        "truck": r"横移動|横方向|水平|横切|平行移動|左右|スイープ",
        "pedestal": r"上昇|下降|高さ|垂直|上へ|下へ",
    }
    if kind not in route_terms:
        return description
    action_phrases = {
        sentence.strip().removeprefix("続いて").rstrip("。.!！")
        for action in actions
        for sentence in re.split(r"(?<=。)", action)
        if sentence.strip()
    }
    action_keys = {action_signature(value) for value in action_phrases}
    kept: list[str] = []
    for sentence in re.split(r"(?<=。)", description):
        sentence = sentence.strip()
        if not sentence:
            continue
        # A camera prefix can be followed by an exact copied ACTION clause.
        # Remove that suffix, retaining any independently useful camera path.
        for phrase in sorted(action_phrases, key=len, reverse=True):
            body = sentence.rstrip("。.!！")
            if phrase and body.endswith(phrase):
                sentence = body[:-len(phrase)].rstrip("、, ")
                if sentence:
                    sentence += "。"
        if not sentence:
            continue
        copied_action = action_signature(sentence) in action_keys
        performer_command = (
            re.match(r"<Subject [1-9][0-9]*>(?:は|が|の)", sentence)
            and re.search(r"(?:手|腕|足|膝|身体|胴体).*(?:上げ|下げ|添え|踏|伸ば|曲げ|ひね|開い)", sentence)
            and not re.search(r"追[う跡従]|捉え|映[すし]|見せ|焦点|構図|カメラ|視点", sentence)
        )
        if not copied_action and not performer_command:
            kept.append(sentence)
    cleaned = "".join(kept)
    if (
        cleaned
        and re.search(route_terms[kind], cleaned)
        and re.search(r"前景|背景|視差|奥行き", cleaned)
    ):
        return cleaned
    fallback = {
        "push": "斜め遠景から前景の奥行きを通って主要被写体へ接近し、動作と接触対象が見える斜め近景で終える。前景と背景の視差を保つ。",
        "pull": "主要被写体の斜め近景から後方へ移動し、前景、全身の動作及び背景を一緒に見渡せる斜め広角構図で終える。奥行きに沿う視差を示す。",
        "truck": "主要被写体の左斜め側方から前景を横切って右斜め側方へ水平移動し、動作を捉えながら近い前景と遠い背景の視差を変える。",
        "pedestal": (
            "高い斜め視点から前景に沿って垂直に下降し、主要被写体の動作と結果を捉える低い斜め視点で終える。前景と背景の上下方向の視差を示す。"
            if re.search(r"descend|drop", requirement["spatial_goal"])
            else "低い斜め視点から前景に沿って垂直に上昇し、主要被写体の動作と周囲を捉える高い斜め視点で終える。前景と背景の上下方向の視差を示す。"
        ),
    }
    return fallback[kind]
