"""Small audio utilities that do not depend on llama-cpp-python."""

from __future__ import annotations

import logging
import math
from typing import Any

from ..common.logging import log_node_success


LOGGER = logging.getLogger("cl_audiopad")
_PAD_POSITIONS = ("end", "start", "both")
_H3_FPS = 24.0
_H3_SAFE_TAIL_FRAMES = 16
_H3_FRAME_MODES = ("auto_safe", "exact_frames", "disabled")
_MAX_TIMELINE_FRAMES = 2_073_600  # 24 hours at H3's fixed 24 fps.


def _non_negative_seconds(value: Any, name: str, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= maximum
    ):
        raise ValueError(f"{name} must be between 0 and {maximum} seconds")
    return float(value)


def _timeline_frame_target_samples(
    base_duration_seconds: float,
    sample_rate: int,
    h3_frame_mode: str,
    h3_target_frames: int,
) -> tuple[int, str | None]:
    """Resolve a Plan-independent H3 timeline target at fixed 24 fps."""

    if h3_frame_mode not in _H3_FRAME_MODES:
        raise ValueError(f"h3_frame_mode must be one of {_H3_FRAME_MODES}")
    if (
        not isinstance(h3_target_frames, int)
        or isinstance(h3_target_frames, bool)
        or not 0 <= h3_target_frames <= _MAX_TIMELINE_FRAMES
    ):
        raise ValueError(
            f"h3_target_frames must be an integer between 0 and {_MAX_TIMELINE_FRAMES}"
        )

    if h3_frame_mode == "disabled":
        return 0, None
    if h3_frame_mode == "exact_frames":
        if h3_target_frames < 1:
            raise ValueError(
                "h3_target_frames must be positive when h3_frame_mode=exact_frames"
            )
        target_frames = h3_target_frames
        description = f"exact H3 target={target_frames} frames at 24 fps"
    else:
        # CL Vocal to Prompt Segments emits an integer-second Scene timeline.
        # The compiler guarantees that H3 lattice compensation is 0..16 frames.
        whole_seconds = int(math.ceil(base_duration_seconds - 1e-9))
        requested_frames = whole_seconds * int(_H3_FPS)
        target_frames = requested_frames + _H3_SAFE_TAIL_FRAMES
        description = (
            f"automatic H3-safe target={target_frames} frames at 24 fps "
            f"({whole_seconds}s timeline + {_H3_SAFE_TAIL_FRAMES} safety frames)"
        )

    return int(round(target_frames / _H3_FPS * sample_rate)), description


def _pad_audio_to_samples(
    audio: dict[str, Any],
    waveform: Any,
    current_samples: int,
    output_samples: int,
    pad_position: str,
) -> dict[str, Any]:
    """Return ``audio`` padded to an exact sample count without modifying it."""
    if output_samples < current_samples:
        raise ValueError("output sample count cannot be shorter than input audio")
    if output_samples == current_samples:
        return audio

    padding_samples = output_samples - current_samples
    if pad_position == "end":
        before_samples = 0
    elif pad_position == "start":
        before_samples = padding_samples
    else:
        before_samples = padding_samples // 2

    output_shape = list(waveform.shape)
    output_shape[-1] = output_samples
    padded_waveform = waveform.new_zeros(tuple(output_shape))
    padded_waveform[
        ..., before_samples : before_samples + current_samples
    ] = waveform
    padded_audio = dict(audio)
    padded_audio["waveform"] = padded_waveform
    return padded_audio


class CLAudioPad:
    """Pad ComfyUI AUDIO with exact zero-valued PCM without trimming input."""

    RETURN_TYPES = ("AUDIO", "FLOAT", "FLOAT", "FLOAT", "STRING")
    RETURN_NAMES = (
        "padded_audio",
        "original_duration",
        "padded_duration",
        "padding_added",
        "status",
    )
    FUNCTION = "pad_audio"
    CATEGORY = "MiniMax H3/Audio Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "audio": (
                    "AUDIO",
                    {
                        "tooltip": "Audio from Load Audio or another standard ComfyUI AUDIO output.",
                    },
                ),
                "target_duration_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 86400.0,
                        "step": 0.001,
                        "tooltip": "Minimum source-timeline duration before H3 frame handling. 0 uses the input duration.",
                    },
                ),
                "extra_padding_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 3600.0,
                        "step": 0.001,
                        "tooltip": "Additional zero-valued PCM added after satisfying the audio, UI target, and H3 frame target.",
                    },
                ),
                "pad_position": (
                    list(_PAD_POSITIONS),
                    {
                        "default": "end",
                        "tooltip": "Where silence is inserted. Use end for source-track lip sync; start and both shift the source timeline.",
                    },
                ),
                "h3_frame_mode": (
                    list(_H3_FRAME_MODES),
                    {
                        "default": "auto_safe",
                        "tooltip": "auto_safe rounds the source timeline up to a whole second and reserves the compiler's maximum 16-frame H3 lattice excess. exact_frames uses h3_target_frames. disabled performs no H3 frame calculation.",
                    },
                ),
                "h3_target_frames": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": _MAX_TIMELINE_FRAMES,
                        "step": 1,
                        "tooltip": "Absolute 24 fps minimum frame count used only with h3_frame_mode=exact_frames.",
                    },
                ),
            },
            "optional": {
                "match_audio": (
                    "AUDIO",
                    {
                        "tooltip": "Optional authoritative track whose duration is another minimum target. Use the padded full mix here to extend a shorter aligned vocal stem without trimming, resampling, mixing, or moving either track.",
                    },
                ),
            },
        }

    @staticmethod
    def _validate_audio(audio: Any) -> tuple[Any, int, int]:
        if not isinstance(audio, dict):
            raise ValueError("audio must be a ComfyUI AUDIO object")
        waveform = audio.get("waveform")
        sample_rate = audio.get("sample_rate")
        if waveform is None or not hasattr(waveform, "shape"):
            raise ValueError("audio.waveform must be a tensor")
        shape = tuple(waveform.shape)
        if len(shape) != 3 or shape[0] < 1 or shape[1] < 1 or shape[2] < 0:
            raise ValueError(
                "audio.waveform must have shape [batch, channels, samples]"
            )
        if (
            not isinstance(sample_rate, int)
            or isinstance(sample_rate, bool)
            or sample_rate < 1
        ):
            raise ValueError("audio.sample_rate must be a positive integer")
        if not callable(getattr(waveform, "new_zeros", None)):
            raise ValueError("audio.waveform must support tensor allocation")
        return waveform, sample_rate, int(shape[-1])

    @classmethod
    def pad_audio(
        cls,
        audio: dict[str, Any],
        target_duration_seconds: float,
        extra_padding_seconds: float,
        pad_position: str,
        h3_frame_mode: str = "auto_safe",
        h3_target_frames: int = 0,
        match_audio: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, float, float, str]:
        waveform, sample_rate, current_samples = cls._validate_audio(audio)
        target_seconds = _non_negative_seconds(
            target_duration_seconds, "target_duration_seconds", 86400.0
        )
        extra_seconds = _non_negative_seconds(
            extra_padding_seconds, "extra_padding_seconds", 3600.0
        )
        if pad_position not in _PAD_POSITIONS:
            raise ValueError(f"pad_position must be one of {_PAD_POSITIONS}")

        ui_target_samples = int(round(target_seconds * sample_rate))
        match_target_samples = 0
        match_description = None
        if match_audio is not None:
            _, match_sample_rate, match_samples = cls._validate_audio(match_audio)
            match_duration = match_samples / float(match_sample_rate)
            match_target_samples = int(round(match_duration * sample_rate))
            match_description = (
                f"match_audio={match_samples} samples at {match_sample_rate} Hz "
                f"({match_duration:.6f}s)"
            )
        preliminary_target_samples = max(
            current_samples,
            ui_target_samples,
            match_target_samples,
        )
        h3_target_samples, h3_description = _timeline_frame_target_samples(
            preliminary_target_samples / float(sample_rate),
            sample_rate,
            h3_frame_mode,
            h3_target_frames,
        )
        base_target_samples = max(preliminary_target_samples, h3_target_samples)
        extra_samples = int(round(extra_seconds * sample_rate))
        output_samples = base_target_samples + extra_samples
        padding_samples = output_samples - current_samples

        original_duration = current_samples / float(sample_rate)
        padded_duration = output_samples / float(sample_rate)
        padding_duration = padding_samples / float(sample_rate)
        target_parts = []
        if target_seconds > 0.0:
            target_parts.append(f"UI target={target_seconds:.6f}s")
        if h3_description is not None:
            target_parts.append(h3_description)
        if match_description is not None:
            target_parts.append(match_description)
        if extra_seconds > 0.0:
            target_parts.append(f"extra={extra_seconds:.6f}s")
        target_description = ", ".join(target_parts) or "no target"

        if padding_samples == 0:
            status = (
                f"audio unchanged at {current_samples} samples "
                f"({original_duration:.6f}s); {target_description}"
            )
            log_node_success(LOGGER, "cl_audiopad", "%s", status)
            return (audio, original_duration, original_duration, 0.0, status)

        padded_audio = _pad_audio_to_samples(
            audio,
            waveform,
            current_samples,
            output_samples,
            pad_position,
        )

        status = (
            f"padded {padding_samples} zero sample(s) "
            f"({padding_duration:.6f}s) at {pad_position}: "
            f"{original_duration:.6f}s -> {padded_duration:.6f}s; "
            f"{target_description}"
        )
        log_node_success(LOGGER, "cl_audiopad", "%s", status)
        return (
            padded_audio,
            original_duration,
            padded_duration,
            padding_duration,
            status,
        )


class CLAudioPadPair:
    """Pad two aligned AUDIO tracks to their common longest duration."""

    RETURN_TYPES = (
        "AUDIO",
        "AUDIO",
        "FLOAT",
        "FLOAT",
        "FLOAT",
        "FLOAT",
        "FLOAT",
        "STRING",
    )
    RETURN_NAMES = (
        "padded_audio_a",
        "padded_audio_b",
        "original_duration_a",
        "original_duration_b",
        "aligned_duration",
        "padding_added_a",
        "padding_added_b",
        "status",
    )
    FUNCTION = "pad_audio_pair"
    CATEGORY = "MiniMax H3/Audio Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "audio_a": (
                    "AUDIO",
                    {
                        "tooltip": "First aligned track, such as the full mix. It is padded only when shorter than the common target.",
                    },
                ),
                "audio_b": (
                    "AUDIO",
                    {
                        "tooltip": "Second aligned track, such as the vocal stem. It is padded only when shorter than the common target.",
                    },
                ),
                "target_duration_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 86400.0,
                        "step": 0.001,
                        "tooltip": "Optional minimum duration for both tracks. 0 disables this target.",
                    },
                ),
                "extra_padding_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 3600.0,
                        "step": 0.001,
                        "tooltip": "Additional silence appended after satisfying both inputs, the UI target, and the H3 frame target.",
                    },
                ),
                "pad_position": (
                    list(_PAD_POSITIONS),
                    {
                        "default": "end",
                        "tooltip": "Where silence is inserted into each shorter track. Use end to preserve source-timeline synchronization.",
                    },
                ),
                "h3_frame_mode": (
                    list(_H3_FRAME_MODES),
                    {
                        "default": "auto_safe",
                        "tooltip": "auto_safe rounds the common source timeline up to a whole second and reserves the compiler's maximum 16-frame H3 lattice excess. exact_frames uses h3_target_frames. disabled only aligns the inputs/UI target.",
                    },
                ),
                "h3_target_frames": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": _MAX_TIMELINE_FRAMES,
                        "step": 1,
                        "tooltip": "Absolute 24 fps minimum frame count used only with h3_frame_mode=exact_frames.",
                    },
                ),
            },
        }

    @classmethod
    def pad_audio_pair(
        cls,
        audio_a: dict[str, Any],
        audio_b: dict[str, Any],
        target_duration_seconds: float,
        extra_padding_seconds: float,
        pad_position: str,
        h3_frame_mode: str = "auto_safe",
        h3_target_frames: int = 0,
    ) -> tuple[dict[str, Any], dict[str, Any], float, float, float, float, float, str]:
        waveform_a, sample_rate_a, samples_a = CLAudioPad._validate_audio(audio_a)
        waveform_b, sample_rate_b, samples_b = CLAudioPad._validate_audio(audio_b)
        target_seconds = _non_negative_seconds(
            target_duration_seconds, "target_duration_seconds", 86400.0
        )
        extra_seconds = _non_negative_seconds(
            extra_padding_seconds, "extra_padding_seconds", 3600.0
        )
        if pad_position not in _PAD_POSITIONS:
            raise ValueError(f"pad_position must be one of {_PAD_POSITIONS}")

        duration_a = samples_a / float(sample_rate_a)
        duration_b = samples_b / float(sample_rate_b)
        preliminary_duration = max(duration_a, duration_b, target_seconds)
        h3_target_a, h3_description = _timeline_frame_target_samples(
            preliminary_duration,
            sample_rate_a,
            h3_frame_mode,
            h3_target_frames,
        )
        h3_target_b, _ = _timeline_frame_target_samples(
            preliminary_duration,
            sample_rate_b,
            h3_frame_mode,
            h3_target_frames,
        )
        h3_duration = max(
            h3_target_a / float(sample_rate_a),
            h3_target_b / float(sample_rate_b),
        )
        base_duration = max(preliminary_duration, h3_duration)
        output_duration = base_duration + extra_seconds

        output_samples_a = max(samples_a, int(round(output_duration * sample_rate_a)))
        output_samples_b = max(samples_b, int(round(output_duration * sample_rate_b)))
        padding_samples_a = output_samples_a - samples_a
        padding_samples_b = output_samples_b - samples_b
        padding_a = padding_samples_a / float(sample_rate_a)
        padding_b = padding_samples_b / float(sample_rate_b)
        padded_duration_a = output_samples_a / float(sample_rate_a)
        padded_duration_b = output_samples_b / float(sample_rate_b)
        aligned_duration = max(padded_duration_a, padded_duration_b)

        padded_audio_a = _pad_audio_to_samples(
            audio_a,
            waveform_a,
            samples_a,
            output_samples_a,
            pad_position,
        )
        padded_audio_b = _pad_audio_to_samples(
            audio_b,
            waveform_b,
            samples_b,
            output_samples_b,
            pad_position,
        )

        target_parts = [
            f"longest input={max(duration_a, duration_b):.6f}s",
        ]
        if target_seconds > 0.0:
            target_parts.append(f"UI target={target_seconds:.6f}s")
        if h3_description is not None:
            target_parts.append(h3_description)
        if extra_seconds > 0.0:
            target_parts.append(f"extra={extra_seconds:.6f}s")
        status = (
            f"aligned audio pair at {aligned_duration:.6f}s using {pad_position} "
            f"padding: audio_a {duration_a:.6f}s + {padding_a:.6f}s; "
            f"audio_b {duration_b:.6f}s + {padding_b:.6f}s; "
            + ", ".join(target_parts)
        )
        log_node_success(LOGGER, "cl_audiopad", "%s", status)
        return (
            padded_audio_a,
            padded_audio_b,
            duration_a,
            duration_b,
            aligned_duration,
            padding_a,
            padding_b,
            status,
        )
