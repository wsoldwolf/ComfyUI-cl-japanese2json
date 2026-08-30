"""Strict MiniMax H3 Contex-Loop Plan JSON generation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .errors import JSONGenerationError, JSONValidationError, ProtectedTextError
from .protected_text import remove_direct_speech
from .structures import (
    Emd,
    SOUND_NONE,
    VOCALIZATION_EXPLICIT_DIALOGUE_ONLY,
    Scene,
    Soundscape,
)


LOGGER = logging.getLogger("cl_japanese2json")
SUBJECT_RE = re.compile(r"(?<!\\)<Subject ([1-9][0-9]*)(?<!\\)>")
AUDIO_REFERENCE_RE = re.compile(r"(?<!\\)<Audio ([1-9][0-9]*)(?<!\\)>")
DIRECT_SPEECH_RE = re.compile(r"(?<!\\)<d>.*?(?<!\\)</d>", re.DOTALL)
SPEECH_CUE_RE = re.compile(
    r"\b(?:say|says|said|saying|speak|speaks|spoke|spoken|speaking|"
    r"talk|talks|talked|talking|utter|utters|uttered|uttering|"
    r"whisper|whispers|whispered|whispering|shout|shouts|shouted|shouting|"
    r"yell|yells|yelled|yelling|murmur|murmurs|murmured|murmuring|"
    r"groan|groans|groaned|groaning|grumble|grumbles|grumbled|grumbling|"
    r"chant|chants|chanted|chanting|sing|sings|sang|sung|singing|"
    r"announce|announces|announced|announcing|"
    r"exclaim|exclaims|exclaimed|exclaiming|"
    r"reply|replies|replied|replying|respond|responds|responded|responding|"
    r"vocalize|vocalizes|vocalized|vocalizing)\b",
    re.IGNORECASE,
)
NEGATED_SPEECH_PREFIX_RE = re.compile(
    r"(?:\b(?:do|does|did|will|would|should|must|can|could|is|are|was|were)\s+"
    r"(?:not|never)|\b(?:not|never|without|cannot|can't|refrains?\s+from|"
    r"avoids?|no\s+one))\s+(?:[A-Za-z'-]+\s+){0,3}$",
    re.IGNORECASE,
)
OTHER_REFERENCE_RE = re.compile(
    r"(?<!\\)<(?:Picture|Video|Subject) [1-9][0-9]*(?<!\\)>"
)
AUDIO_CLAUSE_SPLIT_RE = re.compile(
    r"\s*(?:,|;|\band\b|\bbut\b|\bwhile\b)\s*", re.IGNORECASE
)
SUBJECT_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
AUDIO_INTRO_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhose\s+(?:voice|audio)\b",
        r"\bwith\b[^,;]*\b(?:voice|audio)\b",
        r"\bvoiced\s+by\s*$",
        r"\b(?:use|uses|used|using)\s*$",
        r"\b(?:an?\s+)?(?:voice|audio)(?:\s+quality)?(?:\s+(?:reference|source))?[^,;]*$",
    )
)
NON_DIEGETIC_MUSIC = "non_diegetic_music:\nN/A"
OVERALL_SOUNDSCAPE_PREFIX = "overall_soundscape:\n"
COMPLETE_SILENCE = OVERALL_SOUNDSCAPE_PREFIX + "Complete silence."
NO_ACTIVE_SUBJECT_BLOCK = (
    "subject_definitions:\n"
    "No character subject or reference-image person is active."
)


def _referenced_subjects(scene: Scene) -> list[int]:
    referenced: set[int] = set()
    for shot in scene.shots:
        try:
            searchable = remove_direct_speech(shot)
        except ProtectedTextError as exc:
            raise JSONGenerationError("Invalid direct-speech tag in scene shot") from exc
        referenced.update(int(match.group(1)) for match in SUBJECT_RE.finditer(searchable))
    return sorted(referenced)


def _scene_requests_speech(scene: Scene) -> bool:
    for shot in scene.shots:
        if DIRECT_SPEECH_RE.search(shot):
            return True
        for match in SPEECH_CUE_RE.finditer(shot):
            prefix = shot[max(0, match.start() - 80):match.start()]
            if not NEGATED_SPEECH_PREFIX_RE.search(prefix):
                return True
    return False


def _scene_has_direct_speech(scene: Scene) -> bool:
    return any(DIRECT_SPEECH_RE.search(shot) is not None for shot in scene.shots)


def _validate_soundscape(soundscape: Soundscape, *, context: str) -> None:
    if not isinstance(soundscape, Soundscape):
        raise JSONGenerationError(f"{context} soundscape must be a Soundscape value")
    for label, value in (
        ("environment", soundscape.environment),
        ("sound effects", soundscape.sound_effects),
        ("vocalization", soundscape.vocalization),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise JSONGenerationError(f"{context} {label} must be a non-empty string")
        if label != "vocalization" and isinstance(value, str):
            if DIRECT_SPEECH_RE.search(value) or AUDIO_REFERENCE_RE.search(value):
                raise JSONGenerationError(
                    f"{context} {label} cannot contain direct speech or an Audio reference"
                )


def _scene_allows_dialogue(scene: Scene, scene_number: int) -> bool:
    mode = scene.soundscape.vocalization
    requests_speech = _scene_requests_speech(scene)
    has_direct_speech = _scene_has_direct_speech(scene)

    if mode == VOCALIZATION_EXPLICIT_DIALOGUE_ONLY:
        if not has_direct_speech:
            raise JSONGenerationError(
                f"Scene {scene_number} enables explicit dialogue but contains no protected direct speech"
            )
        return True
    if mode not in {None, SOUND_NONE}:
        raise JSONGenerationError(
            f"Scene {scene_number} has an invalid vocalization mode"
        )
    if requests_speech:
        raise JSONGenerationError(
            f"Scene {scene_number} contains a speech instruction, but vocalization is not enabled; "
            "add '* 発声: 指定台詞のみ' under '## 音響'"
        )
    return False


def _sentence(value: str) -> str:
    cleaned = value.strip()
    return cleaned if cleaned.endswith((".", "!", "?")) else cleaned + "."


def _overall_soundscape(scene: Scene, allows_dialogue: bool) -> str:
    environment = (
        []
        if scene.soundscape.environment in {None, SOUND_NONE}
        else [scene.soundscape.environment]
    )
    sound_effects = (
        []
        if scene.soundscape.sound_effects in {None, SOUND_NONE}
        else [scene.soundscape.sound_effects]
    )
    parts: list[str] = []
    if environment:
        parts.append("Environment: " + " ".join(_sentence(value) for value in environment))
    if sound_effects:
        parts.append(
            "Sound effects: " + " ".join(_sentence(value) for value in sound_effects)
        )
    if allows_dialogue:
        parts.append(
            "The only character vocalization is the exact shot-synchronized dialogue "
            "explicitly specified in this scene."
        )
    if not parts:
        return COMPLETE_SILENCE
    parts.append("No other sound is present.")
    return OVERALL_SOUNDSCAPE_PREFIX + " ".join(parts)


def _trim_audio_clause(clause: str) -> str:
    audio = AUDIO_REFERENCE_RE.search(clause)
    if audio is None:
        return clause.strip()

    prefix = clause[:audio.start()]
    intro_starts = [
        match.start()
        for pattern in AUDIO_INTRO_PATTERNS
        for match in pattern.finditer(prefix)
    ]
    if intro_starts:
        return prefix[:min(intro_starts)].strip()
    if OTHER_REFERENCE_RE.search(clause):
        return AUDIO_REFERENCE_RE.sub("", clause).strip()
    return prefix.strip()


def _without_audio_references(definition: str) -> str:
    if AUDIO_REFERENCE_RE.search(definition) is None:
        return definition
    retained_sentences: list[str] = []
    for match in SUBJECT_SENTENCE_RE.finditer(definition):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        if AUDIO_REFERENCE_RE.search(sentence) is None:
            retained_sentences.append(sentence)
            continue
        clauses = AUDIO_CLAUSE_SPLIT_RE.split(sentence.rstrip(".!?"))
        retained_clauses = [
            cleaned
            for clause in clauses
            if (cleaned := _trim_audio_clause(clause))
        ]
        if retained_clauses:
            retained_sentences.append(", ".join(retained_clauses) + ".")

    result = " ".join(retained_sentences)
    result = AUDIO_REFERENCE_RE.sub("", result)
    result = re.sub(r"\s+([,.;!?])", r"\1", result)
    result = re.sub(r"(?:,\s*){2,}", ", ", result).strip()
    if not result:
        return "a character."
    return result.rstrip(".!?") + "."


def _subject_block(
    emd: Emd,
    scene: Scene,
    scene_number: int,
    *,
    keep_audio_references: bool,
) -> str:
    referenced_subjects = _referenced_subjects(scene)
    if not referenced_subjects:
        return NO_ACTIVE_SUBJECT_BLOCK

    definitions: list[str] = []
    removed_audio_references = 0
    for number in referenced_subjects:
        if number > len(emd.subjects):
            LOGGER.warning(
                "[cl_japanese2json] Scene %d references undefined <Subject %d>; definition skipped",
                scene_number,
                number,
            )
            continue
        definition = emd.subjects[number - 1]
        if not keep_audio_references:
            removed_audio_references += len(AUDIO_REFERENCE_RE.findall(definition))
            definition = _without_audio_references(definition)
        definitions.append(f"<Subject {number}> is {definition}")

    if removed_audio_references:
        LOGGER.info(
            "[cl_japanese2json] Scene %d has no speech instruction; removed %d Audio reference(s) from subject definitions",
            scene_number,
            removed_audio_references,
        )

    block = "subject_definitions:"
    if definitions:
        block += "\n" + "\n".join(definitions)
    return block


def _shot_object(emd: Emd, scene: Scene, index: int) -> dict[str, Any]:
    if not isinstance(scene.duration, int) or isinstance(scene.duration, bool):
        raise JSONGenerationError(f"Scene {index + 1} duration must be an integer")
    if not 1 <= scene.duration <= 60:
        raise JSONGenerationError(f"Scene {index + 1} duration is outside 1-60 seconds")

    _validate_soundscape(scene.soundscape, context=f"Scene {index + 1}")
    allows_dialogue = _scene_allows_dialogue(scene, index + 1)
    prompt = [
        _subject_block(
            emd,
            scene,
            index + 1,
            keep_audio_references=allows_dialogue,
        )
    ]
    prompt.extend(f"[Shot {shot_index + 1}] {text}" for shot_index, text in enumerate(scene.shots))
    prompt.append(_overall_soundscape(scene, allows_dialogue))
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
        soundscape_items = [
            item for item in prompt if item.startswith(OVERALL_SOUNDSCAPE_PREFIX)
        ]
        if len(soundscape_items) != 1 or len(prompt) < 2 or prompt[-2] != soundscape_items[0]:
            raise JSONValidationError(
                f"Shot {index} prompt must contain exactly one overall soundscape immediately before music"
            )
        if not soundscape_items[0][len(OVERALL_SOUNDSCAPE_PREFIX):].strip():
            raise JSONValidationError(f"Shot {index} overall soundscape must not be empty")
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
