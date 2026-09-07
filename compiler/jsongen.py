"""Strict MiniMax H3 full-reference Contex-Loop Plan JSON generation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Iterable

from .errors import JSONGenerationError, JSONValidationError, ProtectedTextError
from .protected_text import remove_direct_speech
from .structures import (
    AUDIO_COPY_RELATIONSHIPS,
    AUDIO_FULLY_COPY,
    BackgroundMusicReuse,
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
ANY_AUDIO_REFERENCE_RE = re.compile(r"(?<!\\)<Audio\s*[0-9]+\s*(?<!\\)>")
DIRECT_SPEECH_RE = re.compile(r"(?<!\\)<d>.*?(?<!\\)</d>", re.DOTALL)
LIP_SYNC_LINE_RE = re.compile(
    r"^Lip sync: <Subject ([1-4])> <- <Audio ([1-3])>: "
    r"(<d>(?:(?!<d>|</d>).)+</d>)$"
)
SPEAKER_ID_RE = re.compile(r"(?<!\\)\(S([1-9][0-9]*)\)")
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
NON_DIEGETIC_MUSIC_PREFIX = "non_diegetic_music:\n"
NON_DIEGETIC_MUSIC = "non_diegetic_music:\nN/A"
COMPLETE_SILENCE = OVERALL_SOUNDSCAPE_PREFIX + "Complete silence."
NO_DIEGETIC_SOUND = (
    OVERALL_SOUNDSCAPE_PREFIX
    + "No ambience, physical sound, or character vocalization is present."
)
NO_SEPARATELY_GENERATED_DIEGETIC_SOUND = (
    OVERALL_SOUNDSCAPE_PREFIX
    + "No separately generated ambience, physical sound, or character "
    "vocalization is added."
)
NO_ACTIVE_SUBJECT_BLOCK = (
    SUBJECT_DEFINITIONS_PREFIX
    + "No character subject or reference-image person is active."
)
NO_ACTIVE_RETENTION = (
    RETENTION_ANALYSIS_PREFIX + "No reference labels are active in this scene."
)


@dataclass
class ReusedAudioBinding:
    subject: int
    speaker: int
    shot_numbers: list[int] = field(default_factory=list)


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


def _common_lines_for_scene(
    emd: Emd,
    active_subjects: list[int],
    active_audio: set[int],
) -> list[str]:
    active_subject_set = set(active_subjects)
    selected: list[str] = []
    for line in emd.common_prompt:
        searchable = _searchable(line, context="common prompt")
        referenced_subjects = {
            int(match.group(1)) for match in SUBJECT_RE.finditer(searchable)
        }
        referenced_audio = {
            int(match.group(1)) for match in AUDIO_REFERENCE_RE.finditer(searchable)
        }
        if (
            referenced_subjects.issubset(active_subject_set)
            and referenced_audio.issubset(active_audio)
        ):
            selected.append(line)
    return selected


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


def _validate_soundscape(
    soundscape: Soundscape,
    *,
    context: str,
    duration_seconds: int,
) -> None:
    if not isinstance(soundscape, Soundscape):
        raise JSONGenerationError(f"{context} soundscape must be a Soundscape value")
    for label, value in (
        ("environment", soundscape.environment),
        ("sound effects", soundscape.sound_effects),
        ("vocalization", soundscape.vocalization),
        ("background music", soundscape.background_music),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise JSONGenerationError(f"{context} {label} must be a non-empty string")
        if label != "vocalization" and isinstance(value, str):
            if DIRECT_SPEECH_RE.search(value) or AUDIO_REFERENCE_RE.search(value):
                raise JSONGenerationError(
                    f"{context} {label} cannot contain direct speech or an Audio reference"
                )
    music_reuse = soundscape.background_music_reuse
    if music_reuse is not None:
        if not isinstance(music_reuse, BackgroundMusicReuse):
            raise JSONGenerationError(
                f"{context} background music reuse must be a BackgroundMusicReuse value"
            )
        if (
            not isinstance(music_reuse.audio_number, int)
            or isinstance(music_reuse.audio_number, bool)
            or not 1 <= music_reuse.audio_number <= 3
        ):
            raise JSONGenerationError(
                f"{context} background music reuse Audio must be in the Audio 1-3 range"
            )
        if (
            not isinstance(music_reuse.relationship, str)
            or music_reuse.relationship not in AUDIO_COPY_RELATIONSHIPS
        ):
            raise JSONGenerationError(
                f"{context} has an invalid background music reuse relationship"
            )
        if soundscape.background_music is not None:
            raise JSONGenerationError(
                f"{context} cannot combine generated background music with background music reuse"
            )
        source_start = music_reuse.source_start_ms
        source_end = music_reuse.source_end_ms
        if (source_start is None) != (source_end is None):
            raise JSONGenerationError(
                f"{context} background music source range must contain both a start and an end"
            )
        if source_start is not None:
            if music_reuse.relationship != "partially_copy":
                raise JSONGenerationError(
                    f"{context} background music source range is allowed only with partially_copy"
                )
            if (
                not isinstance(source_start, int)
                or isinstance(source_start, bool)
                or not isinstance(source_end, int)
                or isinstance(source_end, bool)
                or source_start < 0
                or source_end <= source_start
            ):
                raise JSONGenerationError(
                    f"{context} background music source range must be increasing non-negative integer milliseconds"
                )
            if source_end - source_start != duration_seconds * 1000:
                raise JSONGenerationError(
                    f"{context} background music source range must exactly match the "
                    f"{duration_seconds}-second Scene duration"
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


def _line_dialogue_subject_matches(
    line: str,
    *,
    context: str,
) -> list[tuple[re.Match[str], re.Match[str]]]:
    dialogues = list(DIRECT_SPEECH_RE.finditer(line))
    subjects = list(SUBJECT_RE.finditer(line))
    result: list[tuple[re.Match[str], re.Match[str]]] = []
    for dialogue in dialogues:
        preceding = [subject for subject in subjects if subject.end() <= dialogue.start()]
        if not preceding:
            raise JSONGenerationError(
                f"{context} direct speech requires a preceding <Subject N> on the same line"
            )
        result.append((dialogue, preceding[-1]))
    return result


def _scene_speaker_bindings(emd: Emd) -> list[dict[int, int]]:
    scene_bindings: list[dict[int, int]] = []

    for scene_number, scene in enumerate(emd.scenes, start=1):
        current_scene: dict[int, int] = {}
        for shot_number, shot in enumerate(scene.shots, start=1):
            for line_number, line in enumerate(shot.lines, start=1):
                if SPEAKER_ID_RE.search(line):
                    raise JSONGenerationError(
                        f"Scene {scene_number} Shot {shot_number} line {line_number} contains a user-supplied speaker ID; speaker IDs are generated internally"
                    )
                pairs = _line_dialogue_subject_matches(
                    line,
                    context=(
                        f"Scene {scene_number} Shot {shot_number} line {line_number}"
                    ),
                )
                for _dialogue, subject_match in pairs:
                    subject = int(subject_match.group(1))
                    current_scene[subject] = subject
        scene_bindings.append(current_scene)
    return scene_bindings


def _sentence(value: str) -> str:
    cleaned = value.strip()
    return cleaned if cleaned.endswith((".", "!", "?")) else cleaned + "."


def _overall_soundscape(
    scene: Scene,
    allows_dialogue: bool,
    *,
    has_background_music: bool,
    reuses_background_music: bool,
    has_generated_dialogue: bool,
    has_bgm_lip_sync: bool,
) -> str:
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
        if has_bgm_lip_sync and not has_generated_dialogue:
            parts.append(
                "The only synchronized character vocalization is the exact vocal "
                "performance already contained in the directly reused audience-only "
                "music track; no new voice is generated."
            )
        else:
            parts.append(
                "The only character vocalization is the exact shot-synchronized dialogue "
                "explicitly specified in this scene."
            )
            if has_bgm_lip_sync:
                parts.append(
                    "The BGM-linked vocal is reused from the music track rather than "
                    "generated again."
                )
    if not parts:
        if reuses_background_music:
            return NO_SEPARATELY_GENERATED_DIEGETIC_SOUND
        return NO_DIEGETIC_SOUND if has_background_music else COMPLETE_SILENCE
    if reuses_background_music:
        parts.append(
            "No other separately generated ambience, physical sound, or character "
            "vocalization is added."
        )
    else:
        parts.append(
            "No other ambience, physical sound, or character vocalization is present."
        )
    return OVERALL_SOUNDSCAPE_PREFIX + " ".join(parts)


def _non_diegetic_music(
    scene: Scene,
    reused_audio: dict[int, ReusedAudioBinding],
) -> str:
    music_reuse = scene.soundscape.background_music_reuse
    if music_reuse is not None:
        audio = music_reuse.audio_number
        binding = reused_audio.get(audio)
        if music_reuse.relationship == AUDIO_FULLY_COPY:
            text = (
                f"<Audio {audio}> is directly reused 1:1 as the target video's "
                "complete audience-only music and final audio track, preserving all "
                "original audio layers, timing, and mix without recomposition, "
                "regeneration, restyling, retiming, looping, or restarting."
            )
        elif music_reuse.source_start_ms is not None:
            source_range = _background_music_source_range(music_reuse)
            text = (
                f"<Audio {audio}>'s exact {source_range} is directly copied "
                "1:1 as this target scene's complete audience-only score, beginning "
                "at the target scene's first frame and ending at its final frame. "
                "Preserve the original music, vocal signal, arrangement, "
                "instrumentation, tempo, rhythm, timing, and internal mix without "
                "recomposition, regeneration, restyling, retiming, looping, "
                "restarting, or crossfading."
            )
        else:
            text = (
                f"The background-music signal from <Audio {audio}> is directly reused "
                "as the audience-only score, preserving its copied layers and timing "
                "while other audio layers may be generated separately."
            )
        if binding is not None:
            text += (
                f" Its original vocal layer is used for exact lip synchronization by "
                f"<Subject {binding.subject}> (S{binding.speaker}) in "
                f"{_shot_list_text(binding.shot_numbers)}; no replacement vocal is generated."
            )
        return NON_DIEGETIC_MUSIC_PREFIX + text
    music = scene.soundscape.background_music
    if music in {None, SOUND_NONE}:
        return NON_DIEGETIC_MUSIC
    return NON_DIEGETIC_MUSIC_PREFIX + _sentence(music)


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


def _generated_dialogue_subjects(scene: Scene) -> set[int]:
    subjects: set[int] = set()
    for shot_number, shot in enumerate(scene.shots, start=1):
        for line_number, line in enumerate(shot.lines, start=1):
            if LIP_SYNC_LINE_RE.fullmatch(line):
                continue
            for _dialogue, subject_match in _line_dialogue_subject_matches(
                line,
                context=f"Shot {shot_number} line {line_number}",
            ):
                subjects.add(int(subject_match.group(1)))
    return subjects


def _active_voice_audio_bindings(
    emd: Emd,
    scene_number: int,
    active_subjects: list[int],
    scene_speakers: dict[int, int],
    *,
    allows_dialogue: bool,
    generated_dialogue_subjects: set[int],
) -> dict[int, tuple[int, int]]:
    if not allows_dialogue:
        return {}
    bindings: dict[int, tuple[int, int]] = {}
    for subject in active_subjects:
        if subject not in generated_dialogue_subjects:
            continue
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


def _reused_audio_bindings(
    scene: Scene, *, scene_number: int
) -> dict[int, ReusedAudioBinding]:
    bindings: dict[int, ReusedAudioBinding] = {}
    for shot_number, shot in enumerate(scene.shots, start=1):
        for line_number, line in enumerate(shot.lines, start=1):
            if not line.startswith("Lip sync:"):
                continue
            match = LIP_SYNC_LINE_RE.fullmatch(line)
            if match is None:
                raise JSONGenerationError(
                    f"Scene {scene_number} Shot {shot_number} line {line_number} "
                    "has invalid canonical lip-sync syntax"
                )
            subject = int(match.group(1))
            audio = int(match.group(2))
            transcript = match.group(3)[3:-4]
            spoken_text = re.sub(
                r"^\[[^\]]+\]", "", transcript, count=1
            ).strip()
            if not spoken_text:
                raise JSONGenerationError(
                    f"Scene {scene_number} Shot {shot_number} line {line_number} "
                    "has an empty lip-sync transcript"
                )
            existing = bindings.get(audio)
            if existing is not None and existing.subject != subject:
                raise JSONGenerationError(
                    f"Scene {scene_number} reuses <Audio {audio}> for multiple Subjects"
                )
            if existing is None:
                existing = ReusedAudioBinding(subject=subject, speaker=subject)
                bindings[audio] = existing
            if shot_number not in existing.shot_numbers:
                existing.shot_numbers.append(shot_number)
    return bindings


def _subject_block(
    emd: Emd,
    scene_number: int,
    active_subjects: list[int],
    voice_audio: dict[int, tuple[int, int]],
    reused_audio: dict[int, ReusedAudioBinding],
    background_music_reuse: BackgroundMusicReuse | None,
) -> str:
    if not active_subjects and background_music_reuse is None:
        return NO_ACTIVE_SUBJECT_BLOCK

    definitions: list[str] = [] if active_subjects else [
        "No character subject or reference-image person is active."
    ]
    for number in active_subjects:
        if number > len(emd.subjects):
            raise JSONGenerationError(
                f"Scene {scene_number} references undefined <Subject {number}>"
            )
        definition = _without_audio_references(emd.subjects[number - 1])
        definitions.append(f"<Subject {number}> is {definition}")
    audio_numbers = set(voice_audio) | set(reused_audio)
    if background_music_reuse is not None:
        audio_numbers.add(background_music_reuse.audio_number)
    audio_definitions: dict[int, str] = {}
    for audio in sorted(audio_numbers):
        if audio in voice_audio:
            subject, speaker = voice_audio[audio]
            audio_definitions[audio] = (
                f"<Audio {audio}> is the voice-timbre reference for "
                f"<Subject {subject}> (S{speaker})."
            )
            continue
        binding = reused_audio.get(audio)
        if (
            background_music_reuse is not None
            and audio == background_music_reuse.audio_number
        ):
            source_range = _background_music_source_range(background_music_reuse)
            range_text = "" if source_range is None else f" for the exact {source_range}"
            if binding is None:
                audio_definitions[audio] = (
                    f"<Audio {audio}> is the directly reused audience-only "
                    f"background-music signal{range_text}."
                )
            else:
                audio_definitions[audio] = (
                    f"<Audio {audio}> is the directly reused audience-only "
                    f"background-music signal{range_text}. Its original vocal layer is performed "
                    f"in exact lip synchronization by <Subject {binding.subject}> "
                    f"(S{binding.speaker}) in {_shot_list_text(binding.shot_numbers)}."
                )
            continue
        if binding is not None:
            audio_definitions[audio] = (
                f"<Audio {audio}> is the directly reused spoken-audio signal "
                f"performed by <Subject {binding.subject}> (S{binding.speaker}) "
                f"for exact lip synchronization in "
                f"{_shot_list_text(binding.shot_numbers)}."
            )
    definitions.extend(audio_definitions[audio] for audio in sorted(audio_definitions))
    return SUBJECT_DEFINITIONS_PREFIX + "\n".join(definitions)


def _summary_block(
    scene: Scene,
    active_subjects: list[int],
    voice_audio: dict[int, tuple[int, int]],
    reused_audio: dict[int, ReusedAudioBinding],
    background_music_reuse: BackgroundMusicReuse | None,
) -> str:
    task_types = ["reference generation"]
    if reused_audio or background_music_reuse is not None:
        task_types.append("audio reuse")
    if voice_audio:
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
            "The target video has no active character subject or reference-image "
            "person."
        )
    if scene.is_continue:
        body += " The scene continues the preceding generated scene."
    if voice_audio:
        audio_labels = ", ".join(f"<Audio {number}>" for number in sorted(voice_audio))
        body += (
            f" {audio_labels} is referenced only for the explicitly specified dialogue."
            if len(voice_audio) == 1
            else f" {audio_labels} are referenced only for the explicitly specified dialogue."
        )
    lip_only_audio = set(reused_audio)
    if background_music_reuse is not None:
        lip_only_audio.discard(background_music_reuse.audio_number)
    if lip_only_audio:
        audio_labels = ", ".join(
            f"<Audio {number}>" for number in sorted(lip_only_audio)
        )
        body += (
            f" {audio_labels} is directly reused for the explicitly transcribed lip-synced dialogue."
            if len(lip_only_audio) == 1
            else f" {audio_labels} are directly reused for the explicitly transcribed lip-synced dialogue."
        )
    if background_music_reuse is not None:
        audio = background_music_reuse.audio_number
        source_range = _background_music_source_range(background_music_reuse)
        body += (
            f" <Audio {audio}> is directly reused as the audience-only background "
            f"music with the {background_music_reuse.relationship} relationship."
        )
        if source_range is not None:
            body += (
                f" Its exact {source_range} is mapped 1:1 from the first through "
                "the final frame of this target scene without recomposition."
            )
        if audio in reused_audio:
            body += " Its original vocal layer drives the specified lip synchronization."
    return SUMMARY_PREFIX + prefix + " " + body


def _shot_list_text(numbers: list[int]) -> str:
    return ", ".join(f"[Shot {number}]" for number in numbers)


def _retention_block(
    emd: Emd,
    scene: Scene,
    active_subjects: list[int],
    voice_audio: dict[int, tuple[int, int]],
    reused_audio: dict[int, ReusedAudioBinding],
    background_music_reuse: BackgroundMusicReuse | None,
) -> str:
    if (
        not active_subjects
        and not voice_audio
        and not reused_audio
        and background_music_reuse is None
    ):
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

    audio_lines: dict[int, str] = {
        audio: (
            f"<Audio {audio}>: reference - only the voice timbre and delivery are "
            f"referenced for <Subject {subject}>; the source signal and "
            "its original speech are not copied."
        )
        for audio, (subject, _speaker) in voice_audio.items()
    }
    for audio, binding in reused_audio.items():
        if (
            background_music_reuse is not None
            and audio == background_music_reuse.audio_number
        ):
            continue
        audio_lines[audio] = (
            f"<Audio {audio}>: partially_copy - the specified spoken-audio "
            f"signal is copied for <Subject {binding.subject}>'s synchronized "
            f"dialogue in {_shot_list_text(binding.shot_numbers)}, while other "
            "audio layers are generated separately."
        )
    if background_music_reuse is not None:
        audio = background_music_reuse.audio_number
        binding = reused_audio.get(audio)
        if background_music_reuse.relationship == AUDIO_FULLY_COPY:
            description = (
                "the complete source audio is reused 1:1 as the target video's "
                "complete final audio track"
            )
        elif background_music_reuse.source_start_ms is not None:
            source_range = _background_music_source_range(background_music_reuse)
            description = (
                f"the exact {source_range} is copied 1:1 from its original timeline "
                "as this scene's complete audience-only score without recomposition, "
                "regeneration, restyling, retiming, looping, or restarting"
            )
        else:
            description = (
                "the source background-music signal is copied as the audience-only "
                "score while other audio layers may be generated separately"
            )
        if binding is not None:
            description += (
                f"; its original vocal layer drives <Subject {binding.subject}>'s "
                f"exact lip synchronization in {_shot_list_text(binding.shot_numbers)}"
            )
        audio_lines[audio] = (
            f"<Audio {audio}>: {background_music_reuse.relationship} - "
            f"{description}."
        )
    lines.extend(audio_lines[audio] for audio in sorted(audio_lines))
    return RETENTION_ANALYSIS_PREFIX + "\n".join(lines)


def _format_timestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _background_music_source_range(
    music_reuse: BackgroundMusicReuse,
) -> str | None:
    if music_reuse.source_start_ms is None or music_reuse.source_end_ms is None:
        return None
    return (
        "source interval from "
        f"{_format_timestamp(music_reuse.source_start_ms)} to "
        f"{_format_timestamp(music_reuse.source_end_ms)}"
    )


def _shot_generated_dialogue_subjects(shot: Shot) -> set[int]:
    subjects: set[int] = set()
    for line_number, line in enumerate(shot.lines, start=1):
        if LIP_SYNC_LINE_RE.fullmatch(line):
            continue
        for _dialogue, subject_match in _line_dialogue_subject_matches(
            line, context=f"Shot line {line_number}"
        ):
            subjects.add(int(subject_match.group(1)))
    return subjects


def _with_internal_speaker_ids(line: str, *, context: str) -> str:
    matches = _line_dialogue_subject_matches(line, context=context)
    insertion_points = {
        subject_match.end(): int(subject_match.group(1))
        for _dialogue, subject_match in matches
    }
    rendered = line
    for position, subject in sorted(insertion_points.items(), reverse=True):
        rendered = rendered[:position] + f" (S{subject})" + rendered[position:]
    return rendered


def _render_shot_line(
    line: str,
    *,
    context: str,
    background_music_audio: int | None,
) -> str:
    lip_sync = LIP_SYNC_LINE_RE.fullmatch(line)
    if lip_sync is None:
        return _with_internal_speaker_ids(line, context=context)
    subject = int(lip_sync.group(1))
    audio = int(lip_sync.group(2))
    dialogue = lip_sync.group(3)
    if audio == background_music_audio:
        return (
            f"<Subject {subject}> (S{subject}) visually performs and lip-syncs "
            f"exactly to the original vocal line {dialogue} in the directly reused "
            f"audience-only background music from <Audio {audio}>. The original "
            "music and vocal signal, words, and timing are preserved; no replacement, "
            "repetition, or additional vocal is generated."
        )
    return (
        f"<Subject {subject}> (S{subject}) physically performs the directly reused "
        f"spoken audio from <Audio {audio}> and lip-syncs exactly to {dialogue}. "
        "The source audio signal and exact words are preserved without replacement, "
        "repetition, or additional speech."
    )


def _detailed_description_block(
    common_lines: list[str],
    scene: Scene,
    voice_audio: dict[int, tuple[int, int]],
    background_music_reuse: BackgroundMusicReuse | None,
) -> str:
    parts = [_sentence(line) for line in common_lines]
    parts.extend(_sentence(line) for line in scene.preamble)
    background_music_audio = (
        None
        if background_music_reuse is None
        else background_music_reuse.audio_number
    )
    if (
        background_music_reuse is not None
        and background_music_reuse.source_start_ms is not None
    ):
        source_range = _background_music_source_range(background_music_reuse)
        parts.append(
            f"Use <Audio {background_music_audio}>'s exact {source_range} throughout "
            "this target scene, mapped 1:1 from its first frame through its final "
            "frame. Preserve "
            "the original music and vocal waveform content, arrangement, "
            "instrumentation, tempo, rhythm, timing, and internal mix without "
            "recomposition, regeneration, restyling, retiming, looping, restarting, "
            "or crossfading."
        )
    audio_by_subject: dict[int, list[tuple[int, int]]] = {}
    for audio, (subject, speaker) in voice_audio.items():
        audio_by_subject.setdefault(subject, []).append((audio, speaker))

    for shot_number, shot in enumerate(scene.shots, start=1):
        body = " ".join(
            _sentence(
                _render_shot_line(
                    line,
                    context=f"Shot {shot_number} line {line_number}",
                    background_music_audio=background_music_audio,
                )
            )
            for line_number, line in enumerate(shot.lines, start=1)
        )
        present_audio = {
            int(match.group(1)) for match in AUDIO_REFERENCE_RE.finditer(body)
        }
        for subject in sorted(_shot_generated_dialogue_subjects(shot)):
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


def _validate_common_prompt(emd: Emd) -> None:
    if not isinstance(emd.common_prompt, list):
        raise JSONGenerationError("common_prompt must be a list of strings")
    for line_number, line in enumerate(emd.common_prompt, start=1):
        if not isinstance(line, str) or not line.strip():
            raise JSONGenerationError(
                f"Common prompt line {line_number} must be a non-empty string"
            )
        for audio_tag in ANY_AUDIO_REFERENCE_RE.finditer(line):
            canonical = AUDIO_REFERENCE_RE.fullmatch(audio_tag.group(0))
            if canonical is None or not 1 <= int(canonical.group(1)) <= 3:
                raise JSONGenerationError(
                    f"Common prompt line {line_number} Audio reference must use "
                    "canonical <Audio 1>-<Audio 3> syntax"
                )
        if DIRECT_SPEECH_RE.search(line):
            raise JSONGenerationError(
                f"Common prompt line {line_number} cannot contain direct speech"
            )
        if SPEAKER_ID_RE.search(line):
            raise JSONGenerationError(
                f"Common prompt line {line_number} cannot contain a speaker ID"
            )
        searchable = _searchable(line, context=f"Common prompt line {line_number}")
        for subject_match in SUBJECT_RE.finditer(searchable):
            subject = int(subject_match.group(1))
            if subject > len(emd.subjects):
                raise JSONGenerationError(
                    f"Common prompt line {line_number} references undefined <Subject {subject}>"
                )
        for speech_match in SPEECH_CUE_RE.finditer(line):
            prefix = line[max(0, speech_match.start() - 80):speech_match.start()]
            if not NEGATED_SPEECH_PREFIX_RE.search(prefix):
                raise JSONGenerationError(
                    f"Common prompt line {line_number} cannot contain a positive speech instruction"
                )


def _validate_no_user_speaker_ids(emd: Emd) -> None:
    values: list[tuple[str, str]] = []
    values.extend(
        (f"Subject definition {index}", value)
        for index, value in enumerate(emd.subjects, start=1)
    )
    values.extend(
        (f"Retention rule {index}", rule.description)
        for index, rule in enumerate(emd.retention_rules, start=1)
        if isinstance(rule, RetentionRule) and isinstance(rule.description, str)
    )
    values.extend(
        (f"Common prompt line {index}", value)
        for index, value in enumerate(emd.common_prompt, start=1)
        if isinstance(value, str)
    )
    for scene_number, scene in enumerate(emd.scenes, start=1):
        values.extend(
            (f"Scene {scene_number} preamble line {index}", value)
            for index, value in enumerate(scene.preamble, start=1)
        )
        for shot_number, shot in enumerate(scene.shots, start=1):
            values.extend(
                (
                    f"Scene {scene_number} Shot {shot_number} line {line_number}",
                    value,
                )
                for line_number, value in enumerate(shot.lines, start=1)
            )
        if isinstance(scene.soundscape, Soundscape):
            values.extend(
                (f"Scene {scene_number} {label}", value)
                for label, value in (
                    ("environment", scene.soundscape.environment),
                    ("sound effects", scene.soundscape.sound_effects),
                    ("vocalization", scene.soundscape.vocalization),
                    ("background music", scene.soundscape.background_music),
                )
                if isinstance(value, str)
            )
    for context, value in values:
        if isinstance(value, str) and SPEAKER_ID_RE.search(value):
            raise JSONGenerationError(
                f"{context} contains a user-supplied speaker ID; speaker IDs are generated internally"
            )


def _shot_object(
    emd: Emd,
    scene: Scene,
    index: int,
    scene_speakers: dict[int, int],
) -> dict[str, Any]:
    scene_number = index + 1
    _validate_scene_structure(scene, scene_number)
    _validate_soundscape(
        scene.soundscape,
        context=f"Scene {scene_number}",
        duration_seconds=scene.duration,
    )
    allows_dialogue = _scene_allows_dialogue(scene, scene_number)
    generated_dialogue_subjects = _generated_dialogue_subjects(scene)
    reused_audio = _reused_audio_bindings(scene, scene_number=scene_number)
    active_subjects = _referenced_subjects(scene)
    for subject in active_subjects:
        if subject > len(emd.subjects):
            raise JSONGenerationError(
                f"Scene {scene_number} references undefined <Subject {subject}>"
            )
    voice_audio = _active_voice_audio_bindings(
        emd,
        scene_number,
        active_subjects,
        scene_speakers,
        allows_dialogue=allows_dialogue,
        generated_dialogue_subjects=generated_dialogue_subjects,
    )
    background_music_reuse = scene.soundscape.background_music_reuse
    background_music_audio = (
        None
        if background_music_reuse is None
        else background_music_reuse.audio_number
    )
    reused_audio_numbers = set(reused_audio)
    if background_music_audio is not None:
        reused_audio_numbers.add(background_music_audio)
    conflicting_audio = set(voice_audio) & reused_audio_numbers
    if conflicting_audio:
        labels = ", ".join(
            f"<Audio {number}>" for number in sorted(conflicting_audio)
        )
        raise JSONGenerationError(
            f"Scene {scene_number} assigns {labels} both as a voice-timbre "
            "reference and as a directly reused audio signal"
        )
    if (
        background_music_reuse is not None
        and background_music_reuse.relationship == AUDIO_FULLY_COPY
    ):
        extra_lip_sync_audio = set(reused_audio) - {background_music_audio}
        has_separate_sound = any(
            value not in {None, SOUND_NONE}
            for value in (
                scene.soundscape.environment,
                scene.soundscape.sound_effects,
            )
        )
        if generated_dialogue_subjects or extra_lip_sync_audio or has_separate_sound:
            raise JSONGenerationError(
                f"Scene {scene_number} uses fully_copy for background music, so it "
                "cannot add generated dialogue, another reused vocal signal, "
                "environment, or sound effects"
            )
    common_lines = _common_lines_for_scene(
        emd,
        active_subjects,
        set(voice_audio) | reused_audio_numbers,
    )
    detailed = _detailed_description_block(
        common_lines,
        scene,
        voice_audio,
        background_music_reuse,
    )
    detailed_audio = {
        int(match.group(1)) for match in AUDIO_REFERENCE_RE.finditer(detailed)
    }
    expected_audio = set(voice_audio) | reused_audio_numbers
    unexpected_audio = detailed_audio - expected_audio
    if unexpected_audio:
        labels = ", ".join(f"<Audio {number}>" for number in sorted(unexpected_audio))
        raise JSONGenerationError(
            f"Scene {scene_number} uses Audio reference(s) without an active voice-reference or lip-sync binding: {labels}"
        )

    has_background_music = (
        scene.soundscape.background_music not in {None, SOUND_NONE}
        or background_music_reuse is not None
    )
    has_bgm_lip_sync = (
        background_music_audio is not None
        and background_music_audio in reused_audio
    )

    prompt = [
        _subject_block(
            emd,
            scene_number,
            active_subjects,
            voice_audio,
            reused_audio,
            background_music_reuse,
        ),
        _summary_block(
            scene,
            active_subjects,
            voice_audio,
            reused_audio,
            background_music_reuse,
        ),
        _retention_block(
            emd,
            scene,
            active_subjects,
            voice_audio,
            reused_audio,
            background_music_reuse,
        ),
        detailed,
        _overall_soundscape(
            scene,
            allows_dialogue,
            has_background_music=has_background_music,
            reuses_background_music=background_music_reuse is not None,
            has_generated_dialogue=bool(generated_dialogue_subjects),
            has_bgm_lip_sync=has_bgm_lip_sync,
        ),
        _non_diegetic_music(scene, reused_audio),
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
    _validate_no_user_speaker_ids(emd)
    _validate_retention_rules(emd)
    _validate_common_prompt(emd)
    scene_speakers = _scene_speaker_bindings(emd)
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
        NON_DIEGETIC_MUSIC_PREFIX,
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
        music = prompt[5][len(NON_DIEGETIC_MUSIC_PREFIX):].strip()
        if DIRECT_SPEECH_RE.search(music):
            raise JSONValidationError(
                f"Shot {index} non_diegetic_music cannot contain direct speech"
            )
        for audio_match in AUDIO_REFERENCE_RE.finditer(music):
            audio_label = audio_match.group(0)
            if audio_label not in prompt[0] or audio_label not in prompt[2]:
                raise JSONValidationError(
                    f"Shot {index} non_diegetic_music uses {audio_label} without "
                    "matching subject_definitions and retention_analysis entries"
                )
        if music != "N/A" and prompt[4] == COMPLETE_SILENCE:
            raise JSONValidationError(
                f"Shot {index} overall_soundscape cannot claim complete silence when background music is active"
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
