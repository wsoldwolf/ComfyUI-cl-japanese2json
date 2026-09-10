"""Lock Scene timing, lyrics comments, lip-sync, and soundscape from Vocal output."""

from __future__ import annotations

import re

from ..common.suno import classify_suno_section
from .errors import TimelineParseError
from .structures import TimelineDocument, TimelineLyric, TimelineScene


_SCENE_NUMBER_RE = re.compile(r"^\s*//\s*シーン\s+([1-9][0-9]*)\s*$")
_SCENE_RE = re.compile(r"^# シーン ([1-9]|[1-5][0-9]|60)秒( 継続)?$")
_DETECTION_RE = re.compile(
    r"^\s*//\s*検出状態:\s*(silent|voiced)。"
    r"ソース範囲\s+([0-9]{2,}:[0-5][0-9]\.[0-9]{3})-"
    r"([0-9]{2,}:[0-5][0-9]\.[0-9]{3})。\s*$"
)
_SECTION_RE = re.compile(r"^\s*//\s*楽曲セクション:\s*(\[[^\]\r\n]+\])\s*$")
_LYRIC_RE = re.compile(r"^\s*//\s*歌詞:\s*(.*?)\s*$")
_LIP_SYNC_PREFIX = "リップシンク:"


def _timestamp_ms(value: str) -> int:
    minutes_text, seconds_text = value.split(":", 1)
    seconds, milliseconds = seconds_text.split(".", 1)
    return int(minutes_text) * 60_000 + int(seconds) * 1000 + int(milliseconds)


def parse_prompt_timeline(text: str) -> TimelineDocument:
    if not isinstance(text, str) or not text.strip():
        raise TimelineParseError("prompt_segments must not be empty")
    if len(text) > 4_194_304:
        raise TimelineParseError("prompt_segments exceeds 4194304 characters")

    raw_scenes: list[dict[str, object]] = []
    pending_number: int | None = None
    current: dict[str, object] | None = None
    mode: str | None = None
    current_section: tuple[str, str] | None = None

    for line_number, raw_line in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"),
        start=1,
    ):
        stripped = raw_line.strip()
        number_match = _SCENE_NUMBER_RE.fullmatch(raw_line)
        if number_match:
            pending_number = int(number_match.group(1))
            continue

        scene_match = _SCENE_RE.fullmatch(stripped)
        if scene_match:
            expected = len(raw_scenes) + 1
            if pending_number != expected:
                raise TimelineParseError(
                    f"Scene comment before line {line_number} must be '// シーン {expected}'"
                )
            current = {
                "scene_id": expected,
                "duration_seconds": int(scene_match.group(1)),
                "is_continue": bool(scene_match.group(2)),
                "state": None,
                "source_start_ms": None,
                "source_end_ms": None,
                "lyrics": [],
                "lip_sync_lines": [],
                "soundscape_lines": [],
            }
            raw_scenes.append(current)
            pending_number = None
            mode = None
            current_section = None
            continue

        if current is None:
            continue

        detection_match = _DETECTION_RE.fullmatch(raw_line)
        if detection_match:
            if current["state"] is not None:
                raise TimelineParseError(
                    f"Scene {current['scene_id']} has duplicate detection metadata"
                )
            current["state"] = detection_match.group(1)
            current["source_start_ms"] = _timestamp_ms(detection_match.group(2))
            current["source_end_ms"] = _timestamp_ms(detection_match.group(3))
            continue

        section_match = _SECTION_RE.fullmatch(raw_line)
        if section_match:
            label = section_match.group(1)
            try:
                current_section = (label, classify_suno_section(label))
            except ValueError as exc:
                raise TimelineParseError(str(exc)) from exc
            continue

        lyric_match = _LYRIC_RE.fullmatch(raw_line)
        if lyric_match:
            lyric = lyric_match.group(1).strip()
            if not lyric:
                raise TimelineParseError(
                    f"Scene {current['scene_id']} has an empty lyric at line {line_number}"
                )
            label, kind = current_section or (None, None)
            lyrics = current["lyrics"]
            assert isinstance(lyrics, list)
            lyrics.append(TimelineLyric(lyric, label, kind))
            continue

        if stripped.startswith("## "):
            if stripped.startswith("## ショット"):
                mode = "shot"
            elif stripped == "## 音響":
                mode = "soundscape"
            else:
                mode = None
            continue

        if not stripped.startswith("* "):
            continue
        value = stripped[2:].strip()
        if mode == "shot" and value.startswith(_LIP_SYNC_PREFIX):
            lip_sync = current["lip_sync_lines"]
            assert isinstance(lip_sync, list)
            lip_sync.append(value)
        elif mode == "soundscape":
            soundscape = current["soundscape_lines"]
            assert isinstance(soundscape, list)
            soundscape.append(value)

    if pending_number is not None:
        raise TimelineParseError(
            f"// シーン {pending_number} is not followed by a Scene directive"
        )
    if not raw_scenes:
        raise TimelineParseError("prompt_segments contains no numbered Scenes")
    if len(raw_scenes) > 128:
        raise TimelineParseError("prompt_segments supports at most 128 Scenes")

    scenes: list[TimelineScene] = []
    expected_start = 0
    for item in raw_scenes:
        scene_id = int(item["scene_id"])
        duration = int(item["duration_seconds"])
        state = item["state"]
        start = item["source_start_ms"]
        end = item["source_end_ms"]
        if state not in {"silent", "voiced"} or not isinstance(start, int) or not isinstance(end, int):
            raise TimelineParseError(
                f"Scene {scene_id} is missing valid detection/source-range metadata"
            )
        if start != expected_start:
            raise TimelineParseError(
                f"Scene {scene_id} source range must start at {expected_start}ms, got {start}ms"
            )
        if end - start != duration * 1000:
            raise TimelineParseError(
                f"Scene {scene_id} source range length does not match {duration} seconds"
            )
        expected_start = end
        lip_sync = tuple(str(value) for value in item["lip_sync_lines"])
        soundscape = tuple(str(value) for value in item["soundscape_lines"])
        lyrics = tuple(item["lyrics"])
        if not soundscape:
            raise TimelineParseError(f"Scene {scene_id} has no ## 音響 bullets")
        if "ソース音声: 完全維持" not in soundscape:
            raise TimelineParseError(
                f"Scene {scene_id} must preserve the locked Source audio"
            )
        if state == "voiced":
            if not lip_sync:
                raise TimelineParseError(
                    f"Voiced Scene {scene_id} has no Source Vocal lip-sync"
                )
            if "発声: ソースボーカルのみ" not in soundscape:
                raise TimelineParseError(
                    f"Voiced Scene {scene_id} must allow Source Vocal only"
                )
        else:
            if lyrics:
                raise TimelineParseError(
                    f"Silent Scene {scene_id} cannot contain resolved Lyrics"
                )
            if lip_sync:
                raise TimelineParseError(
                    f"Silent Scene {scene_id} cannot contain lip-sync"
                )
            if "発声: なし" not in soundscape:
                raise TimelineParseError(
                    f"Silent Scene {scene_id} must disable vocalization"
                )
        scenes.append(
            TimelineScene(
                scene_id=scene_id,
                duration_seconds=duration,
                is_continue=bool(item["is_continue"]),
                state=str(state),
                source_start_ms=start,
                source_end_ms=end,
                lyrics=lyrics,
                lip_sync_lines=lip_sync,
                soundscape_lines=soundscape,
            )
        )

    if scenes[0].is_continue:
        raise TimelineParseError("Scene 1 cannot be a continuation")
    return TimelineDocument(tuple(scenes))
