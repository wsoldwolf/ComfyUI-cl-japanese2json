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

from ..errors import ModelLoadError


LOGGER = logging.getLogger("cl_japanese2json")
QWEN3_NON_THINKING_PREFILL = "<think>\n\n</think>\n\n"

try:
    import llama_cpp as _llama_cpp  # type: ignore
    from llama_cpp import Llama as _Llama  # type: ignore

    LLAMA_CPP_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    _llama_cpp = None
    _Llama = None
    LLAMA_CPP_IMPORT_ERROR = exc


_DEFAULT = object()


class _CombinedStoppingCriteria:
    """Preserve llama.cpp criteria while polling an external interrupt."""

    def __init__(
        self,
        original: Any,
        interrupt_callback: Callable[[], Any],
    ) -> None:
        if original is not None and not callable(original):
            raise ModelLoadError("stopping_criteria must be callable")
        self.original = original
        self.interrupt_callback = interrupt_callback

    def __call__(self, input_ids: Any, logits: Any) -> bool:
        if self.original is not None and self.original(input_ids, logits):
            return True
        return bool(self.interrupt_callback())


class _NativeAbortBridge:
    """Convert Python interrupts into llama.cpp's native decode abort signal."""

    def __init__(self, callback: Callable[[], Any]) -> None:
        self.callback = callback
        self.exception: BaseException | None = None
        self.requested = False
        self.enabled = True

    def poll(self, _data: Any) -> bool:
        if not self.enabled:
            return False
        try:
            requested = bool(self.callback())
        except BaseException as exc:
            self.exception = exc
            requested = True
        if requested:
            self.requested = True
        return requested

    def raise_if_requested(self) -> None:
        if self.exception is not None:
            raise self.exception
        if self.requested:
            raise ModelLoadError("llama.cpp inference was aborted")

    def disable(self) -> None:
        self.enabled = False


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
        # llama.cpp retains the C callback pointer.  Keep both Python objects
        # alive until the context is closed or a replacement is installed.
        self._native_abort_bridge: _NativeAbortBridge | None = None
        self._native_abort_callback: Any | None = None

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
        self._native_abort_bridge = None
        self._native_abort_callback = None
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

    def _install_native_abort_bridge(
        self,
        interrupt_callback: Callable[[], Any] | None,
    ) -> _NativeAbortBridge | None:
        """Install an abort callback that is polled inside native decode work."""

        if interrupt_callback is None or self.llm is None:
            return None
        callback_type = getattr(self.llama_module, "ggml_abort_callback", None)
        setter = getattr(self.llama_module, "llama_set_abort_callback", None)
        context = getattr(self.llm, "ctx", None)
        if context is None:
            internal_context = getattr(self.llm, "_ctx", None)
            context = getattr(internal_context, "ctx", None)
        if not callable(callback_type) or not callable(setter) or context is None:
            return None

        bridge = _NativeAbortBridge(interrupt_callback)
        native_callback = callback_type(bridge.poll)
        previous_bridge = self._native_abort_bridge
        previous_callback = self._native_abort_callback
        try:
            setter(context, native_callback, None)
        except (TypeError, ValueError, RuntimeError, OSError) as exc:
            LOGGER.warning(
                "[cl_japanese2json] Could not install llama.cpp native abort "
                "callback; token-boundary interruption remains active: %s",
                exc,
            )
            return None
        self._native_abort_bridge = bridge
        self._native_abort_callback = native_callback
        if previous_bridge is not None:
            previous_bridge.disable()
        # Keep the previous callback alive until after llama.cpp has replaced
        # its pointer.  The local reference intentionally expires here.
        del previous_callback
        return bridge

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

    def _completion_token_count(self, content: str) -> int:
        if self.llm is not None:
            tokenizer = getattr(self.llm, "tokenize", None)
            if callable(tokenizer):
                try:
                    return len(tokenizer(content.encode("utf-8"), add_bos=False))
                except (TypeError, ValueError, RuntimeError):
                    pass
        return max(1, (len(content.encode("utf-8")) + 2) // 3)

    def _collect_streamed_chat_completion(
        self,
        stream: Any,
        *,
        messages: list[dict[str, str]],
        progress_callback: Callable[[int], None],
        interrupt_callback: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        content_parts: list[str] = []
        finish_reason: Any = None
        usage: dict[str, Any] | None = None
        content_chunk_count = 0

        for chunk in stream:
            if interrupt_callback is not None:
                interrupt_callback()
            if not isinstance(chunk, dict):
                raise ModelLoadError(
                    "llama-cpp-python returned an invalid streaming chat chunk"
                )
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = dict(chunk_usage)
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            payload = choice.get("delta")
            if not isinstance(payload, dict):
                payload = choice.get("message")
            if isinstance(payload, dict):
                piece = payload.get("content")
                if isinstance(piece, str) and piece:
                    content_parts.append(piece)
                    content_chunk_count += 1
                    progress_callback(content_chunk_count)
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason")

        content = "".join(content_parts)
        if usage is None:
            prompt_tokens = self.count_input_tokens(messages)
            completion_tokens = self._completion_token_count(content)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason or "stop",
                }
            ],
            "usage": usage,
        }

    @staticmethod
    def _qwen3_non_thinking_prompt(messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(
                content, str
            ):
                raise ModelLoadError(
                    "Qwen3 non-thinking completion requires plain-text "
                    "system, user, or assistant messages"
                )
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
        parts.append("<|im_start|>assistant\n")
        parts.append(QWEN3_NON_THINKING_PREFILL)
        return "".join(parts)

    @staticmethod
    def _qwen3_stop_sequences(stop: Any) -> list[str]:
        if isinstance(stop, str):
            values = [stop]
        elif isinstance(stop, list):
            values = [value for value in stop if isinstance(value, str)]
        else:
            values = []
        for value in ("<|im_end|>", "<|endoftext|>"):
            if value not in values:
                values.append(value)
        return values

    def _collect_streamed_text_completion(
        self,
        stream: Any,
        *,
        prompt: str,
        progress_callback: Callable[[int], None],
        interrupt_callback: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        content_parts: list[str] = []
        finish_reason: Any = None
        usage: dict[str, Any] | None = None
        content_chunk_count = 0

        for chunk in stream:
            if interrupt_callback is not None:
                interrupt_callback()
            if not isinstance(chunk, dict):
                raise ModelLoadError(
                    "llama-cpp-python returned an invalid streaming text chunk"
                )
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = dict(chunk_usage)
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            piece = choice.get("text")
            if isinstance(piece, str) and piece:
                content_parts.append(piece)
                content_chunk_count += 1
                progress_callback(content_chunk_count)
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason")

        content = "".join(content_parts)
        if usage is None:
            prompt_tokens = self._completion_token_count(prompt)
            completion_tokens = self._completion_token_count(content)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason or "stop",
                }
            ],
            "usage": usage,
        }

    def _complete_qwen3_without_thinking(
        self,
        call_kwargs: dict[str, Any],
        *,
        progress_callback: Callable[[int], None] | None,
        interrupt_callback: Callable[[], Any] | None,
    ) -> Any:
        if self.llm is None:
            raise ModelLoadError("No GGUF model is loaded")
        completion = getattr(self.llm, "create_completion", None)
        if not callable(completion):
            return None

        messages = call_kwargs.pop("messages", None)
        if not isinstance(messages, list):
            raise ModelLoadError("Qwen3 completion requires a messages list")
        prompt = self._qwen3_non_thinking_prompt(messages)
        call_kwargs["prompt"] = prompt
        call_kwargs["stop"] = self._qwen3_stop_sequences(call_kwargs.get("stop"))
        if interrupt_callback is not None:
            call_kwargs["stopping_criteria"] = _CombinedStoppingCriteria(
                call_kwargs.get("stopping_criteria"), interrupt_callback
            )
        if progress_callback is not None:
            call_kwargs["stream"] = True

        LOGGER.info(
            "[cl_japanese2json] Using strict Qwen3 non-thinking assistant prefill"
        )
        response = completion(**call_kwargs)
        if progress_callback is not None and not isinstance(response, dict):
            return self._collect_streamed_text_completion(
                response,
                prompt=prompt,
                progress_callback=progress_callback,
                interrupt_callback=interrupt_callback,
            )
        if not isinstance(response, dict):
            raise ModelLoadError("llama-cpp-python returned an invalid text completion")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(
            choices[0], dict
        ):
            raise ModelLoadError("llama-cpp-python text completion has no choices")
        choice = choices[0]
        content = choice.get("text")
        if not isinstance(content, str):
            raise ModelLoadError("llama-cpp-python text completion has no text")
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": choice.get("finish_reason") or "stop",
                }
            ],
            "usage": response.get("usage"),
        }

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
        progress_callback = call_kwargs.pop("progress_callback", None)
        if progress_callback is not None and not callable(progress_callback):
            raise ModelLoadError("progress_callback must be callable")
        interrupt_callback = call_kwargs.pop("interrupt_callback", None)
        if interrupt_callback is not None and not callable(interrupt_callback):
            raise ModelLoadError("interrupt_callback must be callable")
        if interrupt_callback is not None:
            interrupt_callback()
        native_abort = self._install_native_abort_bridge(interrupt_callback)
        try:
            is_qwen3 = self.is_qwen3()
            if is_qwen3 and callable(getattr(self.llm, "create_completion", None)):
                try:
                    response = self._complete_qwen3_without_thinking(
                        call_kwargs,
                        progress_callback=progress_callback,
                        interrupt_callback=interrupt_callback,
                    )
                except TypeError as exc:
                    raise ModelLoadError(
                        "llama-cpp-python rejected the strict Qwen3 non-thinking "
                        "text-completion arguments; check backend compatibility"
                    ) from exc
            else:
                if is_qwen3:
                    try:
                        parameters = inspect.signature(call).parameters
                    except (TypeError, ValueError):
                        parameters = {}
                    if "enable_thinking" in parameters:
                        call_kwargs["enable_thinking"] = False
                    if "chat_template_kwargs" in parameters:
                        call_kwargs["chat_template_kwargs"] = {
                            "enable_thinking": False
                        }
                    if "reasoning" in parameters:
                        call_kwargs["reasoning"] = False
                if progress_callback is not None:
                    call_kwargs["stream"] = True
                if interrupt_callback is not None:
                    call_kwargs["stopping_criteria"] = _CombinedStoppingCriteria(
                        call_kwargs.get("stopping_criteria"), interrupt_callback
                    )
                try:
                    response = call(**call_kwargs)
                    if progress_callback is not None and not isinstance(
                        response, dict
                    ):
                        response = self._collect_streamed_chat_completion(
                            response,
                            messages=call_kwargs.get("messages", []),
                            progress_callback=progress_callback,
                            interrupt_callback=interrupt_callback,
                        )
                except TypeError as exc:
                    raise ModelLoadError(
                        "llama-cpp-python rejected the Qwen chat-completion "
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
