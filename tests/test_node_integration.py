from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import FakeBackend, PKG, module


nodes = module("nodes")
errors = module("compiler.errors")


def arguments(**overrides):
    values = {
        "plain_text": "# シーン\n* 動作。",
        "model_name": "model.gguf",
        "max_tokens": 64,
        "temperature": 0.1,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
        "gpu_layers": -1,
        "n_batch": 256,
        "n_ctx": 0,
        "flash_attn": True,
        "kv_cache_type": "q8_0",
        "op_offload": True,
        "keep_model_loaded": True,
        "seed": 1,
        "keep_last_prompt": False,
        "steps": 8,
    }
    values.update(overrides)
    return values


class NodeIntegrationTests(unittest.TestCase):
    def test_registration_and_v1_metadata(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLJapaneseToJSONGGUF"]
        self.assertIs(cls, nodes.CLJapaneseToJSONGGUF)
        self.assertEqual(PKG.NODE_DISPLAY_NAME_MAPPINGS["CLJapaneseToJSONGGUF"], "CL Japanese to JSON (GGUF)")
        self.assertEqual(cls.RETURN_TYPES, ("STRING",))
        self.assertEqual(cls.RETURN_NAMES, ("json_text",))
        self.assertEqual(cls.FUNCTION, "compile_json")
        self.assertFalse(cls.OUTPUT_NODE)

    def test_input_types_have_all_sixteen_inputs_in_order(self) -> None:
        with patch.object(
            nodes.CLJapaneseToJSONGGUF,
            "discover_model_names",
            return_value=["model.gguf"],
        ):
            required = nodes.CLJapaneseToJSONGGUF.INPUT_TYPES()["required"]
        self.assertEqual(
            list(required),
            [
                "plain_text",
                "model_name",
                "max_tokens",
                "temperature",
                "top_p",
                "repetition_penalty",
                "gpu_layers",
                "n_batch",
                "n_ctx",
                "flash_attn",
                "kv_cache_type",
                "op_offload",
                "keep_model_loaded",
                "seed",
                "keep_last_prompt",
                "steps",
            ],
        )
        self.assertEqual(required["model_name"][1]["default"], "model.gguf")
        self.assertEqual(required["op_offload"][0], "BOOLEAN")
        self.assertEqual(required["steps"][1]["default"], 8)

    def test_compile_returns_one_tuple_and_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ):
                result = node.compile_json(**arguments())
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 1)
            parsed = json.loads(result[0])
            self.assertEqual(parsed["shots"][0]["id"], "scene_1")
            self.assertEqual(parsed["defaults"]["steps"], 8)
            self.assertEqual(len(backend.calls), 1)
            self.assertIs(backend.llm, backend)

    def test_steps_input_is_written_to_plan_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ):
                result = node.compile_json(**arguments(steps=12))
            self.assertEqual(json.loads(result[0])["defaults"]["steps"], 12)

    def test_keep_last_with_history_bypasses_all_current_inputs(self) -> None:
        node = nodes.CLJapaneseToJSONGGUF()
        backend = FakeBackend()
        node._backend = backend
        node.last_json_text = '{"cached":true}\n'
        result = node.compile_json(
            **arguments(
                plain_text="",
                model_name="missing",
                max_tokens=-1,
                keep_last_prompt=True,
            )
        )
        self.assertEqual(result, ('{"cached":true}\n',))
        self.assertEqual(backend.ensure_calls, [])
        self.assertEqual(backend.calls, [])

    def test_keep_last_without_history_runs_normally_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ):
                result = node.compile_json(**arguments(keep_last_prompt=True))
            self.assertEqual(node.last_json_text, result[0])
            self.assertEqual(len(backend.calls), 1)

    def test_histories_are_per_instance(self) -> None:
        one = nodes.CLJapaneseToJSONGGUF()
        two = nodes.CLJapaneseToJSONGGUF()
        one.last_json_text = "one\n"
        self.assertIsNone(two.last_json_text)

    def test_keep_model_loaded_false_clears_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ):
                node.compile_json(**arguments(keep_model_loaded=False))
            self.assertIsNone(backend.llm)
            self.assertGreaterEqual(backend.clear_count, 1)

    def test_inference_failure_clears_model_and_preserves_old_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend(["bad", "bad"])
            node._backend = backend
            node.last_json_text = "old-valid-json\n"
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ), self.assertRaises(errors.TranslationError):
                node.compile_json(**arguments())
            self.assertIsNone(backend.llm)
            self.assertEqual(node.last_json_text, "old-valid-json\n")

    def test_invalid_generated_json_fails_and_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ), patch.object(nodes, "generate_json", return_value="not-json\n"), self.assertRaises(
                errors.JSONValidationError
            ):
                node.compile_json(**arguments())
            self.assertIsNone(node.last_json_text)
            self.assertIsNone(backend.llm)

    def test_generation_parameter_changes_do_not_change_load_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            node = nodes.CLJapaneseToJSONGGUF()
            backend = FakeBackend()
            node._backend = backend
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "load_system_prompt", return_value="system"
            ):
                node.compile_json(**arguments(max_tokens=64, temperature=0.1))
                node.compile_json(**arguments(max_tokens=96, temperature=0.2))
            first, second = backend.ensure_calls
            self.assertEqual(first, second)
            self.assertEqual(len(backend.calls), 2)

    def test_parameter_validation(self) -> None:
        node = nodes.CLJapaneseToJSONGGUF()
        bad_values = [
            {"plain_text": " "},
            {"max_tokens": 31},
            {"temperature": 0.0},
            {"op_offload": 0},
            {"kv_cache_type": "bad"},
            {"seed": 0},
            {"steps": 0},
            {"steps": True},
        ]
        for override in bad_values:
            with self.subTest(override=override), self.assertRaises(
                errors.CLJapaneseToJSONError
            ):
                node.compile_json(**arguments(**override))

    def test_is_changed_combines_external_fingerprints_without_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "model.gguf"
            model.write_bytes(b"x")
            with patch.object(nodes, "resolve_model_name", return_value=model), patch.object(
                nodes, "system_prompt_fingerprint", return_value=("prompt", 1)
            ):
                fingerprint = nodes.CLJapaneseToJSONGGUF.IS_CHANGED("model.gguf")
            self.assertEqual(fingerprint[-2:], ("prompt", 1))
            self.assertIn(model.stat().st_size, fingerprint)
