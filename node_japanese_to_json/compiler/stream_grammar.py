"""GBNF transport constraints, independent of any character or scene vocabulary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llmj2e import TranslationStream


def translation_stream_grammar(stream: TranslationStream) -> str:
    """Require each structural/protected token once within its source record.

    Prose remains free to expand/contract around the anchors, but cannot emit
    the reserved CLJ prefix. Without that restriction an unconstrained prose
    span could duplicate or move a marker even though the anchor is required.
    The three-state prose grammar also handles overlapping prefixes (CCLJ).
    Direct-speech contents never enter the grammar: only their opaque tokens do.
    """
    literal = lambda value: json.dumps(value, ensure_ascii=True)
    parts: list[str] = []
    previous_block = None
    for item in stream.records:
        record = item.record
        if record.block_index != previous_block:
            parts.append(literal(f"{stream.prefix}D{record.block_index}X\n"))
            previous_block = record.block_index
        parts.extend((literal(item.marker_token + " "), "prose"))
        for token in sorted(record.payload.tokens, key=record.payload.text.index):
            parts.extend((literal(token), "prose"))
        parts.append(literal("\n"))
    parts.append(literal(stream.stop_token))
    return "\n".join([
        "root ::= " + " ".join(parts),
        r'prose ::= [^\r\nC] prose | "C" after-c | ""',
        r'after-c ::= [^\r\nCL] prose | "C" after-c | "L" after-cl | ""',
        r'after-cl ::= [^\r\nCJ] prose | "C" after-c | ""',
    ]) + "\n"
