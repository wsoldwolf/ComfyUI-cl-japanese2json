"""Deterministic camera variety contracts for MV Scene planning.

The policy fixes only camera grammar and broad spatial geometry.  The LLM
still decides how that geometry serves the current action, lyric, world, and
foreground/background elements.
"""

from __future__ import annotations

from typing import Any


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
