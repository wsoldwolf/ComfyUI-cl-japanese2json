"""ComfyUI V1 node wrapper for the cl_japanese2json compiler."""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

from .compiler.errors import CLJapaneseToJSONError
from .compiler.jsongen import generate_json, validate_final_json
from .compiler.llmj2e import translate_markdown
from .compiler.mdparse import parse_markdown
from .debug_output import save_debug_bundle
from .llama_backend import LlamaBackend
from .model_discovery import discover_model_names, resolve_model_name
from .system_prompt import load_system_prompt, system_prompt_fingerprint


LOGGER = logging.getLogger("cl_japanese2json")


class CLJapaneseToJSONGGUF:
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("json_text",)
    FUNCTION = "compile_json"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    def __init__(self) -> None:
        self._backend = LlamaBackend()
        self.last_json_text: str | None = None
        self._lock = threading.RLock()

    @property
    def llm(self) -> Any | None:
        return self._backend.llm

    @property
    def current_model_signature(self) -> tuple[Any, ...] | None:
        return self._backend.current_model_signature

    @classmethod
    def discover_model_names(cls) -> list[str]:
        return discover_model_names()

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        model_names = cls.discover_model_names()
        return {
            "required": {
                "plain_text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "Japanese reduced Markdown compiled into MiniMax H3 Contex-Loop JSON.",
                    },
                ),
                "model_name": (
                    model_names,
                    {
                        "default": model_names[0],
                        "tooltip": "GGUF text model discovered below ComfyUI/models/LLM/GGUF.",
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
                "keep_model_loaded": ("BOOLEAN", {"default": True}),
                "seed": (
                    "INT",
                    {"default": 1, "min": 1, "max": 4294967295},
                ),
                "keep_last_prompt": ("BOOLEAN", {"default": False}),
                "steps": (
                    "INT",
                    {"default": 8, "min": 1, "max": 10000, "step": 1},
                ),
            },
            "optional": {
                "save_debug_output": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Save source, protected requests, raw LLM responses, and validation results below ComfyUI/output/cl_japanese2json_debug.",
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, model_name: str, **_: Any) -> tuple[Any, ...]:
        prompt_fingerprint = system_prompt_fingerprint()
        try:
            path = resolve_model_name(model_name)
            stat = path.stat()
            model_fingerprint: tuple[Any, ...] = (
                str(path.resolve()),
                stat.st_size,
                stat.st_mtime_ns,
            )
        except Exception as exc:
            error_digest = hashlib.sha256(
                f"{model_name}|{type(exc).__name__}|{exc}".encode("utf-8", "replace")
            ).hexdigest()
            model_fingerprint = ("model-resolution-error", model_name, error_digest)
        return model_fingerprint + prompt_fingerprint

    @staticmethod
    def _validate_parameters(
        plain_text: str,
        *,
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
        keep_last_prompt: bool,
        steps: int,
        save_debug_output: bool,
    ) -> None:
        if not isinstance(plain_text, str) or plain_text.strip() == "":
            raise CLJapaneseToJSONError("plain_text must contain Japanese reduced Markdown")

        integer_ranges = {
            "max_tokens": (max_tokens, 32, 16384),
            "gpu_layers": (gpu_layers, -1, 1000),
            "n_batch": (n_batch, 32, 4096),
            "n_ctx": (n_ctx, 0, 131072),
            "seed": (seed, 1, 4294967295),
            "steps": (steps, 1, 10000),
        }
        for name, (value, minimum, maximum) in integer_ranges.items():
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise CLJapaneseToJSONError(
                    f"{name} must be an integer between {minimum} and {maximum}"
                )
        float_ranges = {
            "temperature": (temperature, 0.1, 1.0),
            "top_p": (top_p, 0.0, 1.0),
            "repetition_penalty": (repetition_penalty, 0.5, 2.0),
        }
        for name, (value, minimum, maximum) in float_ranges.items():
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not minimum <= float(value) <= maximum
            ):
                raise CLJapaneseToJSONError(
                    f"{name} must be between {minimum} and {maximum}"
                )
        for name, value in {
            "flash_attn": flash_attn,
            "op_offload": op_offload,
            "keep_model_loaded": keep_model_loaded,
            "keep_last_prompt": keep_last_prompt,
            "save_debug_output": save_debug_output,
        }.items():
            if not isinstance(value, bool):
                raise CLJapaneseToJSONError(f"{name} must be Boolean")
        if kv_cache_type not in {"q8_0", "f16"}:
            raise CLJapaneseToJSONError("kv_cache_type must be q8_0 or f16")

    def clear_model(self) -> None:
        self._backend.clear_model()

    @staticmethod
    def _save_debug_output(
        *,
        plain_text: str,
        system_prompt: str,
        model_name: str,
        settings: dict[str, Any],
        events: list[dict[str, Any]],
        canonical_markdown: str | None,
        json_text: str | None,
        error: Exception | None,
    ) -> None:
        try:
            path = save_debug_bundle(
                plain_text=plain_text,
                system_prompt=system_prompt,
                model_name=model_name,
                settings=settings,
                events=events,
                canonical_markdown=canonical_markdown,
                json_text=json_text,
                error=error,
            )
        except Exception as exc:
            LOGGER.warning(
                "[cl_japanese2json] Could not save debug output: %s", exc
            )
            return
        LOGGER.info("[cl_japanese2json] Saved debug output: %s", path)

    def compile_json(
        self,
        plain_text: str,
        model_name: str,
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
        keep_last_prompt: bool,
        steps: int = 8,
        save_debug_output: bool = False,
    ) -> tuple[str]:
        with self._lock:
            if keep_last_prompt and self.last_json_text is not None:
                LOGGER.info("[cl_japanese2json] Returning cached last JSON")
                return (self.last_json_text,)

            debug_events: list[dict[str, Any]] = []
            system_prompt = ""
            canonical: str | None = None
            json_text: str | None = None
            settings = {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "repetition_penalty": repetition_penalty,
                "gpu_layers": gpu_layers,
                "n_batch": n_batch,
                "n_ctx": n_ctx,
                "flash_attn": flash_attn,
                "kv_cache_type": kv_cache_type,
                "op_offload": op_offload,
                "keep_model_loaded": keep_model_loaded,
                "seed": seed,
                "keep_last_prompt": keep_last_prompt,
                "steps": steps,
                "save_debug_output": save_debug_output,
            }
            try:
                self._validate_parameters(
                    plain_text,
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
                    keep_last_prompt=keep_last_prompt,
                    steps=steps,
                    save_debug_output=save_debug_output,
                )
                system_prompt = load_system_prompt()
                model_path = resolve_model_name(model_name)
                self._backend.ensure_loaded(
                    model_path,
                    n_ctx=n_ctx,
                    gpu_layers=gpu_layers,
                    n_batch=n_batch,
                    flash_attn=flash_attn,
                    kv_cache_type=kv_cache_type,
                    op_offload=op_offload,
                )
                canonical = translate_markdown(
                    plain_text,
                    self._backend,
                    system_prompt,
                    max_tokens=max_tokens,
                    temperature=float(temperature),
                    top_p=float(top_p),
                    repetition_penalty=float(repetition_penalty),
                    seed=seed,
                    debug_events=(
                        debug_events if save_debug_output is True else None
                    ),
                )
                emd = parse_markdown(canonical)
                json_text = generate_json(emd, steps=steps)
                validate_final_json(json_text)
                self.last_json_text = json_text
                LOGGER.info(
                    "[cl_japanese2json] Generated %d scene(s)", len(emd.scenes)
                )
                if save_debug_output is True:
                    self._save_debug_output(
                        plain_text=plain_text,
                        system_prompt=system_prompt,
                        model_name=model_name,
                        settings=settings,
                        events=debug_events,
                        canonical_markdown=canonical,
                        json_text=json_text,
                        error=None,
                    )
                return (json_text,)
            except Exception as exc:
                if save_debug_output is True:
                    self._save_debug_output(
                        plain_text=plain_text,
                        system_prompt=system_prompt,
                        model_name=model_name,
                        settings=settings,
                        events=debug_events,
                        canonical_markdown=canonical,
                        json_text=json_text,
                        error=exc,
                    )
                self.clear_model()
                raise
            finally:
                if not keep_model_loaded:
                    self.clear_model()
