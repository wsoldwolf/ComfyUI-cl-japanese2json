"""Bounded ACTION-only regeneration with immutable scene and lyric structure."""

from dataclasses import replace

from .errors import PlannerResponseError
from .performance_policy import action_signature, deduplicate_actions
from .validation import (
    _lines, _text, _reject_screen_text_creative_cues,
    camera_guard_issues, performance_safety_issues, subject_motion_issues,
    vocal_guard_issues,
)


def parse_motion_repair(content, scene, timeline, protector, blueprint=None):
    lines = _lines(content, "Motion repair")
    if lines[0] != f"MOTION_REPAIR\t{scene.scene_id}" or lines[-1] != "END_MOTION_REPAIR":
        raise PlannerResponseError("Motion repair requires matching scene id and END_MOTION_REPAIR")
    cursor = 1
    shots = []
    for shot in scene.shots:
        if cursor >= len(lines) or lines[cursor] != f"SHOT\t{shot.start_ms}":
            raise PlannerResponseError("Motion repair must preserve every SHOT start_ms and order")
        cursor += 1
        actions = []
        while cursor < len(lines) and lines[cursor].startswith("ACTION\t"):
            parts = lines[cursor].split("\t")
            if len(parts) != 3 or parts[1] != str(len(actions) + 1):
                raise PlannerResponseError("Motion repair ACTION indices must be consecutive from 1")
            value = protector.restore(_text(parts[2], "Motion repair ACTION"))
            _reject_screen_text_creative_cues(value, "Motion repair ACTION")
            actions.append(value)
            cursor += 1
        if not 1 <= len(actions) <= 8 or cursor >= len(lines) or lines[cursor] != "END_SHOT":
            raise PlannerResponseError("Motion repair requires 1-8 ACTIONs and END_SHOT per Shot")
        cursor += 1
        shots.append(replace(shot, subject_actions=deduplicate_actions(tuple(actions))))
    if cursor != len(lines) - 1:
        raise PlannerResponseError("Motion repair added fields or Shots")
    repaired = replace(scene, shots=tuple(shots))
    if blueprint is not None:
        # Existing locked phases retain their Shot, order, target and operation.
        locked = {action_signature(value) for value in blueprint.subject_actions}
        for original, updated in zip(scene.shots, repaired.shots):
            required = [action_signature(a) for a in original.subject_actions if action_signature(a) in locked]
            keys = [action_signature(a) for a in updated.subject_actions]
            position = 0
            for key in required:
                try:
                    position = keys.index(key, position) + 1
                except ValueError as exc:
                    raise PlannerResponseError("Motion repair lost or reordered a locked lyric ACTION") from exc
        if action_signature(repaired.shots[-1].subject_actions[-1]) != action_signature(blueprint.visible_result):
            raise PlannerResponseError("Motion repair must finish with the locked VISIBLE_RESULT")
    issues = (
        subject_motion_issues(repaired, duration_seconds=timeline.duration_seconds)
        + performance_safety_issues(repaired)
        + [i for i in camera_guard_issues(repaired) if i["code"] == "camera_motion_in_action"]
        + vocal_guard_issues(repaired, timeline)
    )
    if issues:
        raise PlannerResponseError("; ".join(str(i["message"]) for i in issues))
    return repaired
