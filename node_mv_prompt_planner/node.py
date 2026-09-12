"""ComfyUI node wrapper for hierarchical GGUF music-video planning."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from typing import Any

from ..common.gguf.discovery import discover_model_names, resolve_model_name
from ..common.gguf.runtime import LlamaBackend
from ..common.logging import log_node_success
from .brief_parser import parse_planning_brief
from .debug_output import save_planner_debug_bundle
from .errors import MVPlannerError
from .planning import generate_mv_plan
from .prompt_loader import planner_prompts_fingerprint
from .renderer import render_planned_markdown
from .timeline_parser import parse_prompt_timeline
from .visual_profiles import (
    DEFAULT_VISUAL_PROFILE_ID,
    discover_visual_profile_ids,
    load_visual_profile,
)


LOGGER = logging.getLogger("cl_mv_prompt_planner")
_CHAT_FORMATS = {"auto": None, "qwen": "qwen", "gemma": "gemma"}
_EIGHT_B_MODEL_RE = re.compile(
    r"(?<![a-z0-9])8[\s._-]*b(?![a-z0-9])",
    re.IGNORECASE,
)
_CRITICAL_WARNING_BORDER = "#" * 72


def _warn_for_8b_model(
    model_name: str,
    visual_enrichment_profile: str,
) -> bool:
    """Warn only when an 8B model is paired with the full visual profile."""

    if (
        _EIGHT_B_MODEL_RE.search(str(model_name)) is None
        or visual_enrichment_profile != "lyric_visuals_full"
    ):
        return False
    LOGGER.warning(_CRITICAL_WARNING_BORDER)
    LOGGER.warning(
        "[cl_mv_prompt_planner] CRITICAL / FATAL-RISK: 8B model detected "
        "with lyric_visuals_full; planning may exhaust retries and fail. "
        "Select lyric_visuals_light_8b or use a 14B+ model."
    )
    LOGGER.warning(_CRITICAL_WARNING_BORDER)
    return True

try:  # Available only when loaded by ComfyUI.
    from comfy.utils import ProgressBar as _ComfyProgressBar  # type: ignore
except ImportError:  # pragma: no cover - standalone tests
    _ComfyProgressBar = None

try:  # Available only when loaded by ComfyUI.
    from comfy.model_management import (  # type: ignore
        throw_exception_if_processing_interrupted as _throw_if_interrupted,
    )
except ImportError:  # pragma: no cover - standalone tests
    def _throw_if_interrupted() -> bool:
        return False


class CLMVPromptPlannerGGUF:
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("planned_markdown", "planner_json", "status")
    FUNCTION = "plan_mv_prompt"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    def __init__(self) -> None:
        self._backend = LlamaBackend(log_name="cl_mv_prompt_planner")
        self._lock = threading.RLock()

    @classmethod
    def discover_model_names(cls) -> list[str]:
        return discover_model_names(log_name="cl_mv_prompt_planner")

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        model_names = cls.discover_model_names()
        visual_profile_ids = discover_visual_profile_ids()
        return {
            "required": {
                "prompt_segments": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Time-locked prompt_text from CL Vocal to Prompt Segments.",
                    },
                ),
                "planning_markdown": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Reduced-Markdown planning brief supplied by a text or primitive-string node: Subjects, Retention Analysis, and Common Prompt only. This input-only socket keeps widget positions stable across workflow reloads.",
                    },
                ),
                "model_name": (
                    model_names,
                    {"default": model_names[0]},
                ),
                "chat_format": (
                    list(_CHAT_FORMATS),
                    {
                        "default": "auto",
                        "tooltip": "auto uses the GGUF chat template; explicit qwen/gemma is for incomplete model metadata.",
                    },
                ),
                "max_tokens": (
                    "INT",
                    {"default": 4096, "min": 32, "max": 16384, "step": 32},
                ),
                "temperature": (
                    "FLOAT",
                    {"default": 0.1, "min": 0.1, "max": 1.0, "step": 0.05},
                ),
                "top_p": (
                    "FLOAT",
                    {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "repetition_penalty": (
                    "FLOAT",
                    {"default": 1.05, "min": 0.5, "max": 2.0, "step": 0.05},
                ),
                "gpu_layers": (
                    "INT",
                    {"default": -1, "min": -1, "max": 1000, "step": 1},
                ),
                "n_batch": (
                    "INT",
                    {"default": 256, "min": 32, "max": 4096, "step": 32},
                ),
                "n_ctx": (
                    "INT",
                    {"default": 0, "min": 0, "max": 131072, "step": 512},
                ),
                "flash_attn": ("BOOLEAN", {"default": True}),
                "kv_cache_type": (["q8_0", "f16"], {"default": "q8_0"}),
                "op_offload": ("BOOLEAN", {"default": True}),
                "keep_model_loaded": ("BOOLEAN", {"default": False}),
                "seed": (
                    "INT",
                    {"default": 1, "min": 1, "max": 4_294_967_295},
                ),
                "scenes_per_batch": (
                    "INT",
                    {"default": 6, "min": 1, "max": 16, "step": 1},
                ),
                "retry_max": (
                    "INT",
                    {
                        "default": 10,
                        "min": 0,
                        "max": 20,
                        "step": 1,
                        "tooltip": "Maximum retries for each invalid Scene independently; one Scene never consumes another Scene's budget.",
                    },
                ),
            },
            "optional": {
                "save_debug_output": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Save every planner system prompt, request data, raw line-protocol response, validation result, and the final partial state below ComfyUI/output/cl_mv_prompt_planner_debug.",
                    },
                ),
                "camera_guard": (
                    ["warn", "strict"],
                    {
                        "default": "warn",
                        "tooltip": "warn keeps the LLM plan and logs high-confidence CAMERA field conflicts; strict retries only the affected Scene. No camera text is silently rewritten.",
                    },
                ),
                "vocal_guard": (
                    ["warn", "strict"],
                    {
                        "default": "warn",
                        "tooltip": "warn keeps the LLM plan and logs vocal cues that may conflict with the locked Timeline; strict retries only the affected Scene. Singing words in an already voiced Scene refer to the locked Source Vocal and are always allowed.",
                    },
                ),
                "visual_enrichment_profile": (
                    visual_profile_ids,
                    {
                        "default": DEFAULT_VISUAL_PROFILE_ID,
                        "tooltip": "Selects a bundled scalable system-prompt profile. performance_only preserves current conservative planning; lyric_visuals_light_8b requires one bounded auxiliary visual per Scene; lyric_visuals_full permits one to three for larger models.",
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, model_name: str, **_: Any) -> tuple[Any, ...]:
        prompt_fingerprint = planner_prompts_fingerprint()
        try:
            path = resolve_model_name(
                model_name, log_name="cl_mv_prompt_planner"
            )
            stat = path.stat()
            model_fingerprint: tuple[Any, ...] = (
                str(path.resolve()),
                stat.st_size,
                stat.st_mtime_ns,
            )
        except Exception as exc:
            error_digest = hashlib.sha256(
                f"{model_name}|{type(exc).__name__}|{exc}".encode(
                    "utf-8", "replace"
                )
            ).hexdigest()
            model_fingerprint = (
                "model-resolution-error",
                model_name,
                error_digest,
            )
        return model_fingerprint + prompt_fingerprint

    @staticmethod
    def _validate_parameters(**values: Any) -> None:
        integer_ranges = {
            "max_tokens": (32, 16384),
            "gpu_layers": (-1, 1000),
            "n_batch": (32, 4096),
            "n_ctx": (0, 131072),
            "seed": (1, 4_294_967_295),
            "scenes_per_batch": (1, 16),
            "retry_max": (0, 20),
        }
        for name, (minimum, maximum) in integer_ranges.items():
            value = values[name]
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise MVPlannerError(
                    f"{name} must be an integer between {minimum} and {maximum}"
                )
        for name, minimum, maximum in (
            ("temperature", 0.1, 1.0),
            ("top_p", 0.0, 1.0),
            ("repetition_penalty", 0.5, 2.0),
        ):
            value = values[name]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not minimum <= float(value) <= maximum
            ):
                raise MVPlannerError(f"{name} must be between {minimum} and {maximum}")
        if values["chat_format"] not in _CHAT_FORMATS:
            raise MVPlannerError("chat_format must be auto, qwen, or gemma")
        if values["kv_cache_type"] not in {"q8_0", "f16"}:
            raise MVPlannerError("kv_cache_type must be q8_0 or f16")
        if values["camera_guard"] not in {"warn", "strict"}:
            raise MVPlannerError("camera_guard must be warn or strict")
        if values["vocal_guard"] not in {"warn", "strict"}:
            raise MVPlannerError("vocal_guard must be warn or strict")
        load_visual_profile(values["visual_enrichment_profile"])
        for name in (
            "flash_attn",
            "op_offload",
            "keep_model_loaded",
            "save_debug_output",
        ):
            if not isinstance(values[name], bool):
                raise MVPlannerError(f"{name} must be Boolean")

    def clear_model(self) -> None:
        self._backend.clear_model()

    def plan_mv_prompt(
        self,
        prompt_segments: str,
        planning_markdown: str,
        model_name: str,
        chat_format: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
        gpu_layers: int,
        n_batch: int,
        n_ctx: int,
        flash_attn: bool,
        kv_cache_type: str,
        op_offload: bool,
        keep_model_loaded: bool,
        seed: int,
        scenes_per_batch: int,
        retry_max: int,
        save_debug_output: bool = False,
        camera_guard: str = "warn",
        vocal_guard: str = "warn",
        visual_enrichment_profile: str = DEFAULT_VISUAL_PROFILE_ID,
    ) -> tuple[str, str, str]:
        with self._lock:
            self._validate_parameters(
                chat_format=chat_format,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                gpu_layers=gpu_layers,
                n_batch=n_batch,
                n_ctx=n_ctx,
                flash_attn=flash_attn,
                kv_cache_type=kv_cache_type,
                op_offload=op_offload,
                keep_model_loaded=keep_model_loaded,
                seed=seed,
                scenes_per_batch=scenes_per_batch,
                retry_max=retry_max,
                camera_guard=camera_guard,
                vocal_guard=vocal_guard,
                visual_enrichment_profile=visual_enrichment_profile,
                save_debug_output=save_debug_output,
            )
            _warn_for_8b_model(model_name, visual_enrichment_profile)
            brief = parse_planning_brief(planning_markdown)
            timeline = parse_prompt_timeline(prompt_segments)
            progress_state: dict[str, Any] = {"label": None, "bar": None}
            debug_events: list[dict[str, Any]] = []
            debug_state: dict[str, Any] = {}
            settings = {
                "chat_format": chat_format,
                "max_tokens": max_tokens,
                "temperature": float(temperature),
                "top_p": float(top_p),
                "repetition_penalty": float(repetition_penalty),
                "gpu_layers": gpu_layers,
                "n_batch": n_batch,
                "n_ctx": n_ctx,
                "flash_attn": flash_attn,
                "kv_cache_type": kv_cache_type,
                "op_offload": op_offload,
                "keep_model_loaded": keep_model_loaded,
                "seed": seed,
                "scenes_per_batch": scenes_per_batch,
                "retry_max": retry_max,
                "camera_guard": camera_guard,
                "vocal_guard": vocal_guard,
                "visual_enrichment_profile": visual_enrichment_profile,
                "save_debug_output": save_debug_output,
            }

            def progress(label: str, current: int) -> None:
                if progress_state["label"] != label:
                    progress_state["label"] = label
                    progress_state["bar"] = (
                        _ComfyProgressBar(max_tokens)
                        if _ComfyProgressBar is not None
                        else None
                    )
                    LOGGER.info("[cl_mv_prompt_planner] LLM inference started: %s", label)
                if progress_state["bar"] is not None:
                    progress_state["bar"].update_absolute(
                        min(current, max_tokens), max_tokens
                    )
                if current and current % 500 == 0:
                    LOGGER.info(
                        "[cl_mv_prompt_planner] LLM inference active: %s streamed_chunks=%d",
                        label,
                        current,
                    )

            try:
                model_path = resolve_model_name(
                    model_name, log_name="cl_mv_prompt_planner"
                )
                self._backend.ensure_loaded(
                    model_path,
                    n_ctx=n_ctx,
                    gpu_layers=gpu_layers,
                    n_batch=n_batch,
                    flash_attn=flash_attn,
                    kv_cache_type=kv_cache_type,
                    op_offload=op_offload,
                    chat_format=_CHAT_FORMATS[chat_format],
                )
                plan = generate_mv_plan(
                    brief,
                    timeline,
                    self._backend,
                    max_tokens=max_tokens,
                    temperature=float(temperature),
                    top_p=float(top_p),
                    repetition_penalty=float(repetition_penalty),
                    seed=seed,
                    scenes_per_batch=scenes_per_batch,
                    retry_max=retry_max,
                    camera_guard=camera_guard,
                    vocal_guard=vocal_guard,
                    visual_enrichment_profile=visual_enrichment_profile,
                    progress_callback=progress,
                    interrupt_callback=_throw_if_interrupted,
                    debug_events=(debug_events if save_debug_output else None),
                    debug_state=(debug_state if save_debug_output else None),
                )
                planned_markdown = render_planned_markdown(brief, timeline, plan)
                planner_json = json.dumps(
                    plan.to_dict(), ensure_ascii=False, indent=2
                ) + "\n"
                request_count = int(plan.metadata.get("request_count", 0))
                status = (
                    f"planned {len(plan.scenes)} scene(s) in {request_count} "
                    f"LLM request(s); retries={plan.attempts}; model={model_name}; "
                    f"chat_format={chat_format}; camera_guard={camera_guard}; "
                    f"camera_warnings={len(plan.metadata.get('camera_warnings', []))}; "
                    f"vocal_guard={vocal_guard}; "
                    f"vocal_warnings={len(plan.metadata.get('vocal_warnings', []))}; "
                    f"visual_enrichment_profile={visual_enrichment_profile}"
                )
                log_node_success(
                    LOGGER,
                    "cl_mv_prompt_planner",
                    "%s",
                    status,
                )
                if save_debug_output:
                    self._save_debug_output(
                        prompt_segments=prompt_segments,
                        planning_markdown=planning_markdown,
                        model_name=model_name,
                        settings=settings,
                        events=debug_events,
                        final_state=debug_state,
                        planned_markdown=planned_markdown,
                        planner_json=planner_json,
                        error=None,
                    )
                return planned_markdown, planner_json, status
            except Exception as exc:
                if save_debug_output:
                    debug_state.setdefault("status", "error")
                    debug_state.setdefault("stage", "node_failed")
                    debug_state["node_error"] = f"{type(exc).__name__}: {exc}"
                    self._save_debug_output(
                        prompt_segments=prompt_segments,
                        planning_markdown=planning_markdown,
                        model_name=model_name,
                        settings=settings,
                        events=debug_events,
                        final_state=debug_state,
                        planned_markdown=None,
                        planner_json=None,
                        error=exc,
                    )
                self._backend.clear_model()
                raise
            finally:
                if not keep_model_loaded:
                    self._backend.clear_model()

    @staticmethod
    def _save_debug_output(
        *,
        prompt_segments: str,
        planning_markdown: str,
        model_name: str,
        settings: dict[str, Any],
        events: list[dict[str, Any]],
        final_state: dict[str, Any],
        planned_markdown: str | None,
        planner_json: str | None,
        error: Exception | None,
    ) -> None:
        try:
            path = save_planner_debug_bundle(
                prompt_segments=prompt_segments,
                planning_markdown=planning_markdown,
                model_name=model_name,
                settings=settings,
                events=events,
                final_state=final_state,
                planned_markdown=planned_markdown,
                planner_json=planner_json,
                error=error,
            )
        except Exception as exc:
            LOGGER.warning(
                "[cl_mv_prompt_planner] Could not save debug output: %s", exc
            )
            return
        LOGGER.info("[cl_mv_prompt_planner] Saved debug output: %s", path)
