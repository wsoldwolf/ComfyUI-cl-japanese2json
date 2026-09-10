"""Deterministically keep the first N Scenes of reduced Markdown."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..common.logging import log_node_success
from ..node_japanese_to_json.compiler.comments import strip_c_comments
from ..node_japanese_to_json.compiler.llmj2e import lex_japanese_markdown
from .errors import SceneLimiterError


_SCENE_NUMBER_COMMENT_RE = re.compile(
    r"^[ \t]*//[ \t]*シーン[ \t]+([1-9][0-9]*)[ \t]*(?:\r?\n)?$"
)
_MAX_SCENE_COUNT = 128
LOGGER = logging.getLogger("cl_scene_limiter")


def _is_scene_directive(line: str) -> bool:
    stripped = line.strip()
    return stripped == "# シーン" or stripped.startswith("# シーン ")


def _scene_start_lines(
    original_lines: list[str],
    comment_free_lines: list[str],
    *,
    line_comment_lines: frozenset[int],
    comment_only_lines: frozenset[int],
) -> list[int]:
    directive_lines = [
        index
        for index, line in enumerate(comment_free_lines)
        if _is_scene_directive(line)
    ]
    starts: list[int] = []
    for scene_number, directive_index in enumerate(directive_lines, start=1):
        start = directive_index
        cursor = directive_index - 1
        while cursor >= 0:
            line_number = cursor + 1
            if comment_free_lines[cursor].strip() and line_number not in comment_only_lines:
                break
            if line_number in line_comment_lines:
                marker = _SCENE_NUMBER_COMMENT_RE.fullmatch(original_lines[cursor])
                if marker is not None and int(marker.group(1)) == scene_number:
                    start = cursor
                    break
            cursor -= 1
        starts.append(start)
    return starts


def limit_reduced_markdown_scenes(
    reduced_markdown: str,
    scene_limit_count: int,
    *,
    disable: bool = False,
) -> str:
    """Return the source with only its first ``scene_limit_count`` Scenes."""

    if not isinstance(reduced_markdown, str):
        raise SceneLimiterError("reduced_markdown must be a string")
    if not isinstance(disable, bool):
        raise SceneLimiterError("disable must be a Boolean")
    if disable:
        return reduced_markdown
    if not reduced_markdown.strip():
        raise SceneLimiterError("reduced_markdown must not be empty")
    if (
        not isinstance(scene_limit_count, int)
        or isinstance(scene_limit_count, bool)
        or not 1 <= scene_limit_count <= _MAX_SCENE_COUNT
    ):
        raise SceneLimiterError(
            "scene_limit_count must be an integer between 1 and 128"
        )

    try:
        lex_japanese_markdown(reduced_markdown)
        scan = strip_c_comments(reduced_markdown)
    except Exception as exc:
        raise SceneLimiterError(
            f"reduced_markdown is not valid Japanese reduced Markdown: {exc}"
        ) from exc

    original_lines = reduced_markdown.splitlines(keepends=True)
    comment_free_lines = scan.text.splitlines(keepends=True)
    if len(original_lines) != len(comment_free_lines):
        raise SceneLimiterError("Comment scanning changed the physical line structure")

    starts = _scene_start_lines(
        original_lines,
        comment_free_lines,
        line_comment_lines=scan.line_comment_lines,
        comment_only_lines=scan.comment_only_lines,
    )
    if not starts:
        raise SceneLimiterError("reduced_markdown contains no # シーン directive")
    if scene_limit_count >= len(starts):
        return reduced_markdown

    cut_line = starts[scene_limit_count]
    result = "".join(original_lines[:cut_line])
    if not result.strip():
        raise SceneLimiterError("Scene limiting produced an empty document")
    return result


class CLSceneLimiter:
    """ComfyUI wrapper for exact first-N Scene filtering."""

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("limited_markdown",)
    FUNCTION = "limit_scenes"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "reduced_markdown": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Japanese reduced Markdown whose first N Scenes will be retained.",
                    },
                ),
                "scene_limit_count": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": _MAX_SCENE_COUNT,
                        "step": 1,
                        "tooltip": "Maximum number of Scenes to retain from the beginning of the document.",
                    },
                ),
                "disable": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Bypass Scene limiting and return the original input unchanged.",
                    },
                ),
            }
        }

    @staticmethod
    def limit_scenes(
        reduced_markdown: str,
        scene_limit_count: int,
        disable: bool = False,
    ) -> tuple[str]:
        result = limit_reduced_markdown_scenes(
            reduced_markdown,
            scene_limit_count,
            disable=disable,
        )
        if disable:
            log_node_success(
                LOGGER,
                "cl_scene_limiter",
                "bypass enabled; returned %d character(s) unchanged",
                len(result),
            )
        else:
            log_node_success(
                LOGGER,
                "cl_scene_limiter",
                "retained the first %d scene(s); output=%d character(s)",
                scene_limit_count,
                len(result),
            )
        return (result,)
