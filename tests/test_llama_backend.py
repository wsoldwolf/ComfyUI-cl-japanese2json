from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from .helpers import module


backend_module = module("llama_backend")
errors = module("compiler.errors")


class FakeLlamaModule:
    GGML_TYPE_Q8_0 = "Q8"
    GGML_TYPE_F16 = "F16"


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
            "response_format": response_format,
            "enable_thinking": enable_thinking,
            "chat_template_kwargs": chat_template_kwargs,
            "reasoning": reasoning,
        }
        return {"choices": [{"message": {"content": "ok"}}]}


class LlamaBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLoadedLlama.instances.clear()

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
                response_format={"type": "json_object"},
            )
            self.assertEqual(loaded.reset_count, 1)
            self.assertIs(loaded.completion_kwargs["enable_thinking"], False)
            self.assertEqual(
                loaded.completion_kwargs["response_format"],
                {"type": "json_object"},
            )
            self.assertEqual(
                loaded.completion_kwargs["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            self.assertIs(loaded.completion_kwargs["reasoning"], False)

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
