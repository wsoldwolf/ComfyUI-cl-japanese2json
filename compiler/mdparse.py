"""Parser for canonical English reduced Markdown."""

from __future__ import annotations

import logging
import re

from .structures import Emd, Scene


LOGGER = logging.getLogger("cl_japanese2json")

VALID_SCENE_RE = re.compile(
    r"^# Scene(?: ([1-9]|[1-5][0-9]|60)sec)?(?: (CONTINUE))?$"
)
DEFENSIVE_SCENE_RE = re.compile(r"^# Scene(?: ([^\s]+)sec)?(?: (CONTINUE))?$")


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


def parse_markdown(markdown: str, *, external_first_context: bool = False) -> Emd:
    """Parse canonical Markdown without modifying payload strings."""

    if not isinstance(markdown, str):
        raise TypeError("canonical Markdown must be a string")

    emd = Emd()
    state = "OUTSIDE"
    current_scene: Scene | None = None
    directive_count = 0

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if line.strip() == "":
            state = "OUTSIDE"
            current_scene = None
            continue

        if line == "# Subjects":
            directive_count += 1
            state = "SUBJECTS"
            current_scene = None
            continue
        if line == "# Common":
            directive_count += 1
            state = "COMMON"
            current_scene = None
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
            current_scene = scene
            continue

        if line.startswith("#"):
            LOGGER.warning(
                "[cl_japanese2json] Unknown canonical directive at line %d ignored: %s",
                line_number,
                line,
            )
            state = "OUTSIDE"
            current_scene = None
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
