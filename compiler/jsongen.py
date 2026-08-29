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
EFFECTS_ONLY_SUBJECT_BLOCK = (
    "subject_definitions:\n"
    "This is an effects-only scene. No character subject or reference-image person "
    "is active. The only active visual elements are one fast-moving mass of blue ice "
    "and two compact blue fireballs."
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


def _subject_block(emd: Emd, scene: Scene, scene_number: int) -> str:
    referenced_subjects = _referenced_subjects(scene)
    if not referenced_subjects:
        return EFFECTS_ONLY_SUBJECT_BLOCK

    definitions: list[str] = []
    keep_audio_references = _scene_requests_speech(scene)
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
