"""PCM vocal analysis, Whisper lyrics alignment, and prompt templates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Iterable

from .compiler.errors import VocalPromptError
from .compiler.llmj2e import lex_japanese_markdown
from .whisper_backend import WhisperBackend
from .whisper_discovery import (
    discover_whisper_model_names,
    resolve_whisper_model_name,
)


LOGGER = logging.getLogger("cl_vocal2promptseg")
_SECTION_HEADING_RE = re.compile(r"^\[[^\]\r\n]+\]$")
_VALID_STATES = {"silent", "voiced"}
_WHISPER_SAMPLE_RATE = 16_000
_VAD_CHUNK_WINDOWS = 2_048

try:  # Available only when loaded by ComfyUI.
    from comfy.utils import ProgressBar as _ComfyProgressBar  # type: ignore
except ImportError:  # pragma: no cover - standalone unit-test environment
    _ComfyProgressBar = None


@dataclass(frozen=True)
class LyricLine:
    lyrics_index: int
    source_line: int
    text: str
    normalized: str


@dataclass(frozen=True)
class DetectedInterval:
    state: str
    start_sample: int
    end_sample: int


@dataclass(frozen=True)
class WhisperWord:
    text: str
    normalized: str
    start: float
    end: float
    source_order: int


@dataclass(frozen=True)
class LyricAlignment:
    line: LyricLine
    status: str
    match_score: float
    whisper_text: str
    start_ms: int | None
    end_ms: int | None
    scene_index: int | None = None


def _import_torch() -> Any:
    try:
        return importlib.import_module("torch")
    except Exception as exc:
        raise VocalPromptError(
            "PyTorch is unavailable. Run this node inside the ComfyUI Python "
            "environment; dependencies are not installed automatically."
        ) from exc


def _validate_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise VocalPromptError(f"{name} must be Boolean")
    return value


def _validate_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise VocalPromptError(
            f"{name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _validate_float(
    value: Any,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise VocalPromptError(f"{name} must be between {minimum} and {maximum}")
    return float(value)


def _validate_audio(audio: Any) -> tuple[Any, int, int, int]:
    if not isinstance(audio, dict):
        raise VocalPromptError("vocal_audio must be a ComfyUI AUDIO object")
    waveform = audio.get("waveform")
    sample_rate = audio.get("sample_rate")
    if waveform is None or not hasattr(waveform, "shape"):
        raise VocalPromptError("vocal_audio.waveform must be a tensor")
    shape = tuple(waveform.shape)
    if len(shape) != 3:
        raise VocalPromptError(
            "vocal_audio.waveform must have shape [batch, channels, samples]"
        )
    if shape[0] != 1:
        raise VocalPromptError("vocal_audio must contain exactly one batch timeline")
    if shape[1] < 1 or shape[2] < 1:
        raise VocalPromptError(
            "vocal_audio.waveform must contain at least one channel and one sample"
        )
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or sample_rate < 1
    ):
        raise VocalPromptError("vocal_audio.sample_rate must be a positive integer")
    return waveform, sample_rate, int(shape[1]), int(shape[2])


def _katakana_to_hiragana(text: str) -> str:
    converted: list[str] = []
    for char in text:
        codepoint = ord(char)
        if 0x30A1 <= codepoint <= 0x30F6:
            converted.append(chr(codepoint - 0x60))
        else:
            converted.append(char)
    return "".join(converted)


def normalize_match_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _katakana_to_hiragana(normalized)
    return "".join(
        char
        for char in normalized
        if unicodedata.category(char)[0] not in {"P", "Z"}
    )


def parse_suno_lyrics(lyrics_text: str) -> list[LyricLine]:
    if not isinstance(lyrics_text, str) or not lyrics_text.strip():
        raise VocalPromptError("lyrics_text must contain Suno Lyrics text")
    normalized_newlines = lyrics_text.replace("\r\n", "\n").replace("\r", "\n")
    result: list[LyricLine] = []
    for source_line, raw_line in enumerate(normalized_newlines.split("\n"), start=1):
        text = raw_line.strip()
        if not text or _SECTION_HEADING_RE.fullmatch(text):
            continue
        normalized = normalize_match_text(text)
        if not normalized:
            raise VocalPromptError(
                f"Lyrics line {source_line} is empty after match normalization"
            )
        result.append(
            LyricLine(
                lyrics_index=len(result) + 1,
                source_line=source_line,
                text=text,
                normalized=normalized,
            )
        )
    if not result:
        raise VocalPromptError("lyrics_text contains no lyric lines after headings")
    return result


def _runs_from_flags(
    flags: list[bool], total_samples: int, window_samples: int
) -> list[tuple[bool, int, int]]:
    if not flags:
        return []
    runs: list[tuple[bool, int, int]] = []
    run_state = flags[0]
    run_start_frame = 0
    for frame_index in range(1, len(flags) + 1):
        state_changed = frame_index == len(flags) or flags[frame_index] != run_state
        if not state_changed:
            continue
        start_sample = run_start_frame * window_samples
        end_sample = min(frame_index * window_samples, total_samples)
        if end_sample > start_sample:
            runs.append((run_state, start_sample, end_sample))
        if frame_index < len(flags):
            run_state = flags[frame_index]
            run_start_frame = frame_index
    return runs


def detected_intervals_from_dbfs(
    window_dbfs: Iterable[float],
    *,
    total_samples: int,
    sample_rate: int,
    window_samples: int,
    silence_threshold_dbfs: float,
    min_voiced_ms: int,
    min_silence_ms: int,
    voice_padding_ms: int,
) -> list[DetectedInterval]:
    values = [float(value) for value in window_dbfs]
    if total_samples < 1 or sample_rate < 1 or window_samples < 1:
        raise VocalPromptError("VAD sample counts and sample rate must be positive")
    expected_windows = math.ceil(total_samples / window_samples)
    if len(values) != expected_windows:
        raise VocalPromptError(
            f"VAD produced {len(values)} windows; expected {expected_windows}"
        )
    if any(not math.isfinite(value) for value in values):
        raise VocalPromptError("VAD dBFS values must be finite")

    flags = [value >= silence_threshold_dbfs for value in values]
    min_silence_samples = round(min_silence_ms * sample_rate / 1000)
    runs = _runs_from_flags(flags, total_samples, window_samples)
    for run_index, (state, start, end) in enumerate(runs):
        if (
            not state
            and end - start < min_silence_samples
            and run_index > 0
            and run_index + 1 < len(runs)
            and runs[run_index - 1][0]
            and runs[run_index + 1][0]
        ):
            first_frame = start // window_samples
            final_frame = math.ceil(end / window_samples)
            flags[first_frame:final_frame] = [True] * (final_frame - first_frame)

    min_voiced_samples = round(min_voiced_ms * sample_rate / 1000)
    runs = _runs_from_flags(flags, total_samples, window_samples)
    for state, start, end in runs:
        if state and end - start < min_voiced_samples:
            first_frame = start // window_samples
            final_frame = math.ceil(end / window_samples)
            flags[first_frame:final_frame] = [False] * (final_frame - first_frame)

    padding_samples = round(voice_padding_ms * sample_rate / 1000)
    voiced: list[tuple[int, int]] = []
    for state, start, end in _runs_from_flags(flags, total_samples, window_samples):
        if not state:
            continue
        expanded_start = max(0, start - padding_samples)
        expanded_end = min(total_samples, end + padding_samples)
        if voiced and expanded_start <= voiced[-1][1]:
            voiced[-1] = (voiced[-1][0], max(voiced[-1][1], expanded_end))
        else:
            voiced.append((expanded_start, expanded_end))

    intervals: list[DetectedInterval] = []
    cursor = 0
    for start, end in voiced:
        if start > cursor:
            intervals.append(DetectedInterval("silent", cursor, start))
        intervals.append(DetectedInterval("voiced", start, end))
        cursor = end
    if cursor < total_samples:
        intervals.append(DetectedInterval("silent", cursor, total_samples))
    if not intervals:
        intervals.append(DetectedInterval("silent", 0, total_samples))
    _validate_detected_intervals(intervals, total_samples)
    return intervals


def _validate_detected_intervals(
    intervals: list[DetectedInterval], total_samples: int
) -> None:
    cursor = 0
    previous_state: str | None = None
    for interval in intervals:
        if interval.state not in _VALID_STATES:
            raise VocalPromptError(f"Unknown detected state: {interval.state!r}")
        if interval.start_sample != cursor or interval.end_sample <= interval.start_sample:
            raise VocalPromptError("Detected intervals contain a gap, overlap, or zero length")
        if previous_state == interval.state:
            raise VocalPromptError("Adjacent detected intervals have the same state")
        cursor = interval.end_sample
        previous_state = interval.state
    if cursor != total_samples:
        raise VocalPromptError("Detected intervals do not cover the complete PCM timeline")


def _window_dbfs_from_waveform(
    waveform: Any, *, total_samples: int, window_samples: int
) -> list[float]:
    torch = _import_torch()
    try:
        channels = waveform.detach()[0]
    except Exception as exc:
        raise VocalPromptError("vocal_audio.waveform is not a usable tensor") from exc

    values: list[float] = []
    chunk_samples = window_samples * _VAD_CHUNK_WINDOWS
    for chunk_start in range(0, total_samples, chunk_samples):
        chunk_end = min(total_samples, chunk_start + chunk_samples)
        try:
            chunk = channels[..., chunk_start:chunk_end].to(dtype=torch.float32)
            if not bool(torch.isfinite(chunk).all().item()):
                raise VocalPromptError("vocal_audio PCM contains NaN or infinity")
            chunk_length = chunk_end - chunk_start
            full_windows = chunk_length // window_samples
            if full_windows:
                full_length = full_windows * window_samples
                framed = chunk[..., :full_length].reshape(
                    int(chunk.shape[0]), full_windows, window_samples
                )
                rms = framed.square().mean(dim=-1).sqrt().amax(dim=0)
                dbfs = 20.0 * torch.log10(torch.clamp(rms, min=1e-12))
                values.extend(float(value) for value in dbfs.detach().cpu().tolist())
            tail_start = full_windows * window_samples
            if tail_start < chunk_length:
                tail = chunk[..., tail_start:]
                tail_rms = tail.square().mean(dim=-1).sqrt().amax()
                tail_dbfs = 20.0 * torch.log10(torch.clamp(tail_rms, min=1e-12))
                values.append(float(tail_dbfs.detach().cpu().item()))
        except VocalPromptError:
            raise
        except Exception as exc:
            raise VocalPromptError("Failed to calculate PCM VAD windows") from exc
    return values


def analyze_vocal_audio(
    waveform: Any,
    *,
    total_samples: int,
    sample_rate: int,
    silence_threshold_dbfs: float,
    analysis_window_ms: int,
    min_voiced_ms: int,
    min_silence_ms: int,
    voice_padding_ms: int,
) -> list[DetectedInterval]:
    window_samples = max(1, round(analysis_window_ms * sample_rate / 1000))
    dbfs = _window_dbfs_from_waveform(
        waveform,
        total_samples=total_samples,
        window_samples=window_samples,
    )
    loudest_window = max(dbfs)
    if loudest_window < silence_threshold_dbfs:
        LOGGER.warning(
            "[cl_vocal2promptseg] Loudest analysis window is %.2f dBFS, below "
            "the %.2f dBFS voice threshold; the timeline may be classified as silent",
            loudest_window,
            silence_threshold_dbfs,
        )
    elif loudest_window - silence_threshold_dbfs <= 1.0:
        LOGGER.warning(
            "[cl_vocal2promptseg] Loudest analysis window is only %.2f dB above "
            "the voice threshold; consider reviewing silence_threshold_dbfs",
            loudest_window - silence_threshold_dbfs,
        )
    return detected_intervals_from_dbfs(
        dbfs,
        total_samples=total_samples,
        sample_rate=sample_rate,
        window_samples=window_samples,
        silence_threshold_dbfs=silence_threshold_dbfs,
        min_voiced_ms=min_voiced_ms,
        min_silence_ms=min_silence_ms,
        voice_padding_ms=voice_padding_ms,
    )


def _prepare_whisper_audio(
    waveform: Any, *, sample_rate: int, total_samples: int
) -> Any:
    torch = _import_torch()
    try:
        mono = waveform.detach()[0].to(dtype=torch.float32).mean(dim=0)
        target_samples = max(
            1, round(total_samples * _WHISPER_SAMPLE_RATE / sample_rate)
        )
        if sample_rate != _WHISPER_SAMPLE_RATE:
            functional = importlib.import_module("torch.nn.functional")
            mono = functional.interpolate(
                mono.reshape(1, 1, total_samples),
                size=target_samples,
                mode="linear",
                align_corners=False,
            ).reshape(target_samples)
        return mono.detach().cpu().contiguous()
    except Exception as exc:
        raise VocalPromptError(
            "Failed to downmix or resample vocal_audio for Whisper"
        ) from exc


def _resolve_device(device: str) -> str:
    if device not in {"auto", "cuda", "cpu"}:
        raise VocalPromptError("device must be auto, cuda, or cpu")
    torch = _import_torch()
    cuda_available = bool(torch.cuda.is_available())
    if device == "auto":
        return "cuda" if cuda_available else "cpu"
    if device == "cuda" and not cuda_available:
        raise VocalPromptError("device=cuda was requested but CUDA is unavailable")
    return device


def _contains_voiced_midpoint(
    start: float, end: float, intervals: list[DetectedInterval], sample_rate: int
) -> bool:
    midpoint_sample = min(
        max(0, int(math.floor(((start + end) / 2.0) * sample_rate))),
        intervals[-1].end_sample - 1,
    )
    return any(
        interval.state == "voiced"
        and interval.start_sample <= midpoint_sample < interval.end_sample
        for interval in intervals
    )


def extract_whisper_words(
    result: dict[str, Any],
    *,
    audio_duration_seconds: float,
    intervals: list[DetectedInterval],
    sample_rate: int,
) -> tuple[list[WhisperWord], int, int]:
    segments = result.get("segments")
    if not isinstance(segments, list):
        raise VocalPromptError("Whisper result has no segments array")
    words: list[WhisperWord] = []
    invalid_count = 0
    source_order = 0
    had_nonempty_segment = False
    missing_word_timestamps = False
    for segment in segments:
        if not isinstance(segment, dict):
            invalid_count += 1
            continue
        segment_text = segment.get("text")
        if isinstance(segment_text, str) and segment_text.strip():
            had_nonempty_segment = True
        raw_words = segment.get("words")
        if raw_words is None:
            if isinstance(segment_text, str) and segment_text.strip():
                missing_word_timestamps = True
            continue
        if not isinstance(raw_words, list):
            invalid_count += 1
            if isinstance(segment_text, str) and segment_text.strip():
                missing_word_timestamps = True
            continue
        for raw_word in raw_words:
            source_order += 1
            if not isinstance(raw_word, dict):
                invalid_count += 1
                continue
            text = raw_word.get("word")
            start = raw_word.get("start")
            end = raw_word.get("end")
            if (
                not isinstance(text, str)
                or not text.strip()
                or not isinstance(start, (int, float))
                or isinstance(start, bool)
                or not isinstance(end, (int, float))
                or isinstance(end, bool)
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or float(start) < 0.0
                or float(end) <= float(start)
                or float(end) > audio_duration_seconds + 1e-6
            ):
                invalid_count += 1
                continue
            normalized = normalize_match_text(text)
            if not normalized:
                continue
            words.append(
                WhisperWord(
                    text=text,
                    normalized=normalized,
                    start=float(start),
                    end=min(float(end), audio_duration_seconds),
                    source_order=source_order,
                )
            )
    if missing_word_timestamps or (had_nonempty_segment and not words):
        raise VocalPromptError(
            "Whisper returned text but no usable word timestamps; "
            "word_timestamps=True is required"
        )
    words.sort(key=lambda word: (word.start, word.source_order))
    filtered: list[WhisperWord] = []
    outside_voiced_count = 0
    for word in words:
        if _contains_voiced_midpoint(word.start, word.end, intervals, sample_rate):
            filtered.append(word)
        else:
            outside_voiced_count += 1
    return filtered, invalid_count, outside_voiced_count


def levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def match_similarity(left: str, right: str) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 1.0
    return 1.0 - levenshtein_distance(left, right) / denominator


def align_lyrics(
    lyrics: list[LyricLine],
    words: list[WhisperWord],
    *,
    match_threshold: float,
    search_seconds: float,
    audio_duration_seconds: float,
) -> list[LyricAlignment]:
    cursor = 0
    previous_end_ms = 0
    audio_end_ms = math.ceil(audio_duration_seconds * 1000)
    alignments: list[LyricAlignment] = []

    for line in lyrics:
        best: tuple[float, int, int] | None = None
        minimum_length = max(1, math.floor(len(line.normalized) * 0.4))
        maximum_length = math.ceil(len(line.normalized) * 2.5) + 8
        if cursor < len(words):
            search_origin = words[cursor].start
            for start_index in range(cursor, len(words)):
                first = words[start_index]
                if first.start - search_origin > search_seconds:
                    break
                candidate = ""
                for end_index in range(start_index, len(words)):
                    word = words[end_index]
                    if word.end - first.start > search_seconds:
                        break
                    candidate += word.normalized
                    candidate_length = len(candidate)
                    if candidate_length > maximum_length:
                        break
                    if candidate_length < minimum_length:
                        continue
                    score = match_similarity(line.normalized, candidate)
                    proposal = (score, start_index, end_index)
                    if best is None:
                        best = proposal
                    elif score > best[0] + 1e-12:
                        best = proposal
                    elif abs(score - best[0]) <= 1e-12:
                        best_word_count = best[2] - best[1] + 1
                        proposal_word_count = end_index - start_index + 1
                        if start_index < best[1] or (
                            start_index == best[1]
                            and proposal_word_count < best_word_count
                        ):
                            best = proposal

        if best is None or best[0] < match_threshold:
            alignments.append(
                LyricAlignment(
                    line=line,
                    status="unresolved",
                    match_score=0.0 if best is None else round(best[0], 6),
                    whisper_text="",
                    start_ms=None,
                    end_ms=None,
                )
            )
            continue

        score, start_index, end_index = best
        raw_start_ms = math.floor(words[start_index].start * 1000)
        raw_end_ms = math.ceil(words[end_index].end * 1000)
        start_ms = max(raw_start_ms, previous_end_ms)
        end_ms = min(audio_end_ms, max(start_ms + 1, raw_end_ms))
        if end_ms <= start_ms:
            alignments.append(
                LyricAlignment(
                    line=line,
                    status="unresolved",
                    match_score=round(score, 6),
                    whisper_text="",
                    start_ms=None,
                    end_ms=None,
                )
            )
            continue
        whisper_text = "".join(
            word.text for word in words[start_index : end_index + 1]
        ).strip()
        alignments.append(
            LyricAlignment(
                line=line,
                status="resolved",
                match_score=round(score, 6),
                whisper_text=whisper_text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
        cursor = end_index + 1
        previous_end_ms = end_ms
    return alignments


def build_scenes(
    intervals: list[DetectedInterval],
    *,
    total_samples: int,
    sample_rate: int,
    max_scene_seconds: int,
) -> tuple[list[dict[str, Any]], int, float]:
    timeline_seconds = math.ceil(total_samples / sample_rate)
    trailing_padding = timeline_seconds - total_samples / sample_rate
    quantized_voiced: list[tuple[int, int]] = []
    for interval in intervals:
        if interval.state != "voiced":
            continue
        start = interval.start_sample // sample_rate
        end = math.ceil(interval.end_sample / sample_rate)
        start = min(max(0, start), timeline_seconds)
        end = min(max(start, end), timeline_seconds)
        if end <= start:
            continue
        if quantized_voiced and start <= quantized_voiced[-1][1]:
            quantized_voiced[-1] = (
                quantized_voiced[-1][0],
                max(quantized_voiced[-1][1], end),
            )
        else:
            quantized_voiced.append((start, end))

    ranges: list[tuple[str, int, int]] = []
    cursor = 0
    for start, end in quantized_voiced:
        if start > cursor:
            ranges.append(("silent", cursor, start))
        ranges.append(("voiced", start, end))
        cursor = end
    if cursor < timeline_seconds:
        ranges.append(("silent", cursor, timeline_seconds))
    if not ranges:
        ranges.append(("silent", 0, timeline_seconds))

    scenes: list[dict[str, Any]] = []
    for state, start, end in ranges:
        length = end - start
        scene_count = math.ceil(length / max_scene_seconds)
        base_length = length // scene_count
        remainder = length % scene_count
        scene_start = start
        for part_index in range(scene_count):
            scene_length = base_length + (1 if part_index < remainder else 0)
            scene_end = scene_start + scene_length
            scenes.append(
                {
                    "index": len(scenes) + 1,
                    "state": state,
                    "start_seconds": scene_start,
                    "end_seconds": scene_end,
                    "duration_seconds": scene_length,
                    "lyrics_indices": [],
                }
            )
            scene_start = scene_end
    if len(scenes) > 128:
        raise VocalPromptError(
            f"Generated {len(scenes)} scenes; the current compiler supports at most 128"
        )
    if not scenes or scenes[0]["start_seconds"] != 0:
        raise VocalPromptError("Scene plan does not start at zero")
    if scenes[-1]["end_seconds"] != timeline_seconds:
        raise VocalPromptError("Scene plan does not cover the complete timeline")
    for scene in scenes:
        if not 1 <= scene["duration_seconds"] <= max_scene_seconds:
            raise VocalPromptError("Scene duration is outside the requested range")
    return scenes, timeline_seconds, trailing_padding


def assign_lyrics_to_scenes(
    alignments: list[LyricAlignment], scenes: list[dict[str, Any]]
) -> list[LyricAlignment]:
    assigned: list[LyricAlignment] = []
    for alignment in alignments:
        scene_index: int | None = None
        if alignment.status == "resolved" and alignment.start_ms is not None:
            for scene in scenes:
                if (
                    scene["start_seconds"] * 1000
                    <= alignment.start_ms
                    < scene["end_seconds"] * 1000
                ):
                    scene_index = int(scene["index"])
                    scene["lyrics_indices"].append(alignment.line.lyrics_index)
                    break
            if scene_index is None:
                raise VocalPromptError(
                    f"Resolved Lyrics line {alignment.line.lyrics_index} is outside the scene plan"
                )
        assigned.append(
            LyricAlignment(
                line=alignment.line,
                status=alignment.status,
                match_score=alignment.match_score,
                whisper_text=alignment.whisper_text,
                start_ms=alignment.start_ms,
                end_ms=alignment.end_ms,
                scene_index=scene_index,
            )
        )
    return assigned


def _source_timestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_prompt_text(
    scenes: list[dict[str, Any]], alignments: list[LyricAlignment]
) -> str:
    lyrics_by_scene: dict[int, list[str]] = {}
    for alignment in alignments:
        if alignment.status == "resolved" and alignment.scene_index is not None:
            lyrics_by_scene.setdefault(alignment.scene_index, []).append(
                alignment.line.text
            )

    lines = [
        "# サブジェクト",
        "// 次の行を、必要な外観参照と人物説明を含むSubject 1の定義へ編集できます。",
        "* 人物。",
        "",
        "# 共通プロンプト",
        "// 次の行を、全Sceneに共通する画風、背景、照明及び制約へ編集できます。",
        "* 全シーンで一貫した画風、照明、背景及び人物の外観を維持する。",
    ]
    for scene in scenes:
        lines.append("")
        continuation = "" if scene["index"] == 1 else " 継続"
        lines.append(
            f"# シーン {scene['duration_seconds']}秒{continuation}"
        )
        source_start = _source_timestamp(scene["start_seconds"] * 1000)
        source_end = _source_timestamp(scene["end_seconds"] * 1000)
        lines.append(
            f"// 検出状態: {scene['state']}。ソース範囲 {source_start}-{source_end}。"
        )
        for lyric in lyrics_by_scene.get(scene["index"], []):
            lines.append(f"// {lyric}")
        lines.extend(
            [
                "## ショット",
                "// 次の行を、この区間の具体的な人物動作とカメラワークへ編集できます。",
            ]
        )
        if scene["state"] == "voiced":
            lines.extend(
                [
                    "* <Subject 1>はソースボーカルの抑揚に合わせて自然に演技する。",
                    "* リップシンク: <Subject 1> <- ソースボーカル",
                    "## 音響",
                    "* 発声: ソースボーカルのみ",
                    "* ソース音声: 完全維持",
                ]
            )
        else:
            lines.extend(
                [
                    "* <Subject 1>は口を閉じ、発話を示す口の動きを行わず、自然に演技する。",
                    "## 音響",
                    "* 発声: なし",
                    "* ソース音声: 完全維持",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def build_srt_text(alignments: list[LyricAlignment]) -> str:
    blocks: list[str] = []
    for alignment in alignments:
        if (
            alignment.status != "resolved"
            or alignment.start_ms is None
            or alignment.end_ms is None
        ):
            continue
        blocks.append(
            "\n".join(
                [
                    str(len(blocks) + 1),
                    f"{_srt_timestamp(alignment.start_ms)} --> "
                    f"{_srt_timestamp(alignment.end_ms)}",
                    alignment.line.text,
                ]
            )
        )
    text = "" if not blocks else "\n\n".join(blocks) + "\n\n"
    _validate_srt_text(text)
    return text


_SRT_TIMING_RE = re.compile(
    r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})$"
)


def _srt_parts_to_ms(parts: tuple[str, str, str, str]) -> int:
    hours, minutes, seconds, milliseconds = (int(value) for value in parts)
    if minutes > 59 or seconds > 59:
        raise VocalPromptError("Generated SRT contains an invalid timestamp")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _validate_srt_text(text: str) -> None:
    if not text:
        return
    if not text.endswith("\n\n") or "\r" in text:
        raise VocalPromptError("Generated SRT has invalid line endings")
    blocks = text[:-2].split("\n\n")
    previous_end = 0
    for expected_number, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) != 3 or lines[0] != str(expected_number) or not lines[2]:
            raise VocalPromptError("Generated SRT has an invalid entry structure")
        timing = _SRT_TIMING_RE.fullmatch(lines[1])
        if timing is None:
            raise VocalPromptError("Generated SRT has an invalid timestamp format")
        start_ms = _srt_parts_to_ms(timing.groups()[:4])
        end_ms = _srt_parts_to_ms(timing.groups()[4:])
        if start_ms < previous_end or end_ms <= start_ms:
            raise VocalPromptError(
                "Generated SRT timestamps overlap or are not increasing"
            )
        previous_end = end_ms


def _interval_json(interval: DetectedInterval, sample_rate: int) -> dict[str, Any]:
    return {
        "state": interval.state,
        "start_sample": interval.start_sample,
        "end_sample": interval.end_sample,
        "start_seconds": round(interval.start_sample / sample_rate, 6),
        "end_seconds": round(interval.end_sample / sample_rate, 6),
        "duration_seconds": round(
            (interval.end_sample - interval.start_sample) / sample_rate, 6
        ),
    }


def _alignment_json(alignment: LyricAlignment) -> dict[str, Any]:
    return {
        "lyrics_index": alignment.line.lyrics_index,
        "source_line": alignment.line.source_line,
        "text": alignment.line.text,
        "status": alignment.status,
        "match_score": alignment.match_score,
        "whisper_text": alignment.whisper_text,
        "start_ms": alignment.start_ms,
        "end_ms": alignment.end_ms,
        "scene_index": alignment.scene_index,
    }


def build_segments_json(
    *,
    sample_rate: int,
    total_samples: int,
    timeline_seconds: int,
    trailing_padding: float,
    whisper_model: str,
    language_requested: str,
    language_detected: str | None,
    device: str,
    settings: dict[str, Any],
    intervals: list[DetectedInterval],
    alignments: list[LyricAlignment],
    scenes: list[dict[str, Any]],
) -> str:
    payload = {
        "schema_version": 2,
        "sample_rate": sample_rate,
        "total_samples": total_samples,
        "audio_duration_seconds": round(total_samples / sample_rate, 6),
        "timeline_duration_seconds": timeline_seconds,
        "trailing_padding_seconds": round(trailing_padding, 6),
        "whisper": {
            "model": whisper_model,
            "language_requested": language_requested,
            "language_detected": language_detected,
            "device": device,
        },
        "settings": settings,
        "detected_intervals": [
            _interval_json(interval, sample_rate) for interval in intervals
        ],
        "lyrics": [_alignment_json(alignment) for alignment in alignments],
        "scenes": scenes,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise VocalPromptError("Generated segments_json is invalid") from exc
    return text


class _InferenceHeartbeat:
    def __init__(self, model_name: str, interval_seconds: float = 10.0) -> None:
        self.model_name = model_name
        self.interval_seconds = interval_seconds
        self.started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_InferenceHeartbeat":
        self.started = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            LOGGER.info(
                "[cl_vocal2promptseg] Whisper inference active: model=%s elapsed=%.1fs",
                self.model_name,
                time.monotonic() - self.started,
            )

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class CLVocalToPromptSegments:
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt_text", "srt_text", "segments_json", "status")
    FUNCTION = "build_prompt_segments"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    def __init__(self) -> None:
        self._backend = WhisperBackend()
        self._lock = threading.RLock()

    @classmethod
    def discover_model_names(cls) -> list[str]:
        return discover_whisper_model_names()

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        model_names = cls.discover_model_names()
        return {
            "required": {
                "vocal_audio": (
                    "AUDIO",
                    {
                        "tooltip": "Full-length vocal stem aligned to the locked source full mix.",
                    },
                ),
                "lyrics_text": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Suno Lyrics text supplied by a STRING output. [Section] headings are ignored.",
                    },
                ),
                "whisper_model": (
                    model_names,
                    {
                        "default": model_names[0],
                        "tooltip": "Local OpenAI Whisper .pt checkpoint below ComfyUI/models/whisper. No model is downloaded automatically.",
                    },
                ),
                "language": (["ja", "auto"], {"default": "ja"}),
                "device": (["auto", "cuda", "cpu"], {"default": "auto"}),
                "keep_whisper_loaded": ("BOOLEAN", {"default": True}),
                "max_scene_seconds": (
                    "INT",
                    {"default": 10, "min": 1, "max": 60, "step": 1},
                ),
                "silence_threshold_dbfs": (
                    "FLOAT",
                    {
                        "default": -45.0,
                        "min": -100.0,
                        "max": 0.0,
                        "step": 0.5,
                    },
                ),
                "analysis_window_ms": (
                    "INT",
                    {"default": 20, "min": 5, "max": 200, "step": 1},
                ),
                "min_voiced_ms": (
                    "INT",
                    {"default": 120, "min": 0, "max": 5000, "step": 10},
                ),
                "min_silence_ms": (
                    "INT",
                    {"default": 300, "min": 0, "max": 10000, "step": 10},
                ),
                "voice_padding_ms": (
                    "INT",
                    {"default": 80, "min": 0, "max": 2000, "step": 10},
                ),
                "lyrics_match_threshold": (
                    "FLOAT",
                    {"default": 0.55, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "lyrics_search_seconds": (
                    "FLOAT",
                    {"default": 60.0, "min": 1.0, "max": 600.0, "step": 1.0},
                ),
            }
        }

    @classmethod
    def IS_CHANGED(cls, whisper_model: str, **_: Any) -> tuple[Any, ...]:
        try:
            path = resolve_whisper_model_name(whisper_model)
            stat = path.stat()
            return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        except Exception as exc:
            digest = hashlib.sha256(
                f"{whisper_model}|{type(exc).__name__}|{exc}".encode(
                    "utf-8", "replace"
                )
            ).hexdigest()
            return ("whisper-model-resolution-error", whisper_model, digest)

    def clear_model(self) -> None:
        self._backend.clear_model()

    def build_prompt_segments(
        self,
        vocal_audio: dict[str, Any],
        lyrics_text: str,
        whisper_model: str,
        language: str,
        device: str,
        keep_whisper_loaded: bool,
        max_scene_seconds: int,
        silence_threshold_dbfs: float,
        analysis_window_ms: int,
        min_voiced_ms: int,
        min_silence_ms: int,
        voice_padding_ms: int,
        lyrics_match_threshold: float,
        lyrics_search_seconds: float,
    ) -> tuple[str, str, str, str]:
        with self._lock:
            _validate_bool(keep_whisper_loaded, "keep_whisper_loaded")
            max_scene_seconds = _validate_int(
                max_scene_seconds, "max_scene_seconds", 1, 60
            )
            silence_threshold_dbfs = _validate_float(
                silence_threshold_dbfs, "silence_threshold_dbfs", -100.0, 0.0
            )
            analysis_window_ms = _validate_int(
                analysis_window_ms, "analysis_window_ms", 5, 200
            )
            min_voiced_ms = _validate_int(
                min_voiced_ms, "min_voiced_ms", 0, 5000
            )
            min_silence_ms = _validate_int(
                min_silence_ms, "min_silence_ms", 0, 10000
            )
            voice_padding_ms = _validate_int(
                voice_padding_ms, "voice_padding_ms", 0, 2000
            )
            lyrics_match_threshold = _validate_float(
                lyrics_match_threshold, "lyrics_match_threshold", 0.0, 1.0
            )
            lyrics_search_seconds = _validate_float(
                lyrics_search_seconds, "lyrics_search_seconds", 1.0, 600.0
            )
            if language not in {"ja", "auto"}:
                raise VocalPromptError("language must be ja or auto")
            if not isinstance(whisper_model, str) or not whisper_model:
                raise VocalPromptError("whisper_model must be a model ID")

            lyrics = parse_suno_lyrics(lyrics_text)
            waveform, sample_rate, _, total_samples = _validate_audio(vocal_audio)
            resolved_model = resolve_whisper_model_name(whisper_model)
            resolved_device = _resolve_device(device)
            progress = _ComfyProgressBar(5) if _ComfyProgressBar is not None else None

            try:
                intervals = analyze_vocal_audio(
                    waveform,
                    total_samples=total_samples,
                    sample_rate=sample_rate,
                    silence_threshold_dbfs=silence_threshold_dbfs,
                    analysis_window_ms=analysis_window_ms,
                    min_voiced_ms=min_voiced_ms,
                    min_silence_ms=min_silence_ms,
                    voice_padding_ms=voice_padding_ms,
                )
                interval_states = {interval.state for interval in intervals}
                if "voiced" not in interval_states:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] No voiced interval was detected; "
                        "all generated scenes will disable vocalization"
                    )
                if "silent" not in interval_states:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] No silent interval was detected; "
                        "all generated scenes will use Source Vocal lip-sync"
                    )
                if progress is not None:
                    progress.update_absolute(1, 5)
                scenes, timeline_seconds, trailing_padding = build_scenes(
                    intervals,
                    total_samples=total_samples,
                    sample_rate=sample_rate,
                    max_scene_seconds=max_scene_seconds,
                )
                whisper_audio = _prepare_whisper_audio(
                    waveform,
                    sample_rate=sample_rate,
                    total_samples=total_samples,
                )
                if progress is not None:
                    progress.update_absolute(2, 5)
                self._backend.ensure_loaded(resolved_model, resolved_device)
                if progress is not None:
                    progress.update_absolute(3, 5)
                LOGGER.info(
                    "[cl_vocal2promptseg] Starting Whisper transcription: "
                    "model=%s device=%s duration=%.3fs",
                    whisper_model,
                    resolved_device,
                    total_samples / sample_rate,
                )
                with _InferenceHeartbeat(whisper_model):
                    whisper_result = self._backend.transcribe(
                        whisper_audio,
                        language=None if language == "auto" else language,
                        device=resolved_device,
                    )
                if progress is not None:
                    progress.update_absolute(4, 5)

                words, invalid_words, outside_words = extract_whisper_words(
                    whisper_result,
                    audio_duration_seconds=total_samples / sample_rate,
                    intervals=intervals,
                    sample_rate=sample_rate,
                )
                if invalid_words:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] Ignored %d Whisper word or segment "
                        "value(s) with invalid structure or timestamps",
                        invalid_words,
                    )
                if outside_words:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] Ignored %d Whisper word(s) outside "
                        "detected voiced intervals",
                        outside_words,
                    )
                alignments = align_lyrics(
                    lyrics,
                    words,
                    match_threshold=lyrics_match_threshold,
                    search_seconds=lyrics_search_seconds,
                    audio_duration_seconds=total_samples / sample_rate,
                )
                alignments = assign_lyrics_to_scenes(alignments, scenes)
                prompt_text = build_prompt_text(scenes, alignments)
                try:
                    lex_japanese_markdown(prompt_text)
                except Exception as exc:
                    raise VocalPromptError(
                        "Generated prompt_text failed reduced-Markdown validation"
                    ) from exc
                srt_text = build_srt_text(alignments)
                settings = {
                    "max_scene_seconds": max_scene_seconds,
                    "silence_threshold_dbfs": silence_threshold_dbfs,
                    "analysis_window_ms": analysis_window_ms,
                    "min_voiced_ms": min_voiced_ms,
                    "min_silence_ms": min_silence_ms,
                    "voice_padding_ms": voice_padding_ms,
                    "lyrics_match_threshold": lyrics_match_threshold,
                    "lyrics_search_seconds": lyrics_search_seconds,
                }
                detected_language = whisper_result.get("language")
                if detected_language is not None and not isinstance(
                    detected_language, str
                ):
                    detected_language = str(detected_language)
                segments_json = build_segments_json(
                    sample_rate=sample_rate,
                    total_samples=total_samples,
                    timeline_seconds=timeline_seconds,
                    trailing_padding=trailing_padding,
                    whisper_model=whisper_model,
                    language_requested=language,
                    language_detected=detected_language,
                    device=resolved_device,
                    settings=settings,
                    intervals=intervals,
                    alignments=alignments,
                    scenes=scenes,
                )
                resolved_count = sum(
                    alignment.status == "resolved" for alignment in alignments
                )
                unresolved_count = len(alignments) - resolved_count
                voiced_scene_count = sum(
                    scene["state"] == "voiced" for scene in scenes
                )
                voiced_interval_count = sum(
                    interval.state == "voiced" for interval in intervals
                )
                if unresolved_count:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] Could not align %d/%d Lyrics line(s); "
                        "unresolved lines were omitted from comments and SRT",
                        unresolved_count,
                        len(alignments),
                    )
                if resolved_count == 0:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] No Lyrics lines were resolved; "
                        "returning an empty SRT"
                    )
                if trailing_padding > 1e-9:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] Timeline requires %.6fs of end "
                        "padding; use CL Audio Pad with pad_position=end",
                        trailing_padding,
                    )
                status = (
                    f"analyzed {total_samples} samples at {sample_rate} Hz "
                    f"({total_samples / sample_rate:.6f}s); "
                    f"whisper={whisper_model} on {resolved_device} "
                    f"language={detected_language or language}; "
                    f"lyrics={resolved_count} resolved, {unresolved_count} unresolved; "
                    f"detected {voiced_interval_count} voiced interval(s); "
                    f"generated {len(scenes)} scene(s): {voiced_scene_count} voiced, "
                    f"{len(scenes) - voiced_scene_count} silent; "
                    f"timeline={timeline_seconds}s; "
                    f"end padding required={trailing_padding:.6f}s"
                )
                LOGGER.info("[cl_vocal2promptseg] %s", status)
                if progress is not None:
                    progress.update_absolute(5, 5)
                return prompt_text, srt_text, segments_json, status
            except Exception:
                self.clear_model()
                raise
            finally:
                if not keep_whisper_loaded:
                    self.clear_model()
