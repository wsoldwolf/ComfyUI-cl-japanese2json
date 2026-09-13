"""ComfyUI wrapper for protected GGUF prompt enhancement."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from typing import Any

from ..common.gguf.discovery import discover_model_names, resolve_model_name
from ..common.gguf.runtime import LlamaBackend
from ..common.logging import log_node_success
from .debug_output import save_enhancer_debug_bundle
from .engine import enhance_reduced_markdown
from .errors import PromptEnhancerError
from .markdown import inspect_prompt
from .profiles import (
    DEFAULT_BACKGROUND_PROFILE_ID,
    DEFAULT_STYLE_PROFILE_ID,
    discover_background_profile_ids,
    discover_style_profile_ids,
    load_background_profile,
    load_style_profile,
)
from .prompt_loader import enhancer_prompts_fingerprint


LOGGER = logging.getLogger("cl_prompt_enhancer")
_CHAT_FORMATS = {"auto": None, "qwen": "qwen", "gemma": "gemma"}

try:
    from comfy.utils import ProgressBar as _ComfyProgressBar  # type: ignore
except ImportError:  # pragma: no cover
    _ComfyProgressBar = None

try:
    from comfy.model_management import (  # type: ignore
        throw_exception_if_processing_interrupted as _throw_if_interrupted,
    )
except ImportError:  # pragma: no cover
    def _throw_if_interrupted() -> bool:
        return False


def _select_override(
    widget_value: str,
    override_value: str | None,
    name: str,
) -> tuple[str, bool]:
    if not isinstance(widget_value, str):
        raise PromptEnhancerError(f"{name} widget value must be a string")
    if override_value is None:
        return widget_value, False
    if not isinstance(override_value, str):
        raise PromptEnhancerError(f"{name}_override must be a string")
    value = override_value.strip()
    return (value, True) if value else (widget_value, False)


class CLPromptEnhancerGGUF:
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("enhanced_markdown", "enhancement_report", "status")
    FUNCTION = "enhance_prompt"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    def __init__(self) -> None:
        self._backend = LlamaBackend(log_name="cl_prompt_enhancer")
        self._lock = threading.RLock()

    @property
    def llm(self) -> Any | None:
        return self._backend.llm

    @classmethod
    def discover_model_names(cls) -> list[str]:
        return discover_model_names(log_name="cl_prompt_enhancer")

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        models = cls.discover_model_names()
        return {
            "required": {
                "source_markdown": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Global reduced Markdown from CL Prompt Merger.",
                    },
                ),
                "model_name": (models, {"default": models[0]}),
                "chat_format": (list(_CHAT_FORMATS), {"default": "auto"}),
                "style_profile": (
                    discover_style_profile_ids(),
                    {
                        "default": DEFAULT_STYLE_PROFILE_ID,
                        "tooltip": "External style profile; passthrough changes no style.",
                    },
                ),
                "background_detail": (
                    discover_background_profile_ids(),
                    {
                        "default": DEFAULT_BACKGROUND_PROFILE_ID,
                        "tooltip": "Background density; user_prompt lines are immutable.",
                    },
                ),
                "max_tokens": (
                    "INT",
                    {"default": 2048, "min": 32, "max": 16384, "step": 32},
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
                "retry_max": (
                    "INT",
                    {
                        "default": 2,
                        "min": 0,
                        "max": 10,
                        "step": 1,
                        "tooltip": "Retries after the initial invalid response.",
                    },
                ),
            },
            "optional": {
                "user_prompt": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Independent user-authored global reduced Markdown. "
                            "The LLM cannot rewrite it."
                        ),
                    },
                ),
                "additional_instruction": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "Optional context hint; it is not copied verbatim.",
                    },
                ),
                "model_name_override": (
                    "STRING",
                    {
                        "forceInput": True,
                        "connected_combo_source": "model_name",
                        "tooltip": "Overrides model_name when non-empty.",
                    },
                ),
                "style_profile_override": (
                    "STRING",
                    {
                        "forceInput": True,
                        "connected_combo_source": "style_profile",
                        "tooltip": "Overrides style_profile when non-empty.",
                    },
                ),
                "background_detail_override": (
                    "STRING",
                    {
                        "forceInput": True,
                        "connected_combo_source": "background_detail",
                        "tooltip": "Overrides background_detail when non-empty.",
                    },
                ),
                "save_debug_output": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Save a diagnostic bundle below ComfyUI/output.",
                    },
                ),
                "semantic_guard": (
                    "BOOLEAN", {"default": False, "tooltip": "Experimental LLM review of changed environment facts. Small models may reject valid changes; disabled by default. Verbatim scene-anchor preservation is always active. Up to 2 meaning repairs."},
                ),
            },
        }

    @classmethod
    def IS_CHANGED(
        cls,
        model_name: str,
        model_name_override: str = "",
        **_: Any,
    ) -> tuple[Any, ...]:
        try:
            effective, _active = _select_override(
                model_name, model_name_override, "model_name"
            )
            path = resolve_model_name(effective, log_name="cl_prompt_enhancer")
            stat = path.stat()
            model_fingerprint: tuple[Any, ...] = (
                str(path.resolve()), stat.st_size, stat.st_mtime_ns
            )
        except Exception as exc:
            digest = hashlib.sha256(
                f"{model_name}|{model_name_override}|{type(exc).__name__}|{exc}".encode(
                    "utf-8", "replace"
                )
            ).hexdigest()
            model_fingerprint = ("model-resolution-error", digest)
        return model_fingerprint + enhancer_prompts_fingerprint()

    @staticmethod
    def _validate_parameters(**values: Any) -> None:
        for name, minimum, maximum in (
            ("max_tokens", 32, 16384),
            ("gpu_layers", -1, 1000),
            ("n_batch", 32, 4096),
            ("n_ctx", 0, 131072),
            ("seed", 1, 4_294_967_295),
            ("retry_max", 0, 10),
        ):
            value = values[name]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise PromptEnhancerError(
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
                raise PromptEnhancerError(f"{name} must be between {minimum} and {maximum}")
        if values["chat_format"] not in _CHAT_FORMATS:
            raise PromptEnhancerError("chat_format must be auto, qwen, or gemma")
        if values["kv_cache_type"] not in {"q8_0", "f16"}:
            raise PromptEnhancerError("kv_cache_type must be q8_0 or f16")
        for name in (
            "flash_attn", "op_offload", "keep_model_loaded", "save_debug_output"
        ):
            if not isinstance(values[name], bool):
                raise PromptEnhancerError(f"{name} must be Boolean")

    def clear_model(self) -> None:
        self._backend.clear_model()

    def enhance_prompt(
        self,
        source_markdown: str,
        model_name: str,
        chat_format: str,
        style_profile: str,
        background_detail: str,
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
        retry_max: int,
        user_prompt: str = "",
        additional_instruction: str = "",
        model_name_override: str = "",
        style_profile_override: str = "",
        background_detail_override: str = "",
        save_debug_output: bool = False,
        semantic_guard: bool = False,
    ) -> tuple[str, str, str]:
        with self._lock:
            if not isinstance(semantic_guard, bool):
                raise PromptEnhancerError("semantic_guard must be Boolean")
            effective_model, model_overridden = _select_override(
                model_name, model_name_override, "model_name"
            )
            effective_style, style_overridden = _select_override(
                style_profile, style_profile_override, "style_profile"
            )
            effective_background, background_overridden = _select_override(
                background_detail, background_detail_override, "background_detail"
            )
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
                retry_max=retry_max,
                save_debug_output=save_debug_output,
            )
            if not isinstance(additional_instruction, str) or "\x00" in additional_instruction:
                raise PromptEnhancerError("additional_instruction must be a string without NUL")
            style = load_style_profile(effective_style)
            background = load_background_profile(effective_background)
            source = inspect_prompt(source_markdown, "source_markdown")
            needs_inference = not background.passthrough or (
                not style.passthrough and bool(source.common)
            )
            for active, name, value in (
                (model_overridden, "model_name", effective_model),
                (style_overridden, "style_profile", effective_style),
                (background_overridden, "background_detail", effective_background),
            ):
                if active:
                    LOGGER.info(
                        "[cl_prompt_enhancer] Using external %s_override: %s", name, value
                    )
            settings = {
                "semantic_guard": semantic_guard,
                "model_name": effective_model,
                "chat_format": chat_format,
                "style_profile": effective_style,
                "background_detail": effective_background,
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
                "retry_max": retry_max,
            }
            events: list[dict[str, Any]] = []
            enhanced: str | None = None
            report: dict[str, Any] | None = None
            progress_bar = (
                _ComfyProgressBar(max_tokens) if _ComfyProgressBar is not None else None
            )

            def progress(_label: str, current: int) -> None:
                if progress_bar is not None:
                    progress_bar.update_absolute(min(current, max_tokens), max_tokens)

            try:
                if needs_inference:
                    path = resolve_model_name(
                        effective_model, log_name="cl_prompt_enhancer"
                    )
                    self._backend.ensure_loaded(
                        path,
                        n_ctx=n_ctx,
                        gpu_layers=gpu_layers,
                        n_batch=n_batch,
                        flash_attn=flash_attn,
                        kv_cache_type=kv_cache_type,
                        op_offload=op_offload,
                        chat_format=_CHAT_FORMATS[chat_format],
                    )
                result = enhance_reduced_markdown(
                    source_markdown,
                    user_prompt,
                    style=style,
                    background=background,
                    backend=self._backend if needs_inference else None,
                    max_tokens=max_tokens,
                    temperature=float(temperature),
                    top_p=float(top_p),
                    repetition_penalty=float(repetition_penalty),
                    seed=seed,
                    retry_max=retry_max,
                    additional_instruction=additional_instruction,
                    progress_callback=progress,
                    interrupt_callback=_throw_if_interrupted,
                    debug_events=events,
                    semantic_guard=semantic_guard,
                )
                enhanced = result.markdown
                report = dict(result.report)
                report["model_name"] = effective_model if needs_inference else None
                report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                status = (
                    f"enhanced global prompt; style={effective_style}; "
                    f"background={effective_background}; "
                    f"user_lines={report['user_lines_preserved']}; "
                    f"added={report['style_lines_added'] + report['background_lines_added']}; "
                    f"removed_auto={len(report['removed_source_common_ids'])}; "
                    f"LLM requests={result.request_count}; retries={result.retry_count}"
                )
                log_node_success(LOGGER, "cl_prompt_enhancer", "%s", status)
                if save_debug_output:
                    path = save_enhancer_debug_bundle(
                        source_markdown=source_markdown,
                        user_prompt=user_prompt,
                        settings=settings,
                        events=result.events,
                        enhanced_markdown=enhanced,
                        report=report,
                        error=None,
                    )
                    LOGGER.info("[cl_prompt_enhancer] Saved debug output: %s", path)
                return enhanced, report_text, status
            except Exception as exc:
                if save_debug_output:
                    try:
                        path = save_enhancer_debug_bundle(
                            source_markdown=source_markdown,
                            user_prompt=user_prompt,
                            settings=settings,
                            events=tuple(events),
                            enhanced_markdown=enhanced,
                            report=report,
                            error=exc,
                        )
                        LOGGER.info("[cl_prompt_enhancer] Saved debug output: %s", path)
                    except Exception as debug_exc:
                        LOGGER.warning(
                            "[cl_prompt_enhancer] Could not save debug output: %s", debug_exc
                        )
                raise
            finally:
                if not keep_model_loaded:
                    self.clear_model()
