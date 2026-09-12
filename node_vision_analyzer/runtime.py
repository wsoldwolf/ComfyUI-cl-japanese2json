"""Vision-specific llama-cpp-python lifecycle using an MTMD projector."""

from __future__ import annotations

import gc
import logging
from pathlib import Path
import threading
from typing import Any

from ..common.errors import ModelLoadError
from ..common.gguf.runtime import (
    LlamaBackend,
)
from .discovery import file_fingerprint


LOGGER = logging.getLogger("cl_vision_analyzer")
_DEFAULT = object()
_NATIVE_OUTPUT_LOCK = threading.RLock()

try:
    import llama_cpp as _llama_cpp  # type: ignore
    from llama_cpp.llama_chat_format import (  # type: ignore
        MTMDChatHandler as _MTMDChatHandler,
    )
    from llama_cpp._utils import (  # type: ignore
        suppress_stdout_stderr as _suppress_native_output,
    )

    VISION_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    _llama_cpp = None
    _MTMDChatHandler = None
    _suppress_native_output = None
    VISION_IMPORT_ERROR = exc


def llama_cpp_version() -> str:
    return str(getattr(_llama_cpp, "__version__", "unavailable"))


def _call_mtmd_quietly(call: Any, call_kwargs: dict[str, Any]) -> Any:
    """Run MTMD prompt/image preprocessing without raw native console dumps."""

    if _suppress_native_output is None:
        return call(**call_kwargs)
    # llama-cpp-python redirects process-level stdout/stderr for this context,
    # so serialize the short MTMD preprocessing call across Vision instances.
    with _NATIVE_OUTPUT_LOCK:
        with _suppress_native_output(disable=False):
            return call(**call_kwargs)


class VisionBackend(LlamaBackend):
    """A Llama backend whose model signature includes its MTMD projector."""

    def __init__(
        self,
        *,
        llama_module: Any = _DEFAULT,
        llama_class: Any = _DEFAULT,
        handler_class: Any = _DEFAULT,
    ) -> None:
        super().__init__(
            llama_module=(
                _llama_cpp if llama_module is _DEFAULT else llama_module
            ),
            llama_class=(
                getattr(_llama_cpp, "Llama", None)
                if llama_class is _DEFAULT
                else llama_class
            ),
            log_name="cl_vision_analyzer",
        )
        self.handler_class = (
            _MTMDChatHandler if handler_class is _DEFAULT else handler_class
        )
        self.chat_handler: Any | None = None
        self.current_projector_path: Path | None = None

    def ensure_loaded(
        self,
        model_path: Path,
        projector_path: Path,
        *,
        n_ctx: int,
        gpu_layers: int,
        n_batch: int,
        flash_attn: bool,
        kv_cache_type: str,
        op_offload: bool,
    ) -> Any:
        try:
            signature = (
                *file_fingerprint(model_path),
                *file_fingerprint(projector_path),
                int(n_ctx),
                int(gpu_layers),
                int(n_batch),
                bool(flash_attn),
                str(kv_cache_type),
                bool(op_offload),
                "mtmd",
            )
        except OSError as exc:
            raise ModelLoadError(
                "Vision model or projector cannot be read"
            ) from exc

        if self.llm is not None and self.current_model_signature == signature:
            return self.llm
        self.clear_model()
        if self.llama_class is None or self.handler_class is None:
            detail = f": {VISION_IMPORT_ERROR}" if VISION_IMPORT_ERROR else ""
            raise ModelLoadError(
                "Installed llama-cpp-python does not provide MTMDChatHandler. "
                "Install a Vision-capable llama-cpp-python build" + detail
            )

        kv_type = self._kv_type(kv_cache_type)
        handler: Any | None = None
        try:
            LOGGER.info(
                "[cl_vision_analyzer] Loading Vision model: %s + %s",
                model_path.name,
                projector_path.name,
            )
            LOGGER.info(
                "[cl_vision_analyzer] n_ctx=%d gpu_layers=%d n_batch=%d "
                "flash_attn=%s kv_cache=%s op_offload=%s",
                n_ctx,
                gpu_layers,
                n_batch,
                flash_attn,
                kv_cache_type,
                op_offload,
            )
            handler = self.handler_class(
                clip_model_path=str(projector_path.resolve(strict=True)),
                verbose=False,
                use_gpu=gpu_layers != 0,
            )
            loaded = self.llama_class(
                model_path=str(model_path.resolve(strict=True)),
                n_ctx=n_ctx,
                n_gpu_layers=gpu_layers,
                n_batch=n_batch,
                flash_attn=flash_attn,
                type_k=kv_type,
                type_v=kv_type,
                offload_kqv=True,
                op_offload=op_offload,
                chat_handler=handler,
                verbose=False,
            )
        except Exception as exc:
            self._close_handler(handler)
            self.clear_model()
            raise ModelLoadError(
                f"Failed to load Vision pair {model_path.name!r} + "
                f"{projector_path.name!r} (n_ctx={n_ctx}, n_batch={n_batch}, "
                f"gpu_layers={gpu_layers})"
            ) from exc

        self.chat_handler = handler
        self.llm = loaded
        self.current_model_signature = signature
        self.current_model_path = model_path.resolve(strict=True)
        self.current_projector_path = projector_path.resolve(strict=True)
        return loaded

    @staticmethod
    def _close_handler(handler: Any | None) -> None:
        if handler is None:
            return
        close = getattr(handler, "close", None)
        if callable(close):
            close()
            return
        exit_stack = getattr(handler, "_exit_stack", None)
        stack_close = getattr(exit_stack, "close", None)
        if callable(stack_close):
            stack_close()

    def clear_model(self) -> None:
        handler = self.chat_handler
        self.chat_handler = None
        self.current_projector_path = None
        try:
            self._close_handler(handler)
        except Exception as exc:
            LOGGER.warning(
                "[cl_vision_analyzer] Projector close raised an error: %s",
                exc,
            )
        super().clear_model()
        gc.collect()

    def count_input_tokens(self, messages: list[dict[str, Any]]) -> int:
        text_parts: list[str] = []
        image_count = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and isinstance(
                        part.get("text"), str
                    ):
                        text_parts.append(part["text"])
                    elif part.get("type") == "image_url":
                        image_count += 1
        text = "\n".join(text_parts)
        tokenizer = getattr(self.llm, "tokenize", None)
        if callable(tokenizer):
            try:
                return (
                    len(tokenizer(text.encode("utf-8"), add_bos=True))
                    + image_count * 1024
                    + 64
                )
            except (TypeError, ValueError, RuntimeError):
                pass
        return max(1, (len(text.encode("utf-8")) + 2) // 3) + image_count * 1024

    def complete_chat(self, **kwargs: Any) -> Any:
        if self.llm is None:
            raise ModelLoadError("No Vision GGUF model is loaded")
        reset = getattr(self.llm, "reset", None)
        if callable(reset):
            reset()
        call = getattr(self.llm, "create_chat_completion", None)
        if not callable(call):
            raise ModelLoadError(
                "Loaded Vision model does not provide create_chat_completion()"
            )

        call_kwargs = dict(kwargs)
        progress_callback = call_kwargs.pop("progress_callback", None)
        interrupt_callback = call_kwargs.pop("interrupt_callback", None)
        if progress_callback is not None and not callable(progress_callback):
            raise ModelLoadError("progress_callback must be callable")
        if interrupt_callback is not None and not callable(interrupt_callback):
            raise ModelLoadError("interrupt_callback must be callable")
        if interrupt_callback is not None:
            interrupt_callback()
        native_abort = self._install_native_abort_bridge(interrupt_callback)
        try:
            if progress_callback is not None:
                call_kwargs["stream"] = True
            # llama-cpp-python''s MTMD create_chat_completion path does not
            # currently expose stopping_criteria.  Native abort polling and
            # the streamed collector still check the ComfyUI interrupt at
            # decode boundaries without passing an unsupported keyword.
            try:
                response = _call_mtmd_quietly(call, call_kwargs)
                if progress_callback is not None and not isinstance(response, dict):
                    response = self._collect_streamed_chat_completion(
                        response,
                        messages=call_kwargs.get("messages", []),
                        progress_callback=progress_callback,
                        interrupt_callback=interrupt_callback,
                    )
            except TypeError as exc:
                raise ModelLoadError(
                    "llama-cpp-python rejected the MTMD chat-completion "
                    "arguments; check backend compatibility"
                ) from exc
        except BaseException:
            if native_abort is not None:
                native_abort.raise_if_requested()
            raise
        else:
            if native_abort is not None:
                native_abort.raise_if_requested()
            return response
        finally:
            if native_abort is not None:
                native_abort.disable()
