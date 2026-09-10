"""C-style comment scanning for the reduced Markdown input language."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import CommentSyntaxError


@dataclass(frozen=True)
class CommentScanResult:
    """Comment-free text plus lines that contained comments and no source text."""

    text: str
    comment_only_lines: frozenset[int]
    line_comment_lines: frozenset[int]


def strip_c_comments(text: str) -> CommentScanResult:
    """Replace C-style comments with whitespace while preserving line structure.

    ``//`` starts a comment only when it is the first non-whitespace content on
    its physical line. ``/* ... */`` works inline and across lines. Comment
    delimiters inside Japanese corner-bracket dialogue or ``<d>`` regions are
    copied literally.
    """

    if not isinstance(text, str):
        raise TypeError("comment input must be a string")

    output: list[str] = []
    state = "NORMAL"
    index = 0
    line_number = 1
    column_number = 1
    line_has_only_whitespace = True
    comment_lines: set[int] = set()
    line_comment_lines: set[int] = set()
    content_lines: set[int] = set()
    block_start: tuple[int, int] | None = None

    def copy_character(character: str, *, content: bool) -> None:
        nonlocal line_number, column_number, line_has_only_whitespace
        output.append(character)
        if character == "\n":
            line_number += 1
            column_number = 1
            line_has_only_whitespace = True
            return
        if character == "\r":
            column_number = 1
            line_has_only_whitespace = True
            return
        column_number += 1
        if content and not character.isspace():
            content_lines.add(line_number)
            line_has_only_whitespace = False

    def replace_character(character: str) -> None:
        comment_lines.add(line_number)
        copy_character(character if character in "\r\n" else " ", content=False)

    while index < len(text):
        if state == "LINE_COMMENT":
            character = text[index]
            replace_character(character)
            index += 1
            if character in "\r\n":
                state = "NORMAL"
            continue

        if state == "BLOCK_COMMENT":
            if text.startswith("/*", index):
                raise CommentSyntaxError(
                    f"Nested block comment at line {line_number}, column {column_number}"
                )
            if text.startswith("*/", index):
                replace_character("*")
                replace_character("/")
                index += 2
                state = "NORMAL"
                block_start = None
                continue
            replace_character(text[index])
            index += 1
            continue

        if state == "JAPANESE_DIALOGUE":
            character = text[index]
            copy_character(character, content=True)
            index += 1
            if character == "」":
                state = "NORMAL"
            continue

        if state == "DIRECT_SPEECH":
            if text.startswith("</d>", index):
                for character in "</d>":
                    copy_character(character, content=True)
                index += 4
                state = "NORMAL"
                continue
            copy_character(text[index], content=True)
            index += 1
            continue

        if text.startswith("*/", index):
            raise CommentSyntaxError(
                f"Unmatched block-comment terminator at line {line_number}, column {column_number}"
            )
        if text.startswith("<!--", index) or text.startswith("-->", index):
            raise CommentSyntaxError(
                "HTML comments are not supported; use // or /* ... */ "
                f"at line {line_number}, column {column_number}"
            )
        if text.startswith("/*", index):
            block_start = (line_number, column_number)
            replace_character("/")
            replace_character("*")
            index += 2
            state = "BLOCK_COMMENT"
            continue
        if text.startswith("//", index) and line_has_only_whitespace:
            line_comment_lines.add(line_number)
            replace_character("/")
            replace_character("/")
            index += 2
            state = "LINE_COMMENT"
            continue
        if text.startswith("<d>", index):
            for character in "<d>":
                copy_character(character, content=True)
            index += 3
            state = "DIRECT_SPEECH"
            continue

        character = text[index]
        copy_character(character, content=True)
        index += 1
        if character == "「":
            state = "JAPANESE_DIALOGUE"

    if state == "BLOCK_COMMENT":
        assert block_start is not None
        start_line, start_column = block_start
        raise CommentSyntaxError(
            f"Unclosed block comment starting at line {start_line}, column {start_column}"
        )

    return CommentScanResult(
        "".join(output),
        frozenset(comment_lines - content_lines),
        frozenset(line_comment_lines),
    )
