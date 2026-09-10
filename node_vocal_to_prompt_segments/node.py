"""PCM vocal analysis, Whisper lyrics alignment, and prompt templates."""

from __future__ import annotations

from dataclasses import dataclass, replace
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

from ..common.logging import ANSI_RESET, log_cyan
from ..common.suno import SECTION_HEADING_RE, classify_suno_section
from .errors import VocalPromptError
from ..node_japanese_to_json.compiler.llmj2e import lex_japanese_markdown
from .whisper_runtime import WhisperBackend
from .whisper_discovery import (
    discover_whisper_model_names,
    resolve_whisper_model_name,
)


LOGGER = logging.getLogger("cl_vocal2promptseg")
_VALID_STATES = {"silent", "voiced"}
_WHISPER_SAMPLE_RATE = 16_000
_VAD_CHUNK_WINDOWS = 2_048
_WHISPER_LANGUAGE_OPTIONS = ("ja", "en", "auto")
_WHISPER_INITIAL_PROMPT_MAX_LINES = 12
_WHISPER_INITIAL_PROMPT_MAX_CHARACTERS = 160
_POST_ANCHOR_SEARCH_SECONDS = 20.0
_RESYNC_MINIMUM_SCORE = 0.8
_RESYNC_LOOKAHEAD_LINES = 3
_REPEATED_LINE_LOOKAHEAD_LINES = 4
_FOLLOWING_LINE_COLLISION_MARGIN = 0.15
_TARGETED_RETRY_MINIMUM_SECONDS = 0.1
_TARGETED_RETRY_WINDOW_SECONDS = 12.0
_TARGETED_RETRY_WINDOW_OVERLAP_SECONDS = 2.0
_TARGETED_RETRY_DUPLICATE_CENTER_SECONDS = 0.35
_MIN_CHAINABLE_SCENE_SECONDS = 2
_SRT_TIME_OFFSET_MIN = -(2**31)
_SRT_TIME_OFFSET_MAX = 2**31 - 1
_ANSI_RED = "\x1b[91m"

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
    section_label: str | None = None
    section_kind: str | None = None


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
    match_method: str | None = None
    candidate_whisper_text: str = ""
    candidate_start_ms: int | None = None
    candidate_end_ms: int | None = None


@dataclass(frozen=True)
class _WordCandidate:
    score: float
    start_index: int
    end_index: int


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
    section_label: str | None = None
    section_kind: str | None = None
    for source_line, raw_line in enumerate(normalized_newlines.split("\n"), start=1):
        text = raw_line.strip()
        if not text:
            continue
        if SECTION_HEADING_RE.fullmatch(text):
            section_label = text
            try:
                section_kind = classify_suno_section(text)
            except ValueError as exc:  # defensive: regex already matched
                raise VocalPromptError(str(exc)) from exc
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
                section_label=section_label,
                section_kind=section_kind,
            )
        )
    if not result:
        raise VocalPromptError("lyrics_text contains no lyric lines after headings")
    return result


def build_whisper_initial_prompt(lyrics: list[LyricLine]) -> str:
    """Build a bounded leading-Lyrics hint for Whisper's first decode window."""

    selected: list[str] = []
    for line in lyrics[:_WHISPER_INITIAL_PROMPT_MAX_LINES]:
        candidate = "\n".join([*selected, line.text])
        if len(candidate) <= _WHISPER_INITIAL_PROMPT_MAX_CHARACTERS:
            selected.append(line.text)
            continue
        if not selected:
            selected.append(line.text[:_WHISPER_INITIAL_PROMPT_MAX_CHARACTERS])
        break
    return "\n".join(selected)


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


def _word_candidates(
    line: LyricLine,
    words: list[WhisperWord],
    *,
    start_index: int,
    end_index: int,
    search_seconds: float,
) -> list[_WordCandidate]:
    if start_index >= end_index or start_index >= len(words):
        return []
    end_index = min(end_index, len(words))
    search_origin = words[start_index].start
    minimum_length = max(1, math.floor(len(line.normalized) * 0.4))
    maximum_length = math.ceil(len(line.normalized) * 2.5) + 8
    candidates: list[_WordCandidate] = []
    for candidate_start in range(start_index, end_index):
        first = words[candidate_start]
        if first.start - search_origin > search_seconds:
            break
        candidate_text = ""
        for candidate_end in range(candidate_start, end_index):
            word = words[candidate_end]
            if word.end - first.start > search_seconds:
                break
            candidate_text += word.normalized
            candidate_length = len(candidate_text)
            if candidate_length > maximum_length:
                break
            if candidate_length < minimum_length:
                continue
            candidates.append(
                _WordCandidate(
                    match_similarity(line.normalized, candidate_text),
                    candidate_start,
                    candidate_end,
                )
            )
    return candidates


def _best_word_candidate(
    line: LyricLine,
    words: list[WhisperWord],
    *,
    start_index: int,
    end_index: int,
    search_seconds: float,
) -> _WordCandidate | None:
    best: _WordCandidate | None = None
    for proposal in _word_candidates(
        line,
        words,
        start_index=start_index,
        end_index=end_index,
        search_seconds=search_seconds,
    ):
        if best is None or proposal.score > best.score + 1e-12:
            best = proposal
            continue
        if abs(proposal.score - best.score) > 1e-12:
            continue
        best_word_count = best.end_index - best.start_index + 1
        proposal_word_count = proposal.end_index - proposal.start_index + 1
        if proposal.start_index < best.start_index or (
            proposal.start_index == best.start_index
            and proposal_word_count < best_word_count
        ):
            best = proposal
    return best


def _best_supported_repeated_candidate(
    lyrics: list[LyricLine],
    line_index: int,
    words: list[WhisperWord],
    *,
    start_index: int,
    end_index: int,
    match_threshold: float,
    search_seconds: float,
) -> _WordCandidate | None:
    """Disambiguate repeated refrains by testing the Lyrics that follow them."""
    following = lyrics[
        line_index + 1 : line_index + 1 + _REPEATED_LINE_LOOKAHEAD_LINES
    ]
    if not following:
        return _best_word_candidate(
            lyrics[line_index],
            words,
            start_index=start_index,
            end_index=end_index,
            search_seconds=search_seconds,
        )

    best: _WordCandidate | None = None
    best_rank: tuple[int, float, int, float] | None = None
    for candidate in _word_candidates(
        lyrics[line_index],
        words,
        start_index=start_index,
        end_index=end_index,
        search_seconds=search_seconds,
    ):
        if candidate.score < match_threshold:
            continue
        support_cursor = candidate.end_index + 1
        support_count = 0
        support_score = 0.0
        for following_offset, next_line in enumerate(following):
            support = _best_word_candidate(
                next_line,
                words,
                start_index=support_cursor,
                end_index=end_index,
                search_seconds=search_seconds,
            )
            if support is None or support.score < match_threshold:
                if following_offset == 0:
                    break
                continue
            support_count += 1
            support_score += support.score
            support_cursor = support.end_index + 1
        if support_count == 0:
            continue
        # Prefer the candidate which preserves the greatest number of following
        # Lyrics lines. For an equal sequence fit, the earlier refrain is the
        # safe choice; otherwise a later identical chorus can swallow a block.
        rank = (
            support_count,
            support_score,
            -candidate.start_index,
            candidate.score,
        )
        if best_rank is None or rank > best_rank:
            best = candidate
            best_rank = rank
    return best


def _candidate_better_fits_following_line(
    lyrics: list[LyricLine],
    line_index: int,
    candidate: _WordCandidate,
    words: list[WhisperWord],
) -> bool:
    if line_index + 1 >= len(lyrics):
        return False
    current = lyrics[line_index]
    following = lyrics[line_index + 1]
    if current.normalized == following.normalized:
        return False
    candidate_normalized = "".join(
        word.normalized
        for word in words[candidate.start_index : candidate.end_index + 1]
    )
    following_score = match_similarity(following.normalized, candidate_normalized)
    return following_score >= candidate.score + _FOLLOWING_LINE_COLLISION_MARGIN


def _candidate_text(candidate: _WordCandidate, words: list[WhisperWord]) -> str:
    return "".join(
        word.text for word in words[candidate.start_index : candidate.end_index + 1]
    ).strip()


def _unresolved_alignment(
    line: LyricLine,
    candidate: _WordCandidate | None,
    words: list[WhisperWord],
    *,
    audio_end_ms: int,
) -> LyricAlignment:
    if candidate is None:
        return LyricAlignment(line, "unresolved", 0.0, "", None, None)
    return LyricAlignment(
        line=line,
        status="unresolved",
        match_score=round(candidate.score, 6),
        whisper_text="",
        start_ms=None,
        end_ms=None,
        candidate_whisper_text=_candidate_text(candidate, words),
        candidate_start_ms=math.floor(words[candidate.start_index].start * 1000),
        candidate_end_ms=min(
            audio_end_ms, math.ceil(words[candidate.end_index].end * 1000)
        ),
    )


def _resolved_alignment(
    line: LyricLine,
    candidate: _WordCandidate,
    words: list[WhisperWord],
    *,
    previous_end_ms: int,
    maximum_end_ms: int,
    match_method: str,
) -> LyricAlignment | None:
    raw_start_ms = math.floor(words[candidate.start_index].start * 1000)
    raw_end_ms = math.ceil(words[candidate.end_index].end * 1000)
    start_ms = max(raw_start_ms, previous_end_ms)
    end_ms = min(maximum_end_ms, max(start_ms + 1, raw_end_ms))
    if end_ms <= start_ms:
        return None
    return LyricAlignment(
        line=line,
        status="resolved",
        match_score=round(candidate.score, 6),
        whisper_text=_candidate_text(candidate, words),
        start_ms=start_ms,
        end_ms=end_ms,
        match_method=match_method,
    )


def _has_resync_support(
    lyrics: list[LyricLine],
    line_index: int,
    candidate: _WordCandidate,
    words: list[WhisperWord],
    *,
    match_threshold: float,
    search_seconds: float,
) -> bool:
    support_start = candidate.end_index + 1
    if support_start >= len(words):
        return False
    for next_line in lyrics[
        line_index + 1 : line_index + 1 + _RESYNC_LOOKAHEAD_LINES
    ]:
        support = _best_word_candidate(
            next_line,
            words,
            start_index=support_start,
            end_index=len(words),
            search_seconds=min(search_seconds, _POST_ANCHOR_SEARCH_SECONDS),
        )
        if support is not None and support.score >= match_threshold:
            return True
    return False


def align_lyrics(
    lyrics: list[LyricLine],
    words: list[WhisperWord],
    *,
    match_threshold: float,
    neighbor_match_threshold: float = 0.45,
    search_seconds: float,
    audio_duration_seconds: float,
) -> list[LyricAlignment]:
    cursor = 0
    previous_end_ms = 0
    audio_end_ms = math.ceil(audio_duration_seconds * 1000)
    alignments: list[LyricAlignment] = []
    resolved_spans: list[tuple[int, int] | None] = []
    has_primary_anchor = False
    normalized_occurrences: dict[str, int] = {}
    for line in lyrics:
        normalized_occurrences[line.normalized] = (
            normalized_occurrences.get(line.normalized, 0) + 1
        )
    resynchronized = 0

    for line_index, line in enumerate(lyrics):
        effective_search_seconds = (
            search_seconds
            if not has_primary_anchor
            else min(search_seconds, _POST_ANCHOR_SEARCH_SECONDS)
        )
        best = _best_word_candidate(
            line,
            words,
            start_index=cursor,
            end_index=len(words),
            search_seconds=effective_search_seconds,
        )
        if normalized_occurrences[line.normalized] > 1:
            supported_repeated = _best_supported_repeated_candidate(
                lyrics,
                line_index,
                words,
                start_index=cursor,
                end_index=len(words),
                match_threshold=match_threshold,
                search_seconds=effective_search_seconds,
            )
            if supported_repeated is not None:
                best = supported_repeated
            elif best is not None and best.score >= match_threshold:
                # Keep the candidate in diagnostics, but do not advance over an
                # ambiguous repeated refrain without any following-line support.
                alignments.append(
                    _unresolved_alignment(
                        line, best, words, audio_end_ms=audio_end_ms
                    )
                )
                resolved_spans.append(None)
                continue
        if (
            best is not None
            and best.score >= match_threshold
            and _candidate_better_fits_following_line(
                lyrics, line_index, best, words
            )
        ):
            # Whisper may omit one harshly sung line and return the following
            # lyric instead. Do not consume that stronger following-line anchor,
            # otherwise every later line in the verse can shift forward.
            alignments.append(
                _unresolved_alignment(line, best, words, audio_end_ms=audio_end_ms)
            )
            resolved_spans.append(None)
            continue
        match_method = "primary"
        if (
            has_primary_anchor
            and effective_search_seconds < search_seconds
            and (best is None or best.score < match_threshold)
            and normalized_occurrences[line.normalized] == 1
            and len(line.normalized) >= 8
        ):
            broad_candidate = _best_word_candidate(
                line,
                words,
                start_index=cursor,
                end_index=len(words),
                search_seconds=search_seconds,
            )
            resync_threshold = max(match_threshold, _RESYNC_MINIMUM_SCORE)
            if (
                broad_candidate is not None
                and broad_candidate.score >= resync_threshold
                and _has_resync_support(
                    lyrics,
                    line_index,
                    broad_candidate,
                    words,
                    match_threshold=match_threshold,
                    search_seconds=search_seconds,
                )
            ):
                best = broad_candidate
                match_method = "resync"
        if best is None or best.score < match_threshold:
            alignments.append(
                _unresolved_alignment(line, best, words, audio_end_ms=audio_end_ms)
            )
            resolved_spans.append(None)
            continue
        resolved = _resolved_alignment(
            line,
            best,
            words,
            previous_end_ms=previous_end_ms,
            maximum_end_ms=audio_end_ms,
            match_method=match_method,
        )
        if resolved is None:
            alignments.append(
                _unresolved_alignment(line, best, words, audio_end_ms=audio_end_ms)
            )
            resolved_spans.append(None)
            continue
        alignments.append(resolved)
        resolved_spans.append((best.start_index, best.end_index))
        cursor = best.end_index + 1
        previous_end_ms = resolved.end_ms or previous_end_ms
        has_primary_anchor = True
        if match_method == "resync":
            resynchronized += 1

    recovered = 0
    previous_anchor: int | None = None
    for next_anchor, alignment in enumerate(alignments):
        if alignment.status != "resolved":
            continue
        if previous_anchor is not None and next_anchor > previous_anchor + 1:
            previous_span = resolved_spans[previous_anchor]
            next_span = resolved_spans[next_anchor]
            if previous_span is None or next_span is None:  # pragma: no cover
                raise VocalPromptError("Resolved Lyrics alignment has no word span")
            gap_cursor = previous_span[1] + 1
            gap_end = next_span[0]
            neighbor_previous_end = alignments[previous_anchor].end_ms or 0
            neighbor_maximum_end = alignments[next_anchor].start_ms or audio_end_ms
            for pending_index in range(previous_anchor + 1, next_anchor):
                line = alignments[pending_index].line
                candidate = _best_word_candidate(
                    line,
                    words,
                    start_index=gap_cursor,
                    end_index=gap_end,
                    search_seconds=search_seconds,
                )
                alignments[pending_index] = _unresolved_alignment(
                    line, candidate, words, audio_end_ms=audio_end_ms
                )
                if candidate is None or candidate.score < neighbor_match_threshold:
                    continue
                resolved = _resolved_alignment(
                    line,
                    candidate,
                    words,
                    previous_end_ms=neighbor_previous_end,
                    maximum_end_ms=neighbor_maximum_end,
                    match_method="neighbor",
                )
                if resolved is None:
                    continue
                alignments[pending_index] = resolved
                resolved_spans[pending_index] = (
                    candidate.start_index,
                    candidate.end_index,
                )
                gap_cursor = candidate.end_index + 1
                neighbor_previous_end = resolved.end_ms or neighbor_previous_end
                recovered += 1
        previous_anchor = next_anchor

    if recovered:
        LOGGER.info(
            "[cl_vocal2promptseg] Recovered %d Lyrics line(s) between primary "
            "alignment anchors at neighbor threshold %.2f",
            recovered,
            neighbor_match_threshold,
        )
    if resynchronized:
        LOGGER.info(
            "[cl_vocal2promptseg] Re-synchronized %d time(s) using a unique "
            "high-confidence Lyrics line with following-line support",
            resynchronized,
        )
    return alignments


def _unresolved_runs(
    alignments: list[LyricAlignment],
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(len(alignments) + 1):
        unresolved = (
            index < len(alignments) and alignments[index].status != "resolved"
        )
        if unresolved and run_start is None:
            run_start = index
        elif not unresolved and run_start is not None:
            runs.append((run_start, index))
            run_start = None
    return runs


def _targeted_retry_windows(total_seconds: float) -> list[tuple[float, float]]:
    """Return short overlapping decode windows covering one bounded gap."""

    if total_seconds <= _TARGETED_RETRY_WINDOW_SECONDS:
        return [(0.0, total_seconds)]
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < total_seconds:
        end = min(start + _TARGETED_RETRY_WINDOW_SECONDS, total_seconds)
        windows.append((start, end))
        if end >= total_seconds:
            break
        start = end - _TARGETED_RETRY_WINDOW_OVERLAP_SECONDS
    return windows


def _targeted_window_prompt(
    lyrics: list[LyricLine],
    *,
    preceding_line: LyricLine,
    window_start_seconds: float,
    total_seconds: float,
) -> str:
    """Build preceding context without placing current Lyrics in the prompt."""

    fraction = min(1.0, max(0.0, window_start_seconds / total_seconds))
    approximate_index = min(len(lyrics), math.floor(fraction * len(lyrics)))
    context = [preceding_line, *lyrics[:approximate_index]]
    selected: list[str] = []
    for line in reversed(context):
        candidate = "\n".join([line.text, *selected])
        if (
            len(selected) < _WHISPER_INITIAL_PROMPT_MAX_LINES
            and len(candidate) <= _WHISPER_INITIAL_PROMPT_MAX_CHARACTERS
        ):
            selected.insert(0, line.text)
            continue
        break
    if selected:
        return "\n".join(selected)
    return context[-1].text[-_WHISPER_INITIAL_PROMPT_MAX_CHARACTERS:]


def _merge_targeted_window_words(words: list[WhisperWord]) -> list[WhisperWord]:
    """Deduplicate word timestamps produced by overlapping decode windows."""

    ordered = sorted(words, key=lambda word: (word.start, word.source_order))
    merged: list[WhisperWord] = []
    for candidate in ordered:
        candidate_center = (candidate.start + candidate.end) / 2.0
        duplicate_index: int | None = None
        for index in range(len(merged) - 1, -1, -1):
            existing = merged[index]
            existing_center = (existing.start + existing.end) / 2.0
            if (
                candidate_center - existing_center
                > _TARGETED_RETRY_DUPLICATE_CENTER_SECONDS
            ):
                break
            if (
                candidate.normalized == existing.normalized
                and abs(candidate_center - existing_center)
                <= _TARGETED_RETRY_DUPLICATE_CENTER_SECONDS
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            merged.append(candidate)
            continue
        existing = merged[duplicate_index]
        # A word decoded away from a window edge normally has the tighter,
        # more useful timestamp. Retain the shorter duplicate interval.
        if candidate.end - candidate.start < existing.end - existing.start:
            merged[duplicate_index] = candidate
    merged.sort(key=lambda word: (word.start, word.source_order))
    return [replace(word, source_order=index) for index, word in enumerate(merged)]


def targeted_retry_unresolved_lyrics(
    alignments: list[LyricAlignment],
    whisper_audio: Any,
    backend: WhisperBackend,
    *,
    language: str | None,
    device: str,
    condition_on_previous_text: bool,
    match_threshold: float,
    neighbor_match_threshold: float,
) -> tuple[list[LyricAlignment], int, int, int]:
    """Retry only unresolved Lyrics runs bounded by reliable time anchors."""

    updated = list(alignments)
    attempted_runs = 0
    recovered_lines = 0
    invalid_words = 0
    for run_start, run_end in _unresolved_runs(alignments):
        # An unbounded run has no safe PCM window. Keep it unresolved rather
        # than re-running the full recording or inventing a time range.
        if run_start == 0 or run_end >= len(alignments):
            continue
        previous = updated[run_start - 1]
        following = updated[run_end]
        if previous.end_ms is None or following.start_ms is None:
            continue
        slice_start_ms = previous.end_ms
        slice_end_ms = following.start_ms
        slice_seconds = (slice_end_ms - slice_start_ms) / 1000.0
        if slice_seconds < _TARGETED_RETRY_MINIMUM_SECONDS:
            continue
        first_sample = max(
            0, math.floor(slice_start_ms * _WHISPER_SAMPLE_RATE / 1000)
        )
        final_sample = min(
            len(whisper_audio),
            math.ceil(slice_end_ms * _WHISPER_SAMPLE_RATE / 1000),
        )
        if final_sample <= first_sample:
            continue
        run_lyrics = [entry.line for entry in updated[run_start:run_end]]
        windows = _targeted_retry_windows(slice_seconds)
        attempted_runs += 1
        LOGGER.info(
            "[cl_vocal2promptseg] Targeted Whisper retry %d: Lyrics %d-%d, "
            "audio=%.3f-%.3fs (%.3fs), windows=%d (max %.1fs, overlap %.1fs)",
            attempted_runs,
            run_lyrics[0].lyrics_index,
            run_lyrics[-1].lyrics_index,
            slice_start_ms / 1000.0,
            slice_end_ms / 1000.0,
            slice_seconds,
            len(windows),
            _TARGETED_RETRY_WINDOW_SECONDS,
            _TARGETED_RETRY_WINDOW_OVERLAP_SECONDS,
        )
        local_words: list[WhisperWord] = []
        local_samples = final_sample - first_sample
        try:
            for window_index, (window_start, window_end) in enumerate(
                windows, start=1
            ):
                window_first = first_sample + math.floor(
                    window_start * _WHISPER_SAMPLE_RATE
                )
                window_final = min(
                    final_sample,
                    first_sample
                    + math.ceil(window_end * _WHISPER_SAMPLE_RATE),
                )
                window_samples = window_final - window_first
                if window_samples <= 0:  # pragma: no cover - defensive rounding
                    continue
                prompt = _targeted_window_prompt(
                    run_lyrics,
                    preceding_line=previous.line,
                    window_start_seconds=window_start,
                    total_seconds=slice_seconds,
                )
                LOGGER.info(
                    "[cl_vocal2promptseg] Targeted Whisper window %d/%d: "
                    "relative=%.3f-%.3fs, prompt=%d line(s)/%d char(s)",
                    window_index,
                    len(windows),
                    window_start,
                    window_end,
                    prompt.count("\n") + 1 if prompt else 0,
                    len(prompt),
                )
                with _InferenceHeartbeat(
                    f"targeted Lyrics {run_lyrics[0].lyrics_index}-"
                    f"{run_lyrics[-1].lyrics_index} window "
                    f"{window_index}/{len(windows)}"
                ):
                    result = backend.transcribe(
                        whisper_audio[window_first:window_final],
                        language=language,
                        device=device,
                        initial_prompt=prompt or None,
                        condition_on_previous_text=condition_on_previous_text,
                    )
                window_words, local_invalid, _ = extract_whisper_words(
                    result,
                    audio_duration_seconds=window_samples
                    / _WHISPER_SAMPLE_RATE,
                    intervals=[DetectedInterval("voiced", 0, window_samples)],
                    sample_rate=_WHISPER_SAMPLE_RATE,
                )
                invalid_words += local_invalid
                window_offset = (
                    window_first - first_sample
                ) / _WHISPER_SAMPLE_RATE
                local_words.extend(
                    replace(
                        word,
                        start=word.start + window_offset,
                        end=word.end + window_offset,
                    )
                    for word in window_words
                )
            local_words = _merge_targeted_window_words(local_words)
            if not local_words:
                raise VocalPromptError(
                    "targeted Whisper windows returned no usable word timestamps"
                )
            local_alignments = align_lyrics(
                run_lyrics,
                local_words,
                match_threshold=match_threshold,
                neighbor_match_threshold=neighbor_match_threshold,
                search_seconds=max(slice_seconds, 1.0),
                audio_duration_seconds=local_samples / _WHISPER_SAMPLE_RATE,
            )
        except VocalPromptError as exc:
            LOGGER.warning(
                "[cl_vocal2promptseg] Targeted Whisper retry for Lyrics %d-%d "
                "did not produce usable word timestamps: %s",
                run_lyrics[0].lyrics_index,
                run_lyrics[-1].lyrics_index,
                exc,
            )
            continue

        offset_ms = math.floor(first_sample * 1000 / _WHISPER_SAMPLE_RATE)
        run_recovered = 0
        for local_index, local in enumerate(local_alignments):
            if (
                local.status != "resolved"
                or local.start_ms is None
                or local.end_ms is None
            ):
                continue
            absolute_start = max(slice_start_ms, offset_ms + local.start_ms)
            absolute_end = min(slice_end_ms, offset_ms + local.end_ms)
            if absolute_end <= absolute_start:
                continue
            updated[run_start + local_index] = replace(
                local,
                start_ms=absolute_start,
                end_ms=absolute_end,
                match_method="targeted",
            )
            run_recovered += 1
        recovered_lines += run_recovered
        LOGGER.info(
            "[cl_vocal2promptseg] Targeted Whisper retry recovered %d/%d "
            "Lyrics line(s) in range %d-%d",
            run_recovered,
            len(run_lyrics),
            run_lyrics[0].lyrics_index,
            run_lyrics[-1].lyrics_index,
        )
    return updated, attempted_runs, recovered_lines, invalid_words


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


def normalize_chainable_scenes(
    scenes: list[dict[str, Any]],
    *,
    max_scene_seconds: int,
) -> int:
    """Remove one-second intermediate scenes without losing vocal coverage.

    With the normal 22-frame H3 continuation context, a one-second scene
    delivers only 17 reusable frames. Resolved Lyrics can promote a short
    VAD-silent scene after the initial balanced split, so normalize the final
    state ranges before assigning Lyrics to scenes.
    """
    if len(scenes) < 2:
        return 0

    original_signature = [
        (
            str(scene["state"]),
            int(scene["start_seconds"]),
            int(scene["end_seconds"]),
        )
        for scene in scenes
    ]
    ranges: list[list[Any]] = []
    expected_start = 0
    for scene in scenes:
        state = str(scene["state"])
        start = int(scene["start_seconds"])
        end = int(scene["end_seconds"])
        if state not in _VALID_STATES or start != expected_start or end <= start:
            raise VocalPromptError(
                "Scene plan must be contiguous and contain valid states before "
                "context-safe normalization"
            )
        if ranges and ranges[-1][0] == state:
            ranges[-1][2] = end
        else:
            ranges.append([state, start, end])
        expected_start = end

    adjustments = 0
    while True:
        short_index = next(
            (
                index
                for index, (_, start, end) in enumerate(ranges[:-1])
                if end - start < _MIN_CHAINABLE_SCENE_SECONDS
            ),
            None,
        )
        if short_index is None:
            break

        state, start, end = ranges[short_index]
        if state == "silent":
            # A one-second silent gap cannot feed the next H3 continuation.
            # Treating it as voiced is conservative: Source Vocal remains
            # authoritative and no real vocal PCM is classified as silent.
            ranges[short_index][0] = "voiced"
        else:
            neighbor_indices = [
                index
                for index in (short_index - 1, short_index + 1)
                if 0 <= index < len(ranges)
            ]
            if not neighbor_indices:
                break
            neighbor_index = max(
                neighbor_indices,
                key=lambda index: ranges[index][2] - ranges[index][1],
            )
            if neighbor_index < short_index:
                ranges[neighbor_index][2] -= 1
                ranges[short_index][1] -= 1
            else:
                ranges[short_index][2] += 1
                ranges[neighbor_index][1] += 1
            ranges = [item for item in ranges if item[2] > item[1]]
        adjustments += 1

        merged: list[list[Any]] = []
        for item in ranges:
            if merged and merged[-1][0] == item[0]:
                merged[-1][2] = item[2]
            else:
                merged.append(item[:])
        ranges = merged

    rebuilt: list[dict[str, Any]] = []
    for range_index, (state, start, end) in enumerate(ranges):
        length = end - start
        scene_count = math.ceil(length / max_scene_seconds)
        base_length = length // scene_count
        remainder = length % scene_count
        scene_start = start
        for part_index in range(scene_count):
            scene_length = base_length + (1 if part_index < remainder else 0)
            is_final_scene = (
                range_index == len(ranges) - 1
                and part_index == scene_count - 1
            )
            if (
                scene_length < _MIN_CHAINABLE_SCENE_SECONDS
                and not is_final_scene
            ):
                raise VocalPromptError(
                    "max_scene_seconds is too small to create a context-safe "
                    "Scene plan; use at least 3 seconds"
                )
            scene_end = scene_start + scene_length
            rebuilt.append(
                {
                    "index": len(rebuilt) + 1,
                    "state": state,
                    "start_seconds": scene_start,
                    "end_seconds": scene_end,
                    "duration_seconds": scene_length,
                    "lyrics_indices": [],
                }
            )
            scene_start = scene_end

    rebuilt_signature = [
        (scene["state"], scene["start_seconds"], scene["end_seconds"])
        for scene in rebuilt
    ]
    scenes[:] = rebuilt
    if rebuilt_signature == original_signature:
        return 0
    return max(1, adjustments)


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
                match_method=alignment.match_method,
                candidate_whisper_text=alignment.candidate_whisper_text,
                candidate_start_ms=alignment.candidate_start_ms,
                candidate_end_ms=alignment.candidate_end_ms,
            )
        )
    return assigned


def promote_resolved_lyrics_scenes(
    scenes: list[dict[str, Any]], alignments: list[LyricAlignment]
) -> int:
    """Promote VAD-silent scenes touched by verified lyric timing.

    Whisper word acceptance uses word midpoints, while a lyric interval can begin
    just before the integer-second VAD scene that contains those midpoints. Treat
    resolved Lyrics timing as stronger vocal evidence than the energy VAD.
    """
    promoted: set[int] = set()
    for alignment in alignments:
        if (
            alignment.status != "resolved"
            or alignment.start_ms is None
            or alignment.end_ms is None
        ):
            continue
        for scene in scenes:
            scene_start_ms = int(scene["start_seconds"]) * 1000
            scene_end_ms = int(scene["end_seconds"]) * 1000
            if (
                scene_end_ms > alignment.start_ms
                and scene_start_ms < alignment.end_ms
                and scene["state"] != "voiced"
            ):
                scene["state"] = "voiced"
                promoted.add(int(scene["index"]))
    if promoted:
        LOGGER.info(
            "[cl_vocal2promptseg] Promoted %d VAD-silent scene(s) to voiced "
            "because resolved Lyrics timing overlaps them",
            len(promoted),
        )
    return len(promoted)


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
    scenes: list[dict[str, Any]],
    alignments: list[LyricAlignment],
    *,
    include_lyrics_comments: bool = True,
) -> str:
    _validate_bool(include_lyrics_comments, "include_lyrics_comments")
    lyrics_by_scene: dict[int, list[LyricLine]] = {}
    scene_states = {
        int(scene["index"]): str(scene["state"]) for scene in scenes
    }
    for alignment in alignments:
        if alignment.status != "resolved" or alignment.scene_index is None:
            continue
        if scene_states.get(alignment.scene_index) != "voiced":
            raise VocalPromptError(
                f"Resolved Lyrics line {alignment.line.lyrics_index} is "
                "assigned to a non-voiced scene"
            )
        if include_lyrics_comments:
            lyrics_by_scene.setdefault(alignment.scene_index, []).append(
                alignment.line
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
    for scene_number, scene in enumerate(scenes, start=1):
        lines.append("")
        lines.append(f"// シーン {scene_number}")
        continuation = "" if scene["index"] == 1 else " 継続"
        lines.append(
            f"# シーン {scene['duration_seconds']}秒{continuation}"
        )
        source_start = _source_timestamp(scene["start_seconds"] * 1000)
        source_end = _source_timestamp(scene["end_seconds"] * 1000)
        lines.append(
            f"// 検出状態: {scene['state']}。ソース範囲 {source_start}-{source_end}。"
        )
        previous_section: str | None = None
        for lyric in lyrics_by_scene.get(scene["index"], []):
            if lyric.section_label and lyric.section_label != previous_section:
                lines.append(f"// 楽曲セクション: {lyric.section_label}")
                previous_section = lyric.section_label
            lines.append(f"// 歌詞: {lyric.text}")
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


def build_srt_text(
    alignments: list[LyricAlignment],
    *,
    srt_time_offset: int = 0,
    audio_duration_ms: int | None = None,
) -> str:
    srt_time_offset = _validate_int(
        srt_time_offset,
        "srt_time_offset",
        _SRT_TIME_OFFSET_MIN,
        _SRT_TIME_OFFSET_MAX,
    )
    if (
        audio_duration_ms is not None
        and (
            not isinstance(audio_duration_ms, int)
            or isinstance(audio_duration_ms, bool)
            or audio_duration_ms < 0
        )
    ):
        raise VocalPromptError("audio_duration_ms must be a non-negative integer")
    blocks: list[str] = []
    for alignment in alignments:
        if (
            alignment.status != "resolved"
            or alignment.start_ms is None
            or alignment.end_ms is None
        ):
            continue
        start_ms = alignment.start_ms + srt_time_offset
        end_ms = alignment.end_ms + srt_time_offset
        entry_number = len(blocks) + 1
        if start_ms < 0:
            raise VocalPromptError(
                f"srt_time_offset moves SRT entry {entry_number} before "
                f"the audio start ({start_ms} ms)"
            )
        if audio_duration_ms is not None and end_ms > audio_duration_ms:
            raise VocalPromptError(
                f"srt_time_offset moves SRT entry {entry_number} beyond "
                f"the audio end ({end_ms} ms > {audio_duration_ms} ms)"
            )
        blocks.append(
            "\n".join(
                [
                    str(entry_number),
                    f"{_srt_timestamp(start_ms)} --> "
                    f"{_srt_timestamp(end_ms)}",
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


def _srt_lyric_lines(text: str) -> list[str]:
    _validate_srt_text(text)
    if not text:
        return []
    return [block.split("\n")[2] for block in text[:-2].split("\n\n")]


def lyrics_srt_self_test(
    lyrics: list[LyricLine],
    srt_text: str,
) -> dict[str, int | bool | None]:
    expected = [line.text for line in lyrics]
    actual = _srt_lyric_lines(srt_text)
    first_mismatch: int | None = None
    for index in range(max(len(expected), len(actual))):
        expected_line = expected[index] if index < len(expected) else None
        actual_line = actual[index] if index < len(actual) else None
        if expected_line != actual_line:
            first_mismatch = index + 1
            break

    # SRT is an in-order subset when alignment is incomplete. Counting that
    # subsequence keeps coverage meaningful after the first omitted entry.
    matched_count = 0
    actual_index = 0
    for expected_line in expected:
        if actual_index < len(actual) and expected_line == actual[actual_index]:
            matched_count += 1
            actual_index += 1

    return {
        "passed": expected == actual,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "matched_count": matched_count,
        "first_mismatch": first_mismatch,
    }


def _minimum_maximum_average(values: Iterable[float]) -> tuple[float, float, float]:
    materialized = list(values)
    if not materialized:
        return 0.0, 0.0, 0.0
    return min(materialized), max(materialized), sum(materialized) / len(materialized)


def log_quality_diagnostics(
    *,
    lyrics: list[LyricLine],
    srt_text: str,
    alignments: list[LyricAlignment],
    intervals: list[DetectedInterval],
    sample_rate: int,
    total_samples: int,
    silence_threshold_dbfs: float,
    analysis_window_ms: int,
    min_voiced_ms: int,
    min_silence_ms: int,
    voice_padding_ms: int,
    accepted_whisper_words: int,
    invalid_whisper_words: int,
    outside_voiced_words: int,
) -> dict[str, int | bool | None]:
    audio_seconds = total_samples / sample_rate
    voiced_durations = [
        (interval.end_sample - interval.start_sample) / sample_rate
        for interval in intervals
        if interval.state == "voiced"
    ]
    silent_durations = [
        (interval.end_sample - interval.start_sample) / sample_rate
        for interval in intervals
        if interval.state == "silent"
    ]
    voiced_seconds = sum(voiced_durations)
    silent_seconds = sum(silent_durations)
    voiced_min, voiced_max, voiced_average = _minimum_maximum_average(
        voiced_durations
    )
    silent_min, silent_max, silent_average = _minimum_maximum_average(
        silent_durations
    )
    LOGGER.info(
        "[cl_vocal2promptseg] VAD stats: threshold=%.1f dBFS window=%dms "
        "min_voiced=%dms min_silence=%dms padding=%dms; audio=%.3fs; "
        "voiced=%d interval(s)/%.3fs/%.2f%%, silent=%d interval(s)/%.3fs; "
        "Whisper words accepted=%d invalid=%d outside_voiced=%d",
        silence_threshold_dbfs,
        analysis_window_ms,
        min_voiced_ms,
        min_silence_ms,
        voice_padding_ms,
        audio_seconds,
        len(voiced_durations),
        voiced_seconds,
        (100.0 * voiced_seconds / audio_seconds) if audio_seconds else 0.0,
        len(silent_durations),
        silent_seconds,
        accepted_whisper_words,
        invalid_whisper_words,
        outside_voiced_words,
    )
    LOGGER.info(
        "[cl_vocal2promptseg] VAD interval duration stats: "
        "voiced min=%.3fs max=%.3fs avg=%.3fs; "
        "silent min=%.3fs max=%.3fs avg=%.3fs",
        voiced_min,
        voiced_max,
        voiced_average,
        silent_min,
        silent_max,
        silent_average,
    )

    all_min, all_max, all_average = _minimum_maximum_average(
        alignment.match_score for alignment in alignments
    )
    resolved_scores = [
        alignment.match_score
        for alignment in alignments
        if alignment.status == "resolved"
    ]
    resolved_min, resolved_max, resolved_average = _minimum_maximum_average(
        resolved_scores
    )
    primary_count = sum(
        alignment.match_method == "primary" for alignment in alignments
    )
    neighbor_count = sum(
        alignment.match_method == "neighbor" for alignment in alignments
    )
    resync_count = sum(
        alignment.match_method == "resync" for alignment in alignments
    )
    targeted_count = sum(
        alignment.match_method == "targeted" for alignment in alignments
    )
    unresolved_count = sum(
        alignment.status != "resolved" for alignment in alignments
    )
    coverage = 100.0 * len(resolved_scores) / len(alignments) if alignments else 0.0
    LOGGER.info(
        "[cl_vocal2promptseg] Lyrics alignment stats: resolved=%d/%d "
        "(%.2f%%), primary=%d neighbor=%d resync=%d targeted=%d unresolved=%d; "
        "all similarity min=%.4f max=%.4f avg=%.4f; "
        "resolved similarity min=%.4f max=%.4f avg=%.4f",
        len(resolved_scores),
        len(alignments),
        coverage,
        primary_count,
        neighbor_count,
        resync_count,
        targeted_count,
        unresolved_count,
        all_min,
        all_max,
        all_average,
        resolved_min,
        resolved_max,
        resolved_average,
    )

    result = lyrics_srt_self_test(lyrics, srt_text)
    if result["passed"]:
        log_cyan(
            LOGGER,
            "[cl_vocal2promptseg] self test passed: input Lyrics and output "
            "SRT lyrics match exactly (%d/%d)",
            result["actual_count"],
            result["expected_count"],
        )
    else:
        LOGGER.error(
            "%s[cl_vocal2promptseg] self test failed: input Lyrics and output "
            "SRT lyrics differ (matched=%d/%d, output=%d, first_mismatch=%s)%s",
            _ANSI_RED,
            result["matched_count"],
            result["expected_count"],
            result["actual_count"],
            result["first_mismatch"],
            ANSI_RESET,
        )
    return result


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
        "section_label": alignment.line.section_label,
        "section_kind": alignment.line.section_kind,
        "status": alignment.status,
        "match_score": alignment.match_score,
        "match_method": alignment.match_method,
        "whisper_text": alignment.whisper_text,
        "candidate_whisper_text": alignment.candidate_whisper_text,
        "candidate_start_ms": alignment.candidate_start_ms,
        "candidate_end_ms": alignment.candidate_end_ms,
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
        "schema_version": 4,
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
                        "tooltip": "Suno Lyrics text supplied by a STRING output. [Section] headings become MV-planning metadata and are excluded from SRT text.",
                    },
                ),
                "whisper_model": (
                    model_names,
                    {
                        "default": model_names[0],
                        "tooltip": "Local OpenAI Whisper .pt checkpoint below ComfyUI/models/whisper. No model is downloaded automatically.",
                    },
                ),
                "language": (
                    list(_WHISPER_LANGUAGE_OPTIONS),
                    {
                        "default": "ja",
                        "tooltip": (
                            "Whisper language code: ja for Japanese, en for English "
                            "(including US English), or auto for language detection."
                        ),
                    },
                ),
                "device": (["auto", "cuda", "cpu"], {"default": "auto"}),
                "keep_whisper_loaded": ("BOOLEAN", {"default": False}),
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
                "lyrics_neighbor_threshold": (
                    "FLOAT",
                    {
                        "default": 0.45,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "tooltip": (
                            "Lower fallback threshold used only for unresolved Lyrics "
                            "lines bounded by already-resolved neighboring lines."
                        ),
                    },
                ),
                "lyrics_search_seconds": (
                    "FLOAT",
                    {"default": 60.0, "min": 1.0, "max": 600.0, "step": 1.0},
                ),
            },
            "optional": {
                "condition_on_previous_text": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "Preserve Whisper decoder context between internal "
                            "windows. This matches the Whisper CLI default and "
                            "usually improves long or harsh vocals. Existing "
                            "workflows may omit this input and use True."
                        ),
                    },
                ),
                "srt_time_offset": (
                    "INT",
                    {
                        "default": 0,
                        "min": _SRT_TIME_OFFSET_MIN,
                        "max": _SRT_TIME_OFFSET_MAX,
                        "step": 1,
                        "tooltip": (
                            "Shift every generated SRT start/end timestamp by "
                            "this many milliseconds. Out-of-audio timestamps "
                            "stop execution. Prompt and alignment timing do not shift."
                        ),
                    },
                ),
                "include_lyrics_comments": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "Add every resolved Lyrics line to its voiced scene "
                            "as a '// 歌詞: ...' source-template comment. Comments "
                            "are removed before translation and final JSON output."
                        ),
                    },
                ),
            },
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
        lyrics_neighbor_threshold: float,
        lyrics_search_seconds: float,
        condition_on_previous_text: bool = True,
        srt_time_offset: int = 0,
        include_lyrics_comments: bool = True,
    ) -> tuple[str, str, str, str]:
        with self._lock:
            _validate_bool(
                condition_on_previous_text, "condition_on_previous_text"
            )
            _validate_bool(
                include_lyrics_comments, "include_lyrics_comments"
            )
            _validate_bool(keep_whisper_loaded, "keep_whisper_loaded")
            srt_time_offset = _validate_int(
                srt_time_offset,
                "srt_time_offset",
                _SRT_TIME_OFFSET_MIN,
                _SRT_TIME_OFFSET_MAX,
            )
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
            lyrics_neighbor_threshold = _validate_float(
                lyrics_neighbor_threshold, "lyrics_neighbor_threshold", 0.0, 1.0
            )
            if lyrics_neighbor_threshold > lyrics_match_threshold:
                raise VocalPromptError(
                    "lyrics_neighbor_threshold must be less than or equal to "
                    "lyrics_match_threshold"
                )
            lyrics_search_seconds = _validate_float(
                lyrics_search_seconds, "lyrics_search_seconds", 1.0, 600.0
            )
            if language not in _WHISPER_LANGUAGE_OPTIONS:
                raise VocalPromptError("language must be ja, en, or auto")
            if not isinstance(whisper_model, str) or not whisper_model:
                raise VocalPromptError("whisper_model must be a model ID")

            lyrics = parse_suno_lyrics(lyrics_text)
            initial_prompt = build_whisper_initial_prompt(lyrics)
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
                    "model=%s device=%s duration=%.3fs "
                    "initial_prompt=%d line(s)/%d char(s) "
                    "condition_on_previous_text=%s",
                    whisper_model,
                    resolved_device,
                    total_samples / sample_rate,
                    initial_prompt.count("\n") + 1 if initial_prompt else 0,
                    len(initial_prompt),
                    condition_on_previous_text,
                )
                with _InferenceHeartbeat(whisper_model):
                    whisper_result = self._backend.transcribe(
                        whisper_audio,
                        language=None if language == "auto" else language,
                        device=resolved_device,
                        initial_prompt=initial_prompt or None,
                        condition_on_previous_text=condition_on_previous_text,
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
                    neighbor_match_threshold=lyrics_neighbor_threshold,
                    search_seconds=lyrics_search_seconds,
                    audio_duration_seconds=total_samples / sample_rate,
                )
                (
                    alignments,
                    targeted_attempt_count,
                    targeted_resolved_count,
                    targeted_invalid_words,
                ) = targeted_retry_unresolved_lyrics(
                    alignments,
                    whisper_audio,
                    self._backend,
                    language=None if language == "auto" else language,
                    device=resolved_device,
                    condition_on_previous_text=condition_on_previous_text,
                    match_threshold=lyrics_match_threshold,
                    neighbor_match_threshold=lyrics_neighbor_threshold,
                )
                if targeted_invalid_words:
                    LOGGER.warning(
                        "[cl_vocal2promptseg] Ignored %d invalid Whisper word or "
                        "segment value(s) during targeted retries",
                        targeted_invalid_words,
                    )
                promoted_scene_count = promote_resolved_lyrics_scenes(
                    scenes, alignments
                )
                scenes_before_context_normalization = len(scenes)
                context_scene_adjustments = normalize_chainable_scenes(
                    scenes,
                    max_scene_seconds=max_scene_seconds,
                )
                if context_scene_adjustments:
                    LOGGER.info(
                        "[cl_vocal2promptseg] Rebalanced %d short Scene "
                        "boundary/boundaries for H3 continuation context; "
                        "scenes=%d->%d, minimum chained duration=%ds",
                        context_scene_adjustments,
                        scenes_before_context_normalization,
                        len(scenes),
                        _MIN_CHAINABLE_SCENE_SECONDS,
                    )
                alignments = assign_lyrics_to_scenes(alignments, scenes)
                prompt_text = build_prompt_text(
                    scenes,
                    alignments,
                    include_lyrics_comments=include_lyrics_comments,
                )
                try:
                    lex_japanese_markdown(prompt_text)
                except Exception as exc:
                    raise VocalPromptError(
                        "Generated prompt_text failed reduced-Markdown validation"
                    ) from exc
                audio_duration_ms = math.ceil(
                    total_samples * 1000 / sample_rate
                )
                srt_text = build_srt_text(
                    alignments,
                    srt_time_offset=srt_time_offset,
                    audio_duration_ms=audio_duration_ms,
                )
                settings = {
                    "max_scene_seconds": max_scene_seconds,
                    "silence_threshold_dbfs": silence_threshold_dbfs,
                    "analysis_window_ms": analysis_window_ms,
                    "min_voiced_ms": min_voiced_ms,
                    "min_silence_ms": min_silence_ms,
                    "voice_padding_ms": voice_padding_ms,
                    "lyrics_match_threshold": lyrics_match_threshold,
                    "lyrics_neighbor_threshold": lyrics_neighbor_threshold,
                    "condition_on_previous_text": condition_on_previous_text,
                    "srt_time_offset": srt_time_offset,
                    "include_lyrics_comments": include_lyrics_comments,
                    "whisper_initial_prompt_lines": (
                        initial_prompt.count("\n") + 1 if initial_prompt else 0
                    ),
                    "whisper_initial_prompt_characters": len(initial_prompt),
                    "lyrics_search_seconds": lyrics_search_seconds,
                    "post_anchor_search_seconds": min(
                        lyrics_search_seconds, _POST_ANCHOR_SEARCH_SECONDS
                    ),
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
                neighbor_resolved_count = sum(
                    alignment.match_method == "neighbor" for alignment in alignments
                )
                resynchronized_count = sum(
                    alignment.match_method == "resync" for alignment in alignments
                )
                targeted_resolved_count = sum(
                    alignment.match_method == "targeted"
                    for alignment in alignments
                )
                voiced_scene_count = sum(
                    scene["state"] == "voiced" for scene in scenes
                )
                voiced_interval_count = sum(
                    interval.state == "voiced" for interval in intervals
                )
                self_test = log_quality_diagnostics(
                    lyrics=lyrics,
                    srt_text=srt_text,
                    alignments=alignments,
                    intervals=intervals,
                    sample_rate=sample_rate,
                    total_samples=total_samples,
                    silence_threshold_dbfs=silence_threshold_dbfs,
                    analysis_window_ms=analysis_window_ms,
                    min_voiced_ms=min_voiced_ms,
                    min_silence_ms=min_silence_ms,
                    voice_padding_ms=voice_padding_ms,
                    accepted_whisper_words=len(words),
                    invalid_whisper_words=invalid_words,
                    outside_voiced_words=outside_words,
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
                    f"lyrics={resolved_count} resolved "
                    f"({neighbor_resolved_count} neighbor-recovered, "
                    f"{resynchronized_count} resynchronized, "
                    f"{targeted_resolved_count} targeted-recovered in "
                    f"{targeted_attempt_count} run(s), "
                    f"{promoted_scene_count} scene(s) promoted), "
                    f"{unresolved_count} unresolved; "
                    f"self_test={'passed' if self_test['passed'] else 'failed'}; "
                    f"srt_time_offset={srt_time_offset}ms; "
                    f"lyrics_comments="
                    f"{'enabled' if include_lyrics_comments else 'disabled'}; "
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
