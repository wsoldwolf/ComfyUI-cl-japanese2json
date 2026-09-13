"""Conservative deterministic conflict checks for immutable user facts."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable


_TIME_OF_DAY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "night",
        re.compile(
            r"夜間|深夜|真夜中|夜空|夜の|月光|月明かり|月が(?:出|昇)|星空"
        ),
    ),
    (
        "day",
        re.compile(
            r"日中|昼間|昼下がり|真昼|昼の|太陽|日差し|日光|陽光|青空|木漏れ日"
        ),
    ),
    (
        "dawn",
        re.compile(r"明け方|夜明け|日の出|朝焼け|朝日|早朝|朝の"),
    ),
    (
        "dusk",
        re.compile(r"夕方|夕暮れ|夕刻|日没|黄昏|夕焼け|夕日|夕陽"),
    ),
)
_MIXED_RESPONSIBILITY_RE = re.compile(
    r"<(?:Subject|Picture|Audio)\s+[1-9][0-9]*>"
    r"|人物|キャラクター|被写体|カメラ|撮影|リップシンク|発声|音声|歌唱"
    r"|動作|ポーズ|腕|脚|手|顔|衣装|髪|尻尾|耳"
)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?;；]+")
_EXPLICIT_TIME_CLAUSE_RE = re.compile(
    r"(?:時刻|時間帯)[^、,。！？!?;；]{0,48}"
)
_NEGATION_RE = re.compile(
    r"表示しない|使用しない|描かない|生成しない|含めない|禁止|避ける"
    r"|除外|にしない|ではない|でない"
)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _raw_time_of_day_labels(value: str) -> set[str]:
    return {
        label for label, pattern in _TIME_OF_DAY_PATTERNS if pattern.search(value)
    }


def time_of_day_labels(value: str) -> frozenset[str]:
    """Return positive time-of-day concepts, excluding prohibition clauses."""

    text = _normalized(value)
    labels: set[str] = set()
    for fragment in _SENTENCE_SPLIT_RE.split(text):
        if fragment and _NEGATION_RE.search(fragment) is None:
            labels.update(_raw_time_of_day_labels(fragment))
    return frozenset(labels)


def authoritative_time_of_day(values: Iterable[str]) -> str | None:
    """Return one unambiguous time-of-day authority from user Common lines."""

    normalized_values = [_normalized(value) for value in values]
    explicit_labels: set[str] = set()
    for value in normalized_values:
        for match in _EXPLICIT_TIME_CLAUSE_RE.finditer(value):
            clause = match.group(0)
            if _NEGATION_RE.search(clause) is None:
                explicit_labels.update(_raw_time_of_day_labels(clause))
    if len(explicit_labels) == 1:
        return next(iter(explicit_labels))
    if len(explicit_labels) > 1:
        return None

    labels: set[str] = set()
    for value in normalized_values:
        labels.update(time_of_day_labels(value))
    if len(labels) != 1:
        return None
    return next(iter(labels))


def conflicts_with_time_of_day(value: str, authority: str | None) -> bool:
    """Return whether a sentence explicitly names a different time of day."""

    if authority is None:
        return False
    labels = time_of_day_labels(value)
    return bool(labels - {authority})


def source_line_can_be_removed(value: str) -> bool:
    """Protect mixed character, camera, and audio instructions from deletion."""

    return _MIXED_RESPONSIBILITY_RE.search(_normalized(value)) is None
