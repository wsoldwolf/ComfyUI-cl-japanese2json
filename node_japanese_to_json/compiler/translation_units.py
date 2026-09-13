"""Sentence boundaries and explicit-negation checks for protected prose.

This is a transport check, not a semantic translator. Vocabulary and the scope
of a prohibition remain the LLM's responsibility.
"""

from __future__ import annotations

import re


# Split only Japanese sentence punctuation. Decimal points, English
# abbreviations, and punctuation inside protected speech are left untouched.
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？])\s*")

# Deliberately limited to explicit grammatical prohibition forms. Bare ない
# would also match positive descriptions such as 少ない / 危ない.
PROHIBITION_RE = re.compile(
    r"しない|しません|せない|せません|されない|せず|ずに|ことなく|"
    r"ては(?:いけない|ならない)|禁止(?:する|し|で|と|[。！？]|$)"
)
# These constructions cannot be assigned negative polarity by the presence
# of one suffix: obligation and double negation require ordinary translation.
NON_PROHIBITION_RE = re.compile(
    r"しないと(?:いけない|ならない|だめ)|"
    r"(?:しない|せない|されない)(?:わけではない|ことはない|とは限らない)"
)
ENGLISH_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|without|neither|nor|"
    r"avoid(?:s|ed|ing)?|prevent(?:s|ed|ing)?|"
    r"prohibit(?:s|ed|ing)?|forbid(?:s|den|ding)?|"
    r"refrain(?:s|ed|ing)?|exclude(?:s|d)?|excluding)\b|"
    r"\b[a-z]+n['’]t\b",
    re.IGNORECASE,
)


def split_sentences(protected_text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BOUNDARY_RE.split(protected_text)
            if part.strip()]


def needs_explicit_negation(protected_text: str) -> bool:
    """Recognize selected prohibition forms outside protected reference/speech."""

    return PROHIBITION_RE.search(
        NON_PROHIBITION_RE.sub("", protected_text)
    ) is not None


def has_explicit_negation(protected_translation: str) -> bool:
    return ENGLISH_NEGATION_RE.search(protected_translation) is not None
