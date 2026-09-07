"""Small audio utilities that do not depend on llama-cpp-python."""

from __future__ import annotations

import logging
import math
from typing import Any


LOGGER = logging.getLogger("cl_japanese2json")
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
        base_target_samples = max(
            current_samples,
            ui_target_samples,
            plan_target_samples,
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
        if extra_seconds > 0.0:
            target_parts.append(f"extra={extra_seconds:.6f}s")
        target_description = ", ".join(target_parts) or "no target"

        if padding_samples == 0:
            status = (
                f"audio unchanged at {current_samples} samples "
                f"({original_duration:.6f}s); {target_description}"
            )
            LOGGER.info("[cl_japanese2json] %s", status)
            return (audio, original_duration, original_duration, 0.0, status)

        if pad_position == "end":
            before_samples, after_samples = 0, padding_samples
        elif pad_position == "start":
            before_samples, after_samples = padding_samples, 0
        else:
            before_samples = padding_samples // 2
            after_samples = padding_samples - before_samples

        output_shape = list(waveform.shape)
        output_shape[-1] = output_samples
        padded_waveform = waveform.new_zeros(tuple(output_shape))
        padded_waveform[
            ..., before_samples : before_samples + current_samples
        ] = waveform
        padded_audio = dict(audio)
        padded_audio["waveform"] = padded_waveform

        status = (
            f"padded {padding_samples} zero sample(s) "
            f"({padding_duration:.6f}s) at {pad_position}: "
            f"{original_duration:.6f}s -> {padded_duration:.6f}s; "
            f"{target_description}"
        )
        LOGGER.info("[cl_japanese2json] %s", status)
        return (
            padded_audio,
            original_duration,
            padded_duration,
            padding_duration,
            status,
        )
