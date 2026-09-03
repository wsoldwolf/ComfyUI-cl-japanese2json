"""Strict MiniMax H3 full-reference Contex-Loop Plan JSON generation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from .errors import JSONGenerationError, JSONValidationError, ProtectedTextError
from .protected_text import remove_direct_speech
from .structures import (
    Emd,
    RETENTION_ATTRIBUTE_TRANSFER,
    RETENTION_FULLY_PRESERVED,
    RETENTION_RELATIONSHIPS,
    SOUND_NONE,
    VOCALIZATION_EXPLICIT_DIALOGUE_ONLY,
    RetentionRule,
    Scene,
    Shot,
    Soundscape,
)


LOGGER = logging.getLogger("cl_japanese2json")

SUBJECT_RE = re.compile(r"(?<!\\)<Subject ([1-9][0-9]*)(?<!\\)>")
AUDIO_REFERENCE_RE = re.compile(r"(?<!\\)<Audio ([1-9][0-9]*)(?<!\\)>")
DIRECT_SPEECH_RE = re.compile(r"(?<!\\)<d>.*?(?<!\\)</d>", re.DOTALL)
SPEAKER_ID_RE = re.compile(r"(?<!\\)\(S([1-9][0-9]*)\)")
SPEAKER_PAIR_RE = re.compile(
    r"(?<!\\)<Subject ([1-9][0-9]*)(?<!\\)>[ \t]*\(S([1-9][0-9]*)\)"
)
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

SUBJECT_DEFINITIONS_PREFIX = "subject_definitions:\n"
SUMMARY_PREFIX = "summary:\n"
RETENTION_ANALYSIS_PREFIX = "retention_analysis:\n"
DETAILED_DESCRIPTION_PREFIX = "detailed_description:\n"
OVERALL_SOUNDSCAPE_PREFIX = "overall_soundscape:\n"
NON_DIEGETIC_MUSIC = "non_diegetic_music:\nN/A"
COMPLETE_SILENCE = OVERALL_SOUNDSCAPE_PREFIX + "Complete silence."
NO_ACTIVE_SUBJECT_BLOCK = (
    SUBJECT_DEFINITIONS_PREFIX
    + "No character subject or reference-image person is active."
)
NO_ACTIVE_RETENTION = (
    RETENTION_ANALYSIS_PREFIX + "No reference labels are active in this scene."
)


def _scene_lines(scene: Scene) -> Iterable[str]:
    yield from scene.preamble
    for shot in scene.shots:
        yield from shot.lines


def _searchable(value: str, *, context: str) -> str:
    try:
        return remove_direct_speech(value)
    except ProtectedTextError as exc:
        raise JSONGenerationError(f"Invalid direct-speech tag in {context}") from exc


def _referenced_subjects(scene: Scene) -> list[int]:
    referenced: set[int] = set()
    for line in _scene_lines(scene):
        searchable = _searchable(line, context="scene description")
        referenced.update(int(match.group(1)) for match in SUBJECT_RE.finditer(searchable))
    return sorted(referenced)


def _shot_subject_locations(scene: Scene, subject_number: int) -> list[int]:
    locations: list[int] = []
    for shot_number, shot in enumerate(scene.shots, start=1):
        if any(
            any(int(match.group(1)) == subject_number for match in SUBJECT_RE.finditer(
                _searchable(line, context=f"Shot {shot_number}")
            ))
            for line in shot.lines
        ):
            locations.append(shot_number)
    return locations


def _scene_requests_speech(scene: Scene) -> bool:
    for line in _scene_lines(scene):
        if DIRECT_SPEECH_RE.search(line):
            return True
        for match in SPEECH_CUE_RE.finditer(line):
            prefix = line[max(0, match.start() - 80):match.start()]
            if not NEGATED_SPEECH_PREFIX_RE.search(prefix):
                return True
    return False


def _scene_has_direct_speech(scene: Scene) -> bool:
    return any(DIRECT_SPEECH_RE.search(line) is not None for line in _scene_lines(scene))


def _validate_scene_structure(scene: Scene, scene_number: int) -> None:
    if not isinstance(scene.duration, int) or isinstance(scene.duration, bool):
        raise JSONGenerationError(f"Scene {scene_number} duration must be an integer")
    if not 1 <= scene.duration <= 60:
        raise JSONGenerationError(
            f"Scene {scene_number} duration is outside 1-60 seconds"
        )
    if not scene.shots:
        raise JSONGenerationError(f"Scene {scene_number} must contain at least one Shot")
    previous_start = -1
    for shot_number, shot in enumerate(scene.shots, start=1):
        if not isinstance(shot, Shot):
            raise JSONGenerationError(
                f"Scene {scene_number} Shot {shot_number} must be a Shot value"
            )
        if not isinstance(shot.start_ms, int) or isinstance(shot.start_ms, bool):
            raise JSONGenerationError(
                f"Scene {scene_number} Shot {shot_number} start_ms must be an integer"
            )
        if shot_number == 1 and shot.start_ms != 0:
            raise JSONGenerationError(
                f"Scene {scene_number} first Shot must start at 0 milliseconds"
            )
        if shot.start_ms <= previous_start or shot.start_ms >= scene.duration * 1000:
            raise JSONGenerationError(
                f"Scene {scene_number} Shot {shot_number} has an invalid start time"
            )
        if not shot.lines or any(not isinstance(line, str) or not line.strip() for line in shot.lines):
            raise JSONGenerationError(
                f"Scene {scene_number} Shot {shot_number} must contain non-empty text"
            )
        previous_start = shot.start_ms
    if any(not isinstance(line, str) or not line.strip() for line in scene.preamble):
        raise JSONGenerationError(
            f"Scene {scene_number} preamble must contain only non-empty strings"
        )
    if any(DIRECT_SPEECH_RE.search(line) for line in scene.preamble):
        raise JSONGenerationError(
            f"Scene {scene_number} direct speech must be written inside a Shot"
        )


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

    for line_number, line in enumerate(_scene_lines(scene), start=1):
        has_line_dialogue = DIRECT_SPEECH_RE.search(line) is not None
        for match in SPEECH_CUE_RE.finditer(line):
            prefix = line[max(0, match.start() - 80):match.start()]
            if not NEGATED_SPEECH_PREFIX_RE.search(prefix) and not has_line_dialogue:
                raise JSONGenerationError(
                    f"Scene {scene_number} line {line_number} contains a speech instruction without protected direct speech"
                )

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


def _speaker_bindings(emd: Emd) -> tuple[dict[int, int], list[dict[int, int]]]:
    subject_to_speaker: dict[int, int] = {}
    speaker_to_subject: dict[int, int] = {}
    scene_bindings: list[dict[int, int]] = []
    next_new_speaker = 1

    for scene_number, scene in enumerate(emd.scenes, start=1):
        current_scene: dict[int, int] = {}
        for shot_number, shot in enumerate(scene.shots, start=1):
            for line_number, line in enumerate(shot.lines, start=1):
                dialogues = list(DIRECT_SPEECH_RE.finditer(line))
                pairs = list(SPEAKER_PAIR_RE.finditer(line))
                speaker_ids = list(SPEAKER_ID_RE.finditer(line))
                for speaker in speaker_ids:
                    # SPEAKER_PAIR_RE captures only the digits, while SPEAKER_ID_RE
                    # spans the complete marker. Compare values and containment.
                    if not any(
                        pair.start() <= speaker.start() and speaker.end() <= pair.end()
                        for pair in pairs
                    ):
                        raise JSONGenerationError(
                            f"Scene {scene_number} Shot {shot_number} line {line_number} has a speaker ID that is not immediately paired with <Subject N>"
                        )
                if not dialogues:
                    if speaker_ids:
                        raise JSONGenerationError(
                            f"Scene {scene_number} Shot {shot_number} line {line_number} uses a speaker ID without direct speech"
                        )
                    continue

                for dialogue in dialogues:
                    preceding = [pair for pair in pairs if pair.end() <= dialogue.start()]
                    if not preceding:
                        raise JSONGenerationError(
                            f"Scene {scene_number} Shot {shot_number} line {line_number} direct speech requires '<Subject N> (Sx)' before the dialogue"
                        )
                    pair = preceding[-1]
                    subject = int(pair.group(1))
                    speaker = int(pair.group(2))
                    existing_speaker = subject_to_speaker.get(subject)
                    if existing_speaker is not None and existing_speaker != speaker:
                        raise JSONGenerationError(
                            f"<Subject {subject}> is assigned to both (S{existing_speaker}) and (S{speaker})"
                        )
                    existing_subject = speaker_to_subject.get(speaker)
                    if existing_subject is not None and existing_subject != subject:
                        raise JSONGenerationError(
                            f"(S{speaker}) is assigned to both <Subject {existing_subject}> and <Subject {subject}>"
                        )
                    if existing_speaker is None:
                        if speaker != next_new_speaker:
                            raise JSONGenerationError(
                                f"New speaker IDs must follow first-vocal-event order; expected (S{next_new_speaker}), got (S{speaker})"
                            )
                        subject_to_speaker[subject] = speaker
                        speaker_to_subject[speaker] = subject
                        next_new_speaker += 1
                    current_scene[subject] = speaker
        scene_bindings.append(current_scene)
    return subject_to_speaker, scene_bindings


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


def _active_audio_bindings(
    emd: Emd,
    scene_number: int,
    active_subjects: list[int],
    scene_speakers: dict[int, int],
    *,
    allows_dialogue: bool,
) -> dict[int, tuple[int, int]]:
    if not allows_dialogue:
        return {}
    bindings: dict[int, tuple[int, int]] = {}
    for subject in active_subjects:
        speaker = scene_speakers.get(subject)
        if speaker is None:
            continue
        definition = emd.subjects[subject - 1]
        for match in AUDIO_REFERENCE_RE.finditer(definition):
            audio = int(match.group(1))
            previous = bindings.get(audio)
            if previous is not None and previous != (subject, speaker):
                raise JSONGenerationError(
                    f"Scene {scene_number} maps <Audio {audio}> to multiple speakers"
                )
            bindings[audio] = (subject, speaker)
    return bindings


def _subject_block(
    emd: Emd,
    scene_number: int,
    active_subjects: list[int],
    active_audio: dict[int, tuple[int, int]],
) -> str:
    if not active_subjects:
        return NO_ACTIVE_SUBJECT_BLOCK

    definitions: list[str] = []
    for number in active_subjects:
        if number > len(emd.subjects):
            raise JSONGenerationError(
                f"Scene {scene_number} references undefined <Subject {number}>"
            )
        definition = _without_audio_references(emd.subjects[number - 1])
        definitions.append(f"<Subject {number}> is {definition}")
    for audio, (subject, speaker) in sorted(active_audio.items()):
        definitions.append(
            f"<Audio {audio}> is the voice-timbre reference for "
            f"<Subject {subject}> (S{speaker})."
        )
    return SUBJECT_DEFINITIONS_PREFIX + "\n".join(definitions)


def _summary_block(
    scene: Scene,
    active_subjects: list[int],
    active_audio: dict[int, tuple[int, int]],
) -> str:
    task_types = ["reference generation"]
    if active_audio:
        task_types.append("audio reference")
    prefix = "[" + " + ".join(task_types) + "]"
    if active_subjects:
        labels = [f"<Subject {number}>" for number in active_subjects]
        if len(labels) == 1:
            subject_text = labels[0]
        elif len(labels) == 2:
            subject_text = f"{labels[0]} and {labels[1]}"
        else:
            subject_text = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        body = (
            f"The target video uses {subject_text} in a "
            f"{len(scene.shots)}-shot scene."
        )
    else:
        body = (
            "The target video is an effects-only scene with no active character "
            "subject or reference-image person."
        )
    if scene.is_continue:
        body += " The scene continues the preceding generated scene."
    if active_audio:
        audio_labels = ", ".join(f"<Audio {number}>" for number in sorted(active_audio))
        body += (
            f" {audio_labels} is referenced only for the explicitly specified dialogue."
            if len(active_audio) == 1
            else f" {audio_labels} are referenced only for the explicitly specified dialogue."
        )
    return SUMMARY_PREFIX + prefix + " " + body


def _shot_list_text(numbers: list[int]) -> str:
    return ", ".join(f"[Shot {number}]" for number in numbers)


def _retention_block(
    emd: Emd,
    scene: Scene,
    active_subjects: list[int],
    active_audio: dict[int, tuple[int, int]],
) -> str:
    if not active_subjects and not active_audio:
        return NO_ACTIVE_RETENTION

    rules = {rule.subject_number: rule for rule in emd.retention_rules}
    lines: list[str] = []
    active_set = set(active_subjects)
    for subject in active_subjects:
        rule = rules.get(subject)
        if rule is None:
            rule = RetentionRule(
                subject,
                RETENTION_FULLY_PRESERVED,
                "the defined identity and visual characteristics are preserved.",
            )
        if rule.relationship not in RETENTION_RELATIONSHIPS:
            raise JSONGenerationError(
                f"<Subject {subject}> has an invalid retention relationship"
            )
        locations = _shot_subject_locations(scene, subject)
        if rule.relationship == RETENTION_ATTRIBUTE_TRANSFER:
            target = rule.target_subject_number
            if target is None or target not in active_set:
                raise JSONGenerationError(
                    f"Active attribute-transfer source <Subject {subject}> requires its target Subject to be active in the same scene"
                )
            target_locations = _shot_subject_locations(scene, target)
            applied_locations = sorted(set(locations) | set(target_locations))
            where = (
                f"applied to <Subject {target}> in {_shot_list_text(applied_locations)}"
                if applied_locations
                else f"applied to <Subject {target}> throughout the scene"
            )
        else:
            where = (
                f"used in {_shot_list_text(locations)}"
                if locations
                else "applies throughout the scene"
            )
        lines.append(
            f"<Subject {subject}> ({where}): {rule.relationship} - "
            f"{_sentence(rule.description)}"
        )

    for audio, (subject, _speaker) in sorted(active_audio.items()):
        lines.append(
            f"<Audio {audio}>: reference - only the voice timbre and delivery are "
            f"referenced for <Subject {subject}>; the source signal and "
            "its original speech are not copied."
        )
    return RETENTION_ANALYSIS_PREFIX + "\n".join(lines)


def _format_timestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _shot_dialogue_subjects(shot: Shot) -> set[int]:
    subjects: set[int] = set()
    for line in shot.lines:
        pairs = list(SPEAKER_PAIR_RE.finditer(line))
        for dialogue in DIRECT_SPEECH_RE.finditer(line):
            preceding = [pair for pair in pairs if pair.end() <= dialogue.start()]
            if preceding:
                subjects.add(int(preceding[-1].group(1)))
    return subjects


def _detailed_description_block(
    scene: Scene,
    active_audio: dict[int, tuple[int, int]],
) -> str:
    parts = [_sentence(line) for line in scene.preamble]
    audio_by_subject: dict[int, list[tuple[int, int]]] = {}
    for audio, (subject, speaker) in active_audio.items():
        audio_by_subject.setdefault(subject, []).append((audio, speaker))

    for shot_number, shot in enumerate(scene.shots, start=1):
        body = " ".join(_sentence(line) for line in shot.lines)
        present_audio = {
            int(match.group(1)) for match in AUDIO_REFERENCE_RE.finditer(body)
        }
        for subject in sorted(_shot_dialogue_subjects(shot)):
            for audio, speaker in sorted(audio_by_subject.get(subject, [])):
                if audio not in present_audio:
                    body += (
                        f" For <Subject {subject}> (S{speaker})'s explicitly specified "
                        f"dialogue in this shot, use <Audio {audio}> only as a voice-timbre "
                        "and delivery reference; do not copy or introduce any other speech "
                        "from the source audio."
                    )
        if shot_number == 1:
            parts.append(f"[Shot 1] {body}")
        else:
            parts.append(
                f"[Shot {shot_number}] At {_format_timestamp(shot.start_ms)}, {body}"
            )
    return DETAILED_DESCRIPTION_PREFIX + "\n".join(parts)


def _validate_retention_rules(emd: Emd) -> None:
    seen: set[int] = set()
    for rule in emd.retention_rules:
        if not isinstance(rule, RetentionRule):
            raise JSONGenerationError("retention_rules must contain RetentionRule values")
        if rule.subject_number in seen:
            raise JSONGenerationError(
                f"Duplicate retention rule for <Subject {rule.subject_number}>"
            )
        seen.add(rule.subject_number)
        if not 1 <= rule.subject_number <= len(emd.subjects):
            raise JSONGenerationError(
                f"Retention rule references undefined <Subject {rule.subject_number}>"
            )
        if rule.relationship not in RETENTION_RELATIONSHIPS:
            raise JSONGenerationError(
                f"Retention rule for <Subject {rule.subject_number}> has an invalid relationship"
            )
        if not isinstance(rule.description, str) or not rule.description.strip():
            raise JSONGenerationError("Retention descriptions must be non-empty strings")
        target = rule.target_subject_number
        if rule.relationship == RETENTION_ATTRIBUTE_TRANSFER:
            if target is None or target == rule.subject_number:
                raise JSONGenerationError(
                    "attribute_transfer requires a different target Subject"
                )
            if not 1 <= target <= len(emd.subjects):
                raise JSONGenerationError(
                    f"Retention rule references undefined target <Subject {target}>"
                )
        elif target is not None:
            raise JSONGenerationError(
                "Only attribute_transfer accepts a target Subject"
            )


def _shot_object(
    emd: Emd,
    scene: Scene,
    index: int,
    scene_speakers: dict[int, int],
) -> dict[str, Any]:
    scene_number = index + 1
    _validate_scene_structure(scene, scene_number)
    _validate_soundscape(scene.soundscape, context=f"Scene {scene_number}")
    allows_dialogue = _scene_allows_dialogue(scene, scene_number)
    active_subjects = _referenced_subjects(scene)
    for subject in active_subjects:
        if subject > len(emd.subjects):
            raise JSONGenerationError(
                f"Scene {scene_number} references undefined <Subject {subject}>"
            )
    active_audio = _active_audio_bindings(
        emd,
        scene_number,
        active_subjects,
        scene_speakers,
        allows_dialogue=allows_dialogue,
    )
    detailed = _detailed_description_block(scene, active_audio)
    detailed_audio = {
        int(match.group(1)) for match in AUDIO_REFERENCE_RE.finditer(detailed)
    }
    unexpected_audio = detailed_audio - set(active_audio)
    if unexpected_audio:
        labels = ", ".join(f"<Audio {number}>" for number in sorted(unexpected_audio))
        raise JSONGenerationError(
            f"Scene {scene_number} uses Audio reference(s) without an active Subject voice binding: {labels}"
        )

    prompt = [
        _subject_block(emd, scene_number, active_subjects, active_audio),
        _summary_block(scene, active_subjects, active_audio),
        _retention_block(emd, scene, active_subjects, active_audio),
        detailed,
        _overall_soundscape(scene, allows_dialogue),
        NON_DIEGETIC_MUSIC,
    ]

    result: dict[str, Any] = {
        "id": f"scene_{scene_number}",
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
    _validate_retention_rules(emd)
    _, scene_speakers = _speaker_bindings(emd)
    plan = {
        "prompt_prefix": "",
        "defaults": {"duration_seconds": 5, "steps": steps},
        "shots": [
            _shot_object(emd, scene, index, scene_speakers[index])
            for index, scene in enumerate(emd.scenes)
        ],
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
    """Validate the full-reference Contex-Loop subset returned by the node."""

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
    if parsed.get("prompt_prefix") != "":
        raise JSONValidationError("prompt_prefix must be an empty string")

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
    expected_prefixes = (
        SUBJECT_DEFINITIONS_PREFIX,
        SUMMARY_PREFIX,
        RETENTION_ANALYSIS_PREFIX,
        DETAILED_DESCRIPTION_PREFIX,
        OVERALL_SOUNDSCAPE_PREFIX,
    )
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
        if len(prompt) != 6:
            raise JSONValidationError(
                f"Shot {index} prompt must contain exactly six full-reference sections"
            )
        for position, prefix in enumerate(expected_prefixes):
            if not prompt[position].startswith(prefix):
                raise JSONValidationError(
                    f"Shot {index} prompt section {position + 1} has the wrong type or order"
                )
            if not prompt[position][len(prefix):].strip():
                raise JSONValidationError(
                    f"Shot {index} prompt section {position + 1} must not be empty"
                )
        if prompt[-1] != NON_DIEGETIC_MUSIC:
            raise JSONValidationError(
                f"Shot {index} prompt must end with the non-diegetic music disable directive"
            )
        if not prompt[1][len(SUMMARY_PREFIX):].startswith("["):
            raise JSONValidationError(f"Shot {index} summary must begin with a task type")
        detailed = prompt[3][len(DETAILED_DESCRIPTION_PREFIX):]
        shot_numbers = [int(value) for value in re.findall(r"\[Shot ([1-9][0-9]*)\]", detailed)]
        if not shot_numbers or shot_numbers != list(range(1, len(shot_numbers) + 1)):
            raise JSONValidationError(
                f"Shot {index} detailed description must contain sequential Shot labels"
            )
        duration = shot.get("duration_seconds")
        if not _is_int(duration) or not 1 <= duration <= 60:
            raise JSONValidationError(
                f"Shot {index} duration_seconds must be an integer between 1 and 60"
            )

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
