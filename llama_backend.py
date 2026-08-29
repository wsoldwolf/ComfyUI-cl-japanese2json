"""Independent llama-cpp-python model lifecycle and inference wrapper.

The API shape is informed by ComfyUI-QwenVL-Mod, but this is an independent
implementation and imports no code from that project.
"""

from __future__ import annotations

import gc
import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from .compiler.errors import ModelLoadError


LOGGER = logging.getLogger("cl_japanese2json")

try:
    import llama_cpp as _llama_cpp  # type: ignore
    from llama_cpp import Llama as _Llama  # type: ignore

    LLAMA_CPP_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    _llama_cpp = None
    _Llama = None
    LLAMA_CPP_IMPORT_ERROR = exc


_DEFAULT = object()


def model_signature(
    model_path: Path,
    *,
    n_ctx: int,
    gpu_layers: int,
    n_batch: int,
    flash_attn: bool,
    kv_cache_type: str,
    op_offload: bool,
) -> tuple[Any, ...]:
    resolved = model_path.resolve(strict=True)
    stat = resolved.stat()
    return (
        str(resolved),
        stat.st_size,
        stat.st_mtime_ns,
        int(n_ctx),
        int(gpu_layers),
        int(n_batch),
        bool(flash_attn),
        str(kv_cache_type),
        bool(op_offload),
    )


class LlamaBackend:
    def __init__(
        self,
        *,
        llama_module: Any = _DEFAULT,
        llama_class: Callable[..., Any] | None | object = _DEFAULT,
    ) -> None:
        self.llama_module = _llama_cpp if llama_module is _DEFAULT else llama_module
        self.llama_class = _Llama if llama_class is _DEFAULT else llama_class
        self.llm: Any | None = None
        self.current_model_signature: tuple[Any, ...] | None = None
        self.current_model_path: Path | None = None

    def _kv_type(self, selection: str) -> Any:
        if selection not in {"q8_0", "f16"}:
            raise ModelLoadError(f"Unsupported kv_cache_type: {selection!r}")
        if self.llama_module is None:
            detail = f": {LLAMA_CPP_IMPORT_ERROR}" if LLAMA_CPP_IMPORT_ERROR else ""
            raise ModelLoadError(
                "llama-cpp-python is not installed or failed to import. "
                "Install a suitable CUDA or CPU build manually" + detail
            )
        constant_name = "GGML_TYPE_Q8_0" if selection == "q8_0" else "GGML_TYPE_F16"
        if not hasattr(self.llama_module, constant_name):
            raise ModelLoadError(
                f"Installed llama-cpp-python does not expose {constant_name}; "
                "a compatible version is required"
            )
        return getattr(self.llama_module, constant_name)

    def ensure_loaded(
        self,
        model_path: Path,
        *,
        n_ctx: int,
        gpu_layers: int,
        n_batch: int,
        flash_attn: bool,
        kv_cache_type: str,
        op_offload: bool,
    ) -> Any:
        try:
            signature = model_signature(
                model_path,
                n_ctx=n_ctx,
                gpu_layers=gpu_layers,
                n_batch=n_batch,
                flash_attn=flash_attn,
                kv_cache_type=kv_cache_type,
                op_offload=op_offload,
            )
        except OSError as exc:
            raise ModelLoadError(f"GGUF model cannot be read: {model_path}") from exc

        if self.llm is not None and signature == self.current_model_signature:
            return self.llm
        self.clear_model()
        if self.llama_class is None:
            detail = f": {LLAMA_CPP_IMPORT_ERROR}" if LLAMA_CPP_IMPORT_ERROR else ""
            raise ModelLoadError(
                "llama-cpp-python is not installed or failed to import. "
                "Install the desired backend manually" + detail
            )
        kv_type = self._kv_type(kv_cache_type)
        kwargs = {
            "model_path": str(model_path.resolve(strict=True)),
            "n_ctx": n_ctx,
            "n_gpu_layers": gpu_layers,
            "n_batch": n_batch,
            "flash_attn": flash_attn,
            "type_k": kv_type,
            "type_v": kv_type,
            "offload_kqv": True,
            "op_offload": op_offload,
            "chat_format": "qwen",
            "verbose": False,
        }
        LOGGER.info("[cl_japanese2json] Loading model: %s", model_path.name)
        LOGGER.info(
            "[cl_japanese2json] n_ctx=%d gpu_layers=%d n_batch=%d flash_attn=%s kv_cache=%s op_offload=%s",
            n_ctx,
            gpu_layers,
            n_batch,
            flash_attn,
            kv_cache_type,
            op_offload,
        )
        try:
            loaded = self.llama_class(**kwargs)
        except Exception as exc:
            self.clear_model()
            raise ModelLoadError(
                f"Failed to load {model_path.name!r} with chat_format='qwen' "
                f"(n_ctx={n_ctx}, n_batch={n_batch}, gpu_layers={gpu_layers})"
            ) from exc
        self.llm = loaded
        self.current_model_signature = signature
        self.current_model_path = model_path.resolve(strict=True)
        return loaded

    def clear_model(self) -> None:
        old = self.llm
        self.llm = None
        self.current_model_signature = None
        self.current_model_path = None
        if old is not None:
            LOGGER.info("[cl_japanese2json] Unloading model")
            try:
                close = getattr(old, "close", None)
                if callable(close):
                    close()
                else:
                    finalizer = getattr(old, "__del__", None)
                    if callable(finalizer):
                        finalizer()
            except Exception as exc:
                LOGGER.warning("[cl_japanese2json] Model close raised an error: %s", exc)
        gc.collect()
        try:  # Torch is optional and is never installed by this package.
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                try:
                    torch.cuda.ipc_collect()
                except Exception:
                    pass
        except Exception:
            pass

    def effective_n_ctx(self) -> int | None:
        if self.llm is None:
            return None
        accessor = getattr(self.llm, "n_ctx", None)
        value = accessor() if callable(accessor) else accessor
        return value if isinstance(value, int) and value > 0 else None

    def count_input_tokens(self, messages: list[dict[str, str]]) -> int:
        if self.llm is None:
            return 0
        combined = "\n".join(message["content"] for message in messages)
        tokenizer = getattr(self.llm, "tokenize", None)
        if callable(tokenizer):
            try:
                return len(tokenizer(combined.encode("utf-8"), add_bos=True)) + 64
            except (TypeError, ValueError, RuntimeError):
                pass
        return max(1, (len(combined.encode("utf-8")) + 2) // 3) + 64

    def is_qwen3(self) -> bool:
        if self.llm is None:
            return False
        metadata = getattr(self.llm, "metadata", {})
        metadata_text = " ".join(
            f"{key}={value}" for key, value in metadata.items()
        ) if isinstance(metadata, dict) else str(metadata)
        path_text = str(self.current_model_path or "")
        return "qwen3" in f"{metadata_text} {path_text}".casefold()

    def complete_chat(self, **kwargs: Any) -> Any:
        if self.llm is None:
            raise ModelLoadError("No GGUF model is loaded")
        reset = getattr(self.llm, "reset", None)
        if callable(reset):
            reset()

        call = getattr(self.llm, "create_chat_completion", None)
        if not callable(call):
            raise ModelLoadError("Loaded model does not provide create_chat_completion()")

        call_kwargs = dict(kwargs)
        if self.is_qwen3():
            try:
                parameters = inspect.signature(call).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "enable_thinking" in parameters:
                call_kwargs["enable_thinking"] = False
            if "chat_template_kwargs" in parameters:
                call_kwargs["chat_template_kwargs"] = {"enable_thinking": False}
            if "reasoning" in parameters:
                call_kwargs["reasoning"] = False
        try:
            return call(**call_kwargs)
        except TypeError as exc:
            raise ModelLoadError(
                "llama-cpp-python rejected the Qwen chat-completion arguments; "
                "check backend compatibility"
            ) from exc
