"""Parser for canonical English reduced Markdown."""

from __future__ import annotations

import logging
import re

from .errors import MarkdownParseError
from .structures import (
    Emd,
    SOUND_NONE,
    VOCALIZATION_EXPLICIT_DIALOGUE_ONLY,
    Scene,
    Soundscape,
)


LOGGER = logging.getLogger("cl_japanese2json")

VALID_SCENE_RE = re.compile(
    r"^# Scene(?: ([1-9]|[1-5][0-9]|60)sec)?(?: (CONTINUE))?$"
)
DEFENSIVE_SCENE_RE = re.compile(r"^# Scene(?: ([^\s]+)sec)?(?: (CONTINUE))?$")
SOUNDSCAPE_LINE_RE = re.compile(
    r"^\* (Environment|Sound effects|Vocalization): (.+)$"
)


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


def _set_soundscape_value(
    soundscape: Soundscape,
    line: str,
    *,
    line_number: int,
) -> None:
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


def parse_markdown(markdown: str, *, external_first_context: bool = False) -> Emd:
    """Parse canonical Markdown without modifying payload strings."""

    if not isinstance(markdown, str):
        raise TypeError("canonical Markdown must be a string")

    emd = Emd()
    state = "OUTSIDE"
    current_scene: Scene | None = None
    parent_state = "OUTSIDE"
    parent_has_soundscape = False
    current_soundscape: Soundscape | None = None
    directive_count = 0

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if line.strip() == "":
            state = "OUTSIDE"
            current_soundscape = None
            continue

        if line == "# Subjects":
            directive_count += 1
            state = "SUBJECTS"
            parent_state = state
            current_scene = None
            current_soundscape = None
            parent_has_soundscape = False
            continue
        if line == "# Common":
            directive_count += 1
            state = "COMMON"
            parent_state = state
            current_scene = None
            current_soundscape = None
            parent_has_soundscape = False
            continue

        scene_options = _parse_scene_directive(line)
        if scene_options is not None:
            directive_count += 1
            duration, is_continue = scene_options
            scene = Scene(duration=duration, is_continue=is_continue)
            emd.scenes.append(scene)
            if len(emd.scenes) == 1 and scene.is_continue and not external_first_context:
                LOGGER.warning(
                    "[cl_japanese2json] Scene 1 CONTINUE has no external context; treating it as non-continuing"
                )
                scene.is_continue = False
            state = "SCENE"
            parent_state = state
            current_scene = scene
            current_soundscape = None
            parent_has_soundscape = False
            continue

        if line == "## Soundscape":
            directive_count += 1
            if parent_has_soundscape:
                raise MarkdownParseError(
                    f"Duplicate canonical soundscape subdirective at line {line_number}"
                )
            if parent_state == "SCENE" and current_scene is not None:
                current_soundscape = current_scene.soundscape
            else:
                raise MarkdownParseError(
                    f"Canonical soundscape subdirective at line {line_number} has no Scene parent"
                )
            state = "SOUNDSCAPE"
            parent_has_soundscape = True
            continue

        if line.startswith("##"):
            raise MarkdownParseError(
                f"Unknown canonical subdirective at line {line_number}: {line}"
            )

        if line.startswith("#"):
            LOGGER.warning(
                "[cl_japanese2json] Unknown canonical directive at line %d ignored: %s",
                line_number,
                line,
            )
            state = "OUTSIDE"
            parent_state = "OUTSIDE"
            current_scene = None
            current_soundscape = None
            parent_has_soundscape = False
            continue

        if line.startswith("* "):
            body = line[2:]
            if state == "SUBJECTS":
                emd.subjects.append(body)
                if len(emd.subjects) > 4:
                    LOGGER.warning(
                        "[cl_japanese2json] Subject definition %d exceeds the standard Subject 1-4 range",
                        len(emd.subjects),
                    )
            elif state == "COMMON":
                emd.common_prompt.append(body)
            elif state == "SCENE" and current_scene is not None:
                current_scene.shots.append(body)
            elif state == "SOUNDSCAPE" and current_soundscape is not None:
                _set_soundscape_value(
                    current_soundscape,
                    line,
                    line_number=line_number,
                )
            else:
                LOGGER.warning(
                    "[cl_japanese2json] Bullet outside a recognized section at line %d ignored",
                    line_number,
                )
            continue

        if state == "OUTSIDE":
            LOGGER.warning(
                "[cl_japanese2json] Unknown line %d outside a section ignored",
                line_number,
            )
        else:
            LOGGER.warning(
                "[cl_japanese2json] Non-bullet line %d inside %s ignored",
                line_number,
                state,
            )

    LOGGER.info(
        "[cl_japanese2json] Parsed %d directive(s): %d subject(s), %d common line(s), %d scene(s)",
        directive_count,
        len(emd.subjects),
        len(emd.common_prompt),
        len(emd.scenes),
    )
    return emd
