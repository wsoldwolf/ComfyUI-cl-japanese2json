from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from .helpers import module


backend_module = module("common.gguf.runtime")
errors = module("common.errors")
compiler_errors = module("node_japanese_to_json.compiler.errors")


class FakeLlamaModule:
    GGML_TYPE_Q8_0 = "Q8"
    GGML_TYPE_F16 = "F16"


class FakeAbortLlamaModule(FakeLlamaModule):
    abort_callback = None
    abort_context = None

    @staticmethod
    def ggml_abort_callback(callback):
        return callback

    @classmethod
    def llama_set_abort_callback(cls, context, callback, _data):
        cls.abort_context = context
        cls.abort_callback = callback


class FakeLoadedLlama:
    instances: list["FakeLoadedLlama"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.close_count = 0
        self.reset_count = 0
        self.metadata = {"general.name": "Qwen3 test"}
        self.completion_kwargs = None
        self.__class__.instances.append(self)

    def close(self):
        self.close_count += 1

    def reset(self):
        self.reset_count += 1

    def n_ctx(self):
        return 8192

    def tokenize(self, data, add_bos=True):
        return [1, 2, 3]

    def create_chat_completion(
        self,
        *,
        messages,
        max_tokens,
        temperature,
        top_p,
        repeat_penalty,
        seed,
        stop=None,
        response_format=None,
        enable_thinking=True,
        chat_template_kwargs=None,
        reasoning=True,
    ):
        self.completion_kwargs = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "seed": seed,
            "stop": stop,
            "response_format": response_format,
            "enable_thinking": enable_thinking,
            "chat_template_kwargs": chat_template_kwargs,
            "reasoning": reasoning,
        }
        return {"choices": [{"message": {"content": "ok"}}]}


class FakeStreamingLoadedLlama(FakeLoadedLlama):
    def create_chat_completion(
        self,
        *,
        messages,
        max_tokens,
        temperature,
        top_p,
        repeat_penalty,
        seed,
        stop=None,
        response_format=None,
        enable_thinking=True,
        chat_template_kwargs=None,
        reasoning=True,
        stream=False,
    ):
        self.completion_kwargs = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "seed": seed,
            "stop": stop,
            "response_format": response_format,
            "enable_thinking": enable_thinking,
            "chat_template_kwargs": chat_template_kwargs,
            "reasoning": reasoning,
            "stream": stream,
        }
        if not stream:
            return {"choices": [{"message": {"content": "ok"}}]}
        return iter(
            [
                {
                    "choices": [
                        {
                            "delta": {"role": "assistant", "content": "Hello"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": " world"},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 2,
                        "total_tokens": 9,
                    },
                },
            ]
        )


class FakeRawCompletionLlama(FakeLoadedLlama):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text_completion_kwargs = None

    def create_completion(self, **kwargs):
        self.text_completion_kwargs = kwargs
        if kwargs.get("stream"):
            return iter(
                [
                    {
                        "choices": [{"text": "Translated", "finish_reason": None}]
                    },
                    {"choices": [{"text": " stream", "finish_reason": None}]},
                    {"choices": [{"text": "", "finish_reason": "stop"}]},
                ]
            )
        return {
            "choices": [{"text": "Translated stream", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }


class FakeNativeAbortRawCompletionLlama(FakeRawCompletionLlama):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ctx = object()

    def create_completion(self, **kwargs):
        self.text_completion_kwargs = kwargs
        if FakeAbortLlamaModule.abort_callback(None):
            raise RuntimeError("llama_decode returned 2")
        return super().create_completion(**kwargs)


class LlamaBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLoadedLlama.instances.clear()
        FakeAbortLlamaModule.abort_callback = None
        FakeAbortLlamaModule.abort_context = None

    def _load(self, backend, path: Path, **overrides):
        settings = {
            "n_ctx": 0,
            "gpu_layers": -1,
            "n_batch": 256,
            "flash_attn": True,
            "kv_cache_type": "q8_0",
            "op_offload": True,
        }
        settings.update(overrides)
        return backend.ensure_loaded(path, **settings)

    def test_parameter_mapping_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"gguf")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeLoadedLlama,
            )
            first = self._load(backend, path)
            second = self._load(backend, path)
            self.assertIs(first, second)
            self.assertEqual(len(FakeLoadedLlama.instances), 1)
            kwargs = first.kwargs
            self.assertEqual(kwargs["n_gpu_layers"], -1)
            self.assertEqual(kwargs["n_ctx"], 0)
            self.assertEqual(kwargs["type_k"], "Q8")
            self.assertEqual(kwargs["type_v"], "Q8")
            self.assertIs(kwargs["op_offload"], True)
            self.assertTrue(kwargs["offload_kqv"])
            self.assertEqual(kwargs["chat_format"], "qwen")

    def test_loading_setting_change_closes_old_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"gguf")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeLoadedLlama,
            )
            first = self._load(backend, path)
            second = self._load(backend, path, kv_cache_type="f16")
            self.assertIsNot(first, second)
            self.assertEqual(first.close_count, 1)
            self.assertEqual(second.kwargs["type_k"], "F16")

    def test_file_change_changes_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"a")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeLoadedLlama,
            )
            first = self._load(backend, path)
            path.write_bytes(b"larger")
            second = self._load(backend, path)
            self.assertIsNot(first, second)

    def test_clear_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeLoadedLlama,
            )
            loaded = self._load(backend, path)
            backend.clear_model()
            backend.clear_model()
            self.assertEqual(loaded.close_count, 1)
            self.assertIsNone(backend.llm)
            self.assertIsNone(backend.current_model_signature)

    def test_qwen_thinking_arguments_are_disabled_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeLoadedLlama,
            )
            loaded = self._load(backend, path)
            backend.complete_chat(
                messages=[],
                max_tokens=32,
                temperature=0.1,
                top_p=0.9,
                repeat_penalty=1.05,
                seed=1,
                stop=["CLJT0ENDX"],
            )
            self.assertEqual(loaded.reset_count, 1)
            self.assertIs(loaded.completion_kwargs["enable_thinking"], False)
            self.assertEqual(loaded.completion_kwargs["stop"], ["CLJT0ENDX"])
            self.assertIsNone(loaded.completion_kwargs["response_format"])
            self.assertEqual(
                loaded.completion_kwargs["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            self.assertIs(loaded.completion_kwargs["reasoning"], False)

    def test_qwen3_uses_strict_non_thinking_prefill_when_text_completion_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeRawCompletionLlama,
            )
            loaded = self._load(backend, path)
            response = backend.complete_chat(
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "translate /no_think"},
                ],
                max_tokens=32,
                temperature=0.1,
                top_p=0.9,
                repeat_penalty=1.05,
                seed=1,
                stop=["CLJT0ENDX"],
            )

            self.assertIsNone(loaded.completion_kwargs)
            self.assertTrue(
                loaded.text_completion_kwargs["prompt"].endswith(
                    "<|im_start|>assistant\n<think>\n\n</think>\n\n"
                )
            )
            self.assertEqual(
                loaded.text_completion_kwargs["stop"],
                ["CLJT0ENDX", "<|im_end|>", "<|endoftext|>"],
            )
            self.assertEqual(
                response["choices"][0]["message"]["content"],
                "Translated stream",
            )

    def test_streamed_qwen3_text_completion_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeRawCompletionLlama,
            )
            self._load(backend, path)
            progress = []
            response = backend.complete_chat(
                messages=[{"role": "user", "content": "translate /no_think"}],
                max_tokens=32,
                temperature=0.1,
                top_p=0.9,
                repeat_penalty=1.05,
                seed=1,
                stop=["CLJT0ENDX"],
                progress_callback=progress.append,
            )

            self.assertEqual(progress, [1, 2])
            self.assertEqual(
                response["choices"][0]["message"]["content"],
                "Translated stream",
            )

    def test_qwen3_stream_installs_and_polls_interrupt_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeRawCompletionLlama,
            )
            loaded = self._load(backend, path)
            checks = []

            backend.complete_chat(
                messages=[{"role": "user", "content": "translate /no_think"}],
                max_tokens=32,
                temperature=0.1,
                top_p=0.9,
                repeat_penalty=1.05,
                seed=1,
                stop=["CLJT0ENDX"],
                progress_callback=lambda _value: None,
                interrupt_callback=lambda: checks.append("checked") or False,
            )

            criterion = loaded.text_completion_kwargs["stopping_criteria"]
            self.assertTrue(callable(criterion))
            self.assertFalse(criterion(None, None))
            self.assertGreaterEqual(len(checks), 2)

    def test_interrupt_exception_propagates_before_inference(self) -> None:
        class TestInterrupt(BaseException):
            pass

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeRawCompletionLlama,
            )
            loaded = self._load(backend, path)

            def interrupt() -> None:
                raise TestInterrupt()

            with self.assertRaises(TestInterrupt):
                backend.complete_chat(
                    messages=[{"role": "user", "content": "translate"}],
                    max_tokens=32,
                    temperature=0.1,
                    top_p=0.9,
                    repeat_penalty=1.05,
                    seed=1,
                    progress_callback=lambda _value: None,
                    interrupt_callback=interrupt,
                )
            self.assertIsNone(loaded.text_completion_kwargs)

    def test_native_abort_rethrows_the_python_interrupt_exception(self) -> None:
        checks = 0

        def interrupt() -> bool:
            nonlocal checks
            checks += 1
            if checks >= 2:
                raise compiler_errors.InferenceStallError("test inference stalled")
            return False

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeAbortLlamaModule,
                llama_class=FakeNativeAbortRawCompletionLlama,
            )
            loaded = self._load(backend, path)

            with self.assertRaisesRegex(
                compiler_errors.InferenceStallError, "test inference stalled"
            ):
                backend.complete_chat(
                    messages=[{"role": "user", "content": "translate"}],
                    max_tokens=32,
                    temperature=0.1,
                    top_p=0.9,
                    repeat_penalty=1.05,
                    seed=1,
                    progress_callback=lambda _value: None,
                    interrupt_callback=interrupt,
                )

            self.assertIs(FakeAbortLlamaModule.abort_context, loaded.ctx)
            self.assertGreaterEqual(checks, 2)
            self.assertIsNotNone(backend._native_abort_bridge)
            self.assertFalse(backend._native_abort_bridge.enabled)

    def test_streaming_completion_reports_progress_and_rebuilds_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen3-model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=FakeStreamingLoadedLlama,
            )
            loaded = self._load(backend, path)
            progress = []
            response = backend.complete_chat(
                messages=[{"role": "user", "content": "translate"}],
                max_tokens=32,
                temperature=0.1,
                top_p=0.9,
                repeat_penalty=1.05,
                seed=1,
                stop=["CLJT0ENDX"],
                progress_callback=progress.append,
            )

            self.assertTrue(loaded.completion_kwargs["stream"])
            self.assertEqual(progress, [1, 2])
            self.assertEqual(
                response["choices"][0]["message"]["content"], "Hello world"
            )
            self.assertEqual(response["choices"][0]["finish_reason"], "stop")
            self.assertEqual(response["usage"]["total_tokens"], 9)

    def test_missing_constants_raise_compatibility_error(self) -> None:
        class OldModule:
            pass

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=OldModule,
                llama_class=FakeLoadedLlama,
            )
            with self.assertRaisesRegex(errors.ModelLoadError, "GGML_TYPE_Q8_0"):
                self._load(backend, path)

    def test_constructor_failure_leaves_empty_state(self) -> None:
        def failing(**kwargs):
            raise RuntimeError("load failed")

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.gguf"
            path.write_bytes(b"x")
            backend = backend_module.LlamaBackend(
                llama_module=FakeLlamaModule,
                llama_class=failing,
            )
            with self.assertRaises(errors.ModelLoadError):
                self._load(backend, path)
            self.assertIsNone(backend.llm)
            self.assertIsNone(backend.current_model_signature)
