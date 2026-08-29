"""Strict MiniMax H3 Contex-Loop Plan JSON generation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .errors import JSONGenerationError, JSONValidationError, ProtectedTextError
from .protected_text import remove_direct_speech
from .structures import Emd, Scene


LOGGER = logging.getLogger("cl_japanese2json")
SUBJECT_RE = re.compile(r"(?<!\\)<Subject ([1-9][0-9]*)(?<!\\)>")
NON_DIEGETIC_MUSIC = "non_diegetic_music:\nN/A"


def _referenced_subjects(scene: Scene) -> list[int]:
    referenced: set[int] = set()
    for shot in scene.shots:
        try:
            searchable = remove_direct_speech(shot)
        except ProtectedTextError as exc:
            raise JSONGenerationError("Invalid direct-speech tag in scene shot") from exc
        referenced.update(int(match.group(1)) for match in SUBJECT_RE.finditer(searchable))
    return sorted(referenced)


def _subject_block(emd: Emd, scene: Scene, scene_number: int) -> str:
    definitions: list[str] = []
    for number in _referenced_subjects(scene):
        if number > len(emd.subjects):
            LOGGER.warning(
                "[cl_japanese2json] Scene %d references undefined <Subject %d>; definition skipped",
                scene_number,
                number,
            )
            continue
        definitions.append(f"<Subject {number}> is {emd.subjects[number - 1]}")

    block = "subject_definitions:"
    if definitions:
        block += "\n" + "\n".join(definitions)
    return block


def _shot_object(emd: Emd, scene: Scene, index: int) -> dict[str, Any]:
    if not isinstance(scene.duration, int) or isinstance(scene.duration, bool):
        raise JSONGenerationError(f"Scene {index + 1} duration must be an integer")
    if not 1 <= scene.duration <= 60:
        raise JSONGenerationError(f"Scene {index + 1} duration is outside 1-60 seconds")

    prompt = [_subject_block(emd, scene, index + 1)]
    prompt.extend(f"[Shot {shot_index + 1}] {text}" for shot_index, text in enumerate(scene.shots))
    prompt.append(NON_DIEGETIC_MUSIC)

    result: dict[str, Any] = {
        "id": f"scene_{index + 1}",
        "prompt": prompt,
        "duration_seconds": scene.duration,
    }
    if scene.is_continue:
        result["continuation_mode"] = "guide"
    else:
        result["context_length"] = 0
        result["audio_context_length"] = 0
    return result


def generate_json(emd: Emd, *, steps: int = 8) -> str:
    """Generate deterministic JSON and verify that it can be parsed back."""

    if not isinstance(emd, Emd):
        raise JSONGenerationError("JSONGEN requires an Emd value")
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 10000:
        raise JSONGenerationError("steps must be an integer between 1 and 10000")
    if not 1 <= len(emd.scenes) <= 128:
        raise JSONGenerationError(
            f"Scene count must be between 1 and 128; got {len(emd.scenes)}"
        )

    plan = {
        "prompt_prefix": "\n".join(emd.common_prompt),
        "defaults": {"duration_seconds": 5, "steps": steps},
        "shots": [_shot_object(emd, scene, index) for index, scene in enumerate(emd.scenes)],
    }
    try:
        json_text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        json.loads(json_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JSONGenerationError("Generated plan could not be serialized as strict JSON") from exc
    return json_text


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_final_json(json_text: str) -> dict[str, Any]:
    """Validate the Contex-Loop subset returned by the node."""

    if not isinstance(json_text, str):
        raise JSONValidationError("Final JSON output must be a string")
    if not json_text.endswith("\n") or json_text.endswith("\n\n"):
        raise JSONValidationError("Final JSON output must end with exactly one LF")
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise JSONValidationError("Final output is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise JSONValidationError("Final JSON root must be an object")
    if not isinstance(parsed.get("prompt_prefix"), str):
        raise JSONValidationError("prompt_prefix must be a string")

    defaults = parsed.get("defaults")
    if not isinstance(defaults, dict):
        raise JSONValidationError("defaults must be an object")
    if not _is_int(defaults.get("duration_seconds")):
        raise JSONValidationError("defaults.duration_seconds must be an integer")
    if not _is_int(defaults.get("steps")) or not 1 <= defaults["steps"] <= 10000:
        raise JSONValidationError("defaults.steps must be an integer between 1 and 10000")

    shots = parsed.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 128:
        raise JSONValidationError("shots must contain between 1 and 128 entries")
    seen_ids: set[str] = set()
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise JSONValidationError(f"Shot {index} must be an object")
        shot_id = shot.get("id")
        if not isinstance(shot_id, str) or shot_id in seen_ids:
            raise JSONValidationError(f"Shot {index} id must be a unique string")
        seen_ids.add(shot_id)
        prompt = shot.get("prompt")
        if not isinstance(prompt, list) or not all(isinstance(item, str) for item in prompt):
            raise JSONValidationError(f"Shot {index} prompt must be a string array")
        if not prompt or prompt[-1] != NON_DIEGETIC_MUSIC:
            raise JSONValidationError(
                f"Shot {index} prompt must end with the non-diegetic music disable directive"
            )
        duration = shot.get("duration_seconds")
        if not _is_int(duration) or duration <= 0:
            raise JSONValidationError(f"Shot {index} duration_seconds must be a positive integer")

        continuation = shot.get("continuation_mode")
        if continuation is not None:
            if continuation != "guide":
                raise JSONValidationError(f"Shot {index} has an invalid continuation_mode")
            if "context_length" in shot or "audio_context_length" in shot:
                raise JSONValidationError(
                    f"Continuing shot {index} must inherit context length settings"
                )
        elif shot.get("context_length") != 0 or shot.get("audio_context_length") != 0:
            raise JSONValidationError(
                f"Non-continuing shot {index} must reset visual and audio context"
            )
    return parsed
