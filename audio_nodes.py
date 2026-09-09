"""Small audio utilities that do not depend on llama-cpp-python."""

from __future__ import annotations

import logging
import math
from typing import Any


LOGGER = logging.getLogger("cl_audiopad")
_DEFAULT_PLAN_FPS = 24.0
_PAD_POSITIONS = ("end", "start", "both")


def _non_negative_seconds(value: Any, name: str, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= maximum
    ):
        raise ValueError(f"{name} must be between 0 and {maximum} seconds")
    return float(value)


def _plan_target_samples(plan: Any, sample_rate: int) -> tuple[int, str] | None:
    if plan is None:
        return None
    if not isinstance(plan, dict):
        raise ValueError("plan must be an H3_CHAIN_PLAN object")

    frames = plan.get("total_delivered_frames")
    if not isinstance(frames, int) or isinstance(frames, bool) or frames < 1:
        raise ValueError("plan.total_delivered_frames must be a positive integer")

    compatibility = plan.get("compatibility")
    if compatibility is not None and not isinstance(compatibility, dict):
        raise ValueError("plan.compatibility must be an object")
    compatibility = compatibility or {}
    fps = compatibility.get("fps", plan.get("fps", _DEFAULT_PLAN_FPS))
    if (
        not isinstance(fps, (int, float))
        or isinstance(fps, bool)
        or not math.isfinite(float(fps))
        or float(fps) <= 0.0
    ):
        raise ValueError("plan fps must be a finite positive number")

    target = int(round(frames / float(fps) * sample_rate))
    return target, f"plan={frames} frames at {float(fps):g} fps"


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
                        "tooltip": "Minimum total duration when no longer target is supplied by an optional H3 plan. 0 disables this target.",
                    },
                ),
                "extra_padding_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 3600.0,
                        "step": 0.001,
                        "tooltip": "Additional zero-valued PCM added after satisfying the audio, UI target, and optional H3 plan target. With no target, this is a fixed padding amount.",
                    },
                ),
                "pad_position": (
                    list(_PAD_POSITIONS),
                    {
                        "default": "end",
                        "tooltip": "Where silence is inserted. Use end for source-track lip sync; start and both shift the source timeline.",
                    },
                ),
            },
            "optional": {
                "plan": (
                    "H3_CHAIN_PLAN",
                    {
                        "tooltip": "Optional MiniMax H3 Contex-Loop plan. Its delivered frame count and fps automatically set the minimum audio length.",
                    },
                ),
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
        plan: dict[str, Any] | None = None,
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
        plan_target = _plan_target_samples(plan, sample_rate)
        plan_target_samples = 0 if plan_target is None else plan_target[0]
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
        base_target_samples = max(
            current_samples,
            ui_target_samples,
            plan_target_samples,
            match_target_samples,
        )
        extra_samples = int(round(extra_seconds * sample_rate))
        output_samples = base_target_samples + extra_samples
        padding_samples = output_samples - current_samples

        original_duration = current_samples / float(sample_rate)
        padded_duration = output_samples / float(sample_rate)
        padding_duration = padding_samples / float(sample_rate)
        target_parts = []
        if target_seconds > 0.0:
            target_parts.append(f"UI target={target_seconds:.6f}s")
        if plan_target is not None:
            target_parts.append(plan_target[1])
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
            LOGGER.info("[cl_audiopad] %s", status)
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
        LOGGER.info("[cl_audiopad] %s", status)
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
                        "tooltip": "Additional silence appended after satisfying both inputs, the UI target, and the optional H3 plan.",
                    },
                ),
                "pad_position": (
                    list(_PAD_POSITIONS),
                    {
                        "default": "end",
                        "tooltip": "Where silence is inserted into each shorter track. Use end to preserve source-timeline synchronization.",
                    },
                ),
            },
            "optional": {
                "plan": (
                    "H3_CHAIN_PLAN",
                    {
                        "tooltip": "Optional MiniMax H3 Contex-Loop plan. Both tracks are padded to at least its delivered duration.",
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
        plan: dict[str, Any] | None = None,
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
        plan_target_a = _plan_target_samples(plan, sample_rate_a)
        plan_target_b = _plan_target_samples(plan, sample_rate_b)
        plan_seconds = (
            0.0
            if plan_target_a is None
            else max(
                plan_target_a[0] / float(sample_rate_a),
                plan_target_b[0] / float(sample_rate_b),
            )
        )
        base_duration = max(duration_a, duration_b, target_seconds, plan_seconds)
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
        if plan_target_a is not None:
            target_parts.append(plan_target_a[1])
        if extra_seconds > 0.0:
            target_parts.append(f"extra={extra_seconds:.6f}s")
        status = (
            f"aligned audio pair at {aligned_duration:.6f}s using {pad_position} "
            f"padding: audio_a {duration_a:.6f}s + {padding_a:.6f}s; "
            f"audio_b {duration_b:.6f}s + {padding_b:.6f}s; "
            + ", ".join(target_parts)
        )
        LOGGER.info("[cl_audiopad] %s", status)
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
