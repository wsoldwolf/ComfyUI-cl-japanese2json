"""Strict parser for canonical English reduced Markdown."""

from __future__ import annotations

import logging
import re

from .comments import strip_c_comments
from .errors import MarkdownParseError
from .structures import (
    AUDIO_COPY_RELATIONSHIPS,
    BackgroundMusicReuse,
    Emd,
    RETENTION_ATTRIBUTE_TRANSFER,
    RETENTION_RELATIONSHIPS,
    SOUND_NONE,
    VOCALIZATION_EXPLICIT_DIALOGUE_ONLY,
    RetentionRule,
    Scene,
    Shot,
    Soundscape,
)


LOGGER = logging.getLogger("cl_japanese2json")

VALID_SCENE_RE = re.compile(
    r"^# Scene(?: ([1-9]|[1-5][0-9]|60)sec)?(?: (CONTINUE))?$"
)
DEFENSIVE_SCENE_RE = re.compile(r"^# Scene(?: ([^\s]+)sec)?(?: (CONTINUE))?$")
VALID_SHOT_RE = re.compile(r"^## Shot(?: ((?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?)sec)?$")
SOUNDSCAPE_LINE_RE = re.compile(
    r"^\* (Environment|Sound effects|Vocalization|Background music): (.+)$"
)
BACKGROUND_MUSIC_REUSE_LINE_RE = re.compile(
    r"^\* Background music reuse: <Audio ([1-3])> "
    r"(fully_copy|partially_copy)"
    r"(?: ([0-9]{2,}:[0-5][0-9]\.[0-9]{3})-"
    r"([0-9]{2,}:[0-5][0-9]\.[0-9]{3}))?$"
)
LIP_SYNC_LINE_RE = re.compile(
    r"^Lip sync: <Subject ([1-4])> <- <Audio ([1-3])>: "
    r"(<d>(?:(?!<d>|</d>).)+</d>)$"
)
RETENTION_LINE_RE = re.compile(
    r"^\* <Subject ([1-9][0-9]*)> "
    r"(fully_preserved|partially_preserved|attribute_transfer|weak_reference)"
    r"(?: -> <Subject ([1-9][0-9]*)>)?: (.+)$"
)
SPEAKER_ID_RE = re.compile(r"(?<!\\)\(S[1-9][0-9]*\)")
AUDIO_REFERENCE_RE = re.compile(r"(?<!\\)<Audio ([1-9][0-9]*)(?<!\\)>")
ANY_AUDIO_REFERENCE_RE = re.compile(r"(?<!\\)<Audio\s*[0-9]+\s*(?<!\\)>")
DIRECT_SPEECH_RE = re.compile(r"(?<!\\)<d>.*?(?<!\\)</d>", re.DOTALL)


def _parse_scene_directive(line: str) -> tuple[int, bool] | None:
    match = VALID_SCENE_RE.fullmatch(line)
    if match:
        return (int(match.group(1)) if match.group(1) else 5, bool(match.group(2)))

    defensive = DEFENSIVE_SCENE_RE.fullmatch(line)
    if not defensive:
        return None

    duration = 5
    raw_duration = defensive.group(1)
    if raw_duration is not None:
        try:
            candidate = int(raw_duration, 10)
        except ValueError:
            candidate = 5
        if 1 <= candidate <= 60 and raw_duration == str(candidate):
            duration = candidate
        else:
            LOGGER.warning(
                "[cl_japanese2json] Invalid canonical scene duration %r; using 5 seconds",
                raw_duration,
            )
    return duration, bool(defensive.group(2))


def _seconds_text_to_ms(value: str) -> int:
    whole, dot, fraction = value.partition(".")
    milliseconds = int(whole) * 1000
    if dot:
        milliseconds += int(fraction.ljust(3, "0"))
    return milliseconds


def _source_timestamp_to_ms(value: str) -> int:
    minutes_text, seconds_text = value.split(":", 1)
    seconds, milliseconds = seconds_text.split(".", 1)
    return (
        int(minutes_text) * 60_000
        + int(seconds) * 1000
        + int(milliseconds)
    )


def _set_soundscape_value(
    soundscape: Soundscape,
    line: str,
    *,
    line_number: int,
) -> None:
    reuse_match = BACKGROUND_MUSIC_REUSE_LINE_RE.fullmatch(line)
    if reuse_match is not None:
        if (
            soundscape.background_music is not None
            or soundscape.background_music_reuse is not None
        ):
            raise MarkdownParseError(
                f"Duplicate or conflicting canonical background music value at line {line_number}"
            )
        audio_text, relationship, source_start, source_end = reuse_match.groups()
        if relationship not in AUDIO_COPY_RELATIONSHIPS:
            raise MarkdownParseError(
                f"Invalid canonical background music reuse relationship at line {line_number}"
            )
        if source_start is not None and relationship != "partially_copy":
            raise MarkdownParseError(
                f"Canonical background music source range at line {line_number} is allowed only with partially_copy"
            )
        source_start_ms = (
            None if source_start is None else _source_timestamp_to_ms(source_start)
        )
        source_end_ms = (
            None if source_end is None else _source_timestamp_to_ms(source_end)
        )
        if source_start_ms is not None and source_end_ms <= source_start_ms:
            raise MarkdownParseError(
                f"Canonical background music source range at line {line_number} must have an end later than its start"
            )
        soundscape.background_music_reuse = BackgroundMusicReuse(
            audio_number=int(audio_text),
            relationship=relationship,
            source_start_ms=source_start_ms,
            source_end_ms=source_end_ms,
        )
        return

    match = SOUNDSCAPE_LINE_RE.fullmatch(line)
    if match is None:
        raise MarkdownParseError(
            f"Invalid canonical soundscape bullet at line {line_number}"
        )
    label, value = match.groups()
    if label == "Environment":
        attribute = "environment"
    elif label == "Sound effects":
        attribute = "sound_effects"
    elif label == "Background music":
        attribute = "background_music"
        if soundscape.background_music_reuse is not None:
            raise MarkdownParseError(
                f"Duplicate or conflicting canonical background music value at line {line_number}"
            )
    else:
        attribute = "vocalization"
        if value not in {SOUND_NONE, VOCALIZATION_EXPLICIT_DIALOGUE_ONLY}:
            raise MarkdownParseError(
                f"Invalid canonical vocalization value at line {line_number}"
            )

    if getattr(soundscape, attribute) is not None:
        raise MarkdownParseError(
            f"Duplicate canonical {label} soundscape value at line {line_number}"
        )
    setattr(soundscape, attribute, value)


def _parse_retention_rule(
    line: str,
    *,
    line_number: int,
    seen_subjects: set[int],
) -> RetentionRule:
    match = RETENTION_LINE_RE.fullmatch(line)
    if match is None:
        raise MarkdownParseError(
            f"Invalid canonical retention bullet at line {line_number}"
        )
    source_text, relationship, target_text, description = match.groups()
    source = int(source_text)
    target = int(target_text) if target_text is not None else None
    if relationship not in RETENTION_RELATIONSHIPS:
        raise MarkdownParseError(
            f"Invalid retention relationship at line {line_number}"
        )
    if relationship == RETENTION_ATTRIBUTE_TRANSFER:
        if target is None:
            raise MarkdownParseError(
                f"attribute_transfer requires a target Subject at line {line_number}"
            )
        if target == source:
            raise MarkdownParseError(
                f"attribute_transfer source and target must differ at line {line_number}"
            )
    elif target is not None:
        raise MarkdownParseError(
            f"Only attribute_transfer accepts a target Subject at line {line_number}"
        )
    if source in seen_subjects:
        raise MarkdownParseError(
            f"Duplicate retention rule for <Subject {source}> at line {line_number}"
        )
    seen_subjects.add(source)
    return RetentionRule(source, relationship, description, target)


def _validate_scene(scene: Scene, scene_number: int) -> None:
    if not scene.shots:
        raise MarkdownParseError(
            f"Scene {scene_number} must contain at least one ## Shot subdirective"
        )
    for shot_number, shot in enumerate(scene.shots, start=1):
        if not shot.lines:
            raise MarkdownParseError(
                f"Scene {scene_number} Shot {shot_number} must contain at least one bullet"
            )
    music_reuse = scene.soundscape.background_music_reuse
    if music_reuse is not None and music_reuse.source_start_ms is not None:
        source_duration = music_reuse.source_end_ms - music_reuse.source_start_ms
        if source_duration != scene.duration * 1000:
            raise MarkdownParseError(
                f"Scene {scene_number} background music source range must exactly match "
                f"the {scene.duration}-second Scene duration"
            )


def parse_markdown(markdown: str, *, external_first_context: bool = False) -> Emd:
    """Parse canonical Markdown without modifying translated payload strings."""

    if not isinstance(markdown, str):
        raise TypeError("canonical Markdown must be a string")

    comment_scan = strip_c_comments(markdown.lstrip("\ufeff"))

    emd = Emd()
    state = "OUTSIDE"
    current_scene: Scene | None = None
    current_shot: Shot | None = None
    current_soundscape: Soundscape | None = None
    scene_has_soundscape = False
    retention_subjects: set[int] = set()
    seen_subjects_directive = False
    seen_retention_directive = False
    seen_common_directive = False
    seen_scene_directive = False
    directive_count = 0

    for line_number, line in enumerate(comment_scan.text.splitlines(), start=1):
        if line_number in comment_scan.comment_only_lines:
            continue
        line = line.rstrip(" \t")
        if line.strip() == "":
            state = "OUTSIDE"
            current_shot = None
            current_soundscape = None
            continue

        if line == "# Subjects":
            if (
                seen_subjects_directive
                or seen_retention_directive
                or seen_common_directive
                or seen_scene_directive
            ):
                raise MarkdownParseError(
                    f"# Subjects must appear exactly once before retention, common prompt, and scenes (line {line_number})"
                )
            seen_subjects_directive = True
            directive_count += 1
            state = "SUBJECTS"
            current_scene = None
            continue

        if line == "# Retention":
            if seen_retention_directive or seen_common_directive or seen_scene_directive:
                raise MarkdownParseError(
                    f"# Retention must appear at most once before common prompt and scenes (line {line_number})"
                )
            seen_retention_directive = True
            directive_count += 1
            state = "RETENTION"
            current_scene = None
            continue

        if line == "# Common":
            if seen_common_directive or seen_scene_directive:
                raise MarkdownParseError(
                    f"# Common must appear at most once before scenes (line {line_number})"
                )
            seen_common_directive = True
            directive_count += 1
            state = "COMMON"
            current_scene = None
            continue

        scene_options = _parse_scene_directive(line)
        if scene_options is not None:
            if current_scene is not None:
                if scene_has_soundscape and all(
                    value is None
                    for value in (
                        current_scene.soundscape.environment,
                        current_scene.soundscape.sound_effects,
                        current_scene.soundscape.vocalization,
                        current_scene.soundscape.background_music,
                        current_scene.soundscape.background_music_reuse,
                    )
                ):
                    raise MarkdownParseError(
                        f"Scene {len(emd.scenes)} Soundscape must contain at least one bullet"
                    )
                _validate_scene(current_scene, len(emd.scenes))
            seen_scene_directive = True
            directive_count += 1
            duration, is_continue = scene_options
            scene = Scene(duration=duration, is_continue=is_continue)
            emd.scenes.append(scene)
            if len(emd.scenes) == 1 and scene.is_continue and not external_first_context:
                LOGGER.warning(
                    "[cl_japanese2json] Scene 1 CONTINUE has no external context; treating it as non-continuing"
                )
                scene.is_continue = False
            state = "PREAMBLE"
            current_scene = scene
            current_shot = None
            current_soundscape = None
            scene_has_soundscape = False
            continue

        shot_match = VALID_SHOT_RE.fullmatch(line)
        if shot_match is not None:
            if current_scene is None:
                raise MarkdownParseError(
                    f"Canonical Shot subdirective at line {line_number} has no Scene parent"
                )
            if scene_has_soundscape:
                raise MarkdownParseError(
                    f"Canonical Shot subdirective at line {line_number} cannot follow Soundscape"
                )
            if current_scene.shots and not current_scene.shots[-1].lines:
                raise MarkdownParseError(
                    f"Previous Shot must contain at least one bullet before line {line_number}"
                )
            raw_start = shot_match.group(1)
            if not current_scene.shots:
                if raw_start is not None:
                    raise MarkdownParseError(
                        f"The first Shot must not specify a start time at line {line_number}"
                    )
                start_ms = 0
            else:
                if raw_start is None:
                    raise MarkdownParseError(
                        f"Shot {len(current_scene.shots) + 1} requires a start time at line {line_number}"
                    )
                start_ms = _seconds_text_to_ms(raw_start)
                if start_ms <= current_scene.shots[-1].start_ms:
                    raise MarkdownParseError(
                        f"Shot start times must increase at line {line_number}"
                    )
                if start_ms >= current_scene.duration * 1000:
                    raise MarkdownParseError(
                        f"Shot start time must be earlier than the Scene duration at line {line_number}"
                    )
            current_shot = Shot(start_ms=start_ms)
            current_scene.shots.append(current_shot)
            directive_count += 1
            state = "SHOT"
            current_soundscape = None
            continue

        if line == "## Soundscape":
            if current_scene is None:
                raise MarkdownParseError(
                    f"Canonical Soundscape subdirective at line {line_number} has no Scene parent"
                )
            if scene_has_soundscape:
                raise MarkdownParseError(
                    f"Duplicate canonical Soundscape subdirective at line {line_number}"
                )
            if not current_scene.shots:
                raise MarkdownParseError(
                    f"Canonical Soundscape at line {line_number} must follow at least one Shot"
                )
            if not current_scene.shots[-1].lines:
                raise MarkdownParseError(
                    f"Previous Shot must contain at least one bullet before line {line_number}"
                )
            scene_has_soundscape = True
            current_soundscape = current_scene.soundscape
            current_shot = None
            directive_count += 1
            state = "SOUNDSCAPE"
            continue

        if line.startswith("#"):
            raise MarkdownParseError(
                f"Unknown or removed canonical directive at line {line_number}: {line}"
            )

        if not line.startswith("* "):
            raise MarkdownParseError(
                f"Expected a bullet at line {line_number}: {line}"
            )

        body = line[2:]
        if not body.strip():
            raise MarkdownParseError(f"Empty bullet at line {line_number}")
        if SPEAKER_ID_RE.search(body):
            raise MarkdownParseError(
                f"Speaker IDs are generated internally and cannot be written at line {line_number}"
            )
        if state == "SUBJECTS":
            emd.subjects.append(body)
            if len(emd.subjects) > 4:
                LOGGER.warning(
                    "[cl_japanese2json] Subject definition %d exceeds the standard Subject 1-4 range",
                    len(emd.subjects),
                )
        elif state == "RETENTION":
            emd.retention_rules.append(
                _parse_retention_rule(
                    line,
                    line_number=line_number,
                    seen_subjects=retention_subjects,
                )
            )
        elif state == "COMMON":
            for audio_tag in ANY_AUDIO_REFERENCE_RE.finditer(body):
                canonical = AUDIO_REFERENCE_RE.fullmatch(audio_tag.group(0))
                if canonical is None or not 1 <= int(canonical.group(1)) <= 3:
                    raise MarkdownParseError(
                        f"Common prompt Audio reference at line {line_number} "
                        "must use canonical <Audio 1>-<Audio 3> syntax"
                    )
            if DIRECT_SPEECH_RE.search(body):
                raise MarkdownParseError(
                    f"Common prompt cannot contain direct speech at line {line_number}"
                )
            emd.common_prompt.append(body)
        elif state == "PREAMBLE" and current_scene is not None:
            if current_scene.shots or scene_has_soundscape:
                raise MarkdownParseError(
                    f"Scene preamble bullets must precede every Shot at line {line_number}"
                )
            current_scene.preamble.append(body)
        elif state == "SHOT" and current_shot is not None:
            if body.startswith("Lip sync:"):
                lip_sync = LIP_SYNC_LINE_RE.fullmatch(body)
                if lip_sync is None:
                    raise MarkdownParseError(
                        f"Invalid canonical lip-sync bullet at line {line_number}"
                    )
                transcript = lip_sync.group(3)[3:-4]
                spoken_text = re.sub(
                    r"^\[[^\]]+\]", "", transcript, count=1
                ).strip()
                if not spoken_text:
                    raise MarkdownParseError(
                        f"Canonical lip-sync dialogue at line {line_number} must not be empty"
                    )
            current_shot.lines.append(body)
        elif state == "SOUNDSCAPE" and current_soundscape is not None:
            _set_soundscape_value(current_soundscape, line, line_number=line_number)
        else:
            raise MarkdownParseError(
                f"Bullet at line {line_number} is outside a recognized section"
            )

    if current_scene is not None:
        if scene_has_soundscape and all(
            value is None
            for value in (
                current_scene.soundscape.environment,
                current_scene.soundscape.sound_effects,
                current_scene.soundscape.vocalization,
                current_scene.soundscape.background_music,
                current_scene.soundscape.background_music_reuse,
            )
        ):
            raise MarkdownParseError(
                f"Scene {len(emd.scenes)} Soundscape must contain at least one bullet"
            )
        _validate_scene(current_scene, len(emd.scenes))
    if seen_common_directive and not emd.common_prompt:
        raise MarkdownParseError("# Common must contain at least one bullet")
    if not emd.scenes:
        raise MarkdownParseError("At least one # Scene directive is required")

    LOGGER.info(
        "[cl_japanese2json] Parsed %d directive(s): %d subject(s), %d retention rule(s), %d common prompt line(s), %d scene(s)",
        directive_count,
        len(emd.subjects),
        len(emd.retention_rules),
        len(emd.common_prompt),
        len(emd.scenes),
    )
    return emd
