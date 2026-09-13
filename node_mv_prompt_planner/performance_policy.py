"""Shared duration-aware performance constraints for MV Scene planning."""

from __future__ import annotations

import re
import unicodedata


def action_signature(value: str) -> str:
    """Ignore formatting, not targets, direction, negation or repeat cues."""

    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).rstrip("。.!！")
    # Equivalent Subject-led limb instructions are not a second motion phase.
    value = re.sub(r"(<Subject\d+>)(?:の|は)(?=(?:右|左|両)?(?:手|腕|足|脚|膝|腰))", r"\1", value)
    return value


def motion_phase_signature(value: str) -> str:
    """A speed adverb alone does not establish another executed phase."""
    value = action_signature(value)
    if re.search(r"ない|せず|禁止|もう一度|再び|繰り返|回|拍", value):
        return value
    return re.sub(r"ゆっくり(?:と)?", "", value)


def deduplicate_actions(actions: tuple[str, ...]) -> tuple[str, ...]:
    """Remove repeated complete sentences within one Shot in stable order.

    Do not use fuzzy similarity: left/right, contact/release and explicit
    repetitions must survive even when their wording is very similar.
    """

    seen: set[str] = set()
    result: list[str] = []
    for action in actions:
        kept: list[str] = []
        for sentence in re.split(r"(?<=。)", action):
            sentence = sentence.strip()
            if sentence.startswith("続いて"):
                sentence = sentence[len("続いて"):].lstrip("、 ")
            key = motion_phase_signature(sentence)
            if key and key not in seen:
                kept.append(sentence)
                seen.add(key)
        if kept:
            result.append("".join(kept))
    return tuple(result)


def minimum_deliberate_action_phases(duration_seconds: int) -> int:
    """Return the minimum articulated Subject-motion phases for a Scene."""

    if duration_seconds >= 12:
        return 4
    if duration_seconds >= 8:
        return 3
    if duration_seconds >= 5:
        return 2
    return 1


def balanced_shot_start_windows(
    duration_ms: int, shot_count: int
) -> tuple[tuple[int, int, int], ...]:
    """Return broad min/preferred/max windows for later Shot starts.

    These are coverage guards, not an instruction to cut mechanically at equal
    intervals.  A 35 percent tolerance leaves room for lyric and action beats
    while preventing a multi-Shot plan from spending almost the whole Scene in
    its first or final Shot.
    """

    if duration_ms <= 1 or shot_count <= 1:
        return ()
    segment = duration_ms / shot_count
    tolerance = segment * 0.35
    windows: list[tuple[int, int, int]] = []
    previous_maximum = 0
    for index in range(1, shot_count):
        preferred = round(segment * index)
        minimum = max(previous_maximum + 1, round(preferred - tolerance))
        maximum = min(duration_ms - 1, round(preferred + tolerance))
        if minimum > maximum:
            minimum = maximum
        windows.append((minimum, preferred, maximum))
        previous_maximum = maximum
    return tuple(windows)
