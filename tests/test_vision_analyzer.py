from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import torch

from .helpers import PKG, ROOT, module


cache_mod = module("node_vision_analyzer.cache")
discovery = module("node_vision_analyzer.discovery")
graph_binding = module("node_vision_analyzer.graph_binding")
image_io = module("node_vision_analyzer.image_io")
node_mod = module("node_vision_analyzer.node")
renderer = module("node_vision_analyzer.renderer")
vision_runtime = module("node_vision_analyzer.runtime")
validation = module("node_vision_analyzer.validation")


VALID_RESPONSE = "\n".join(
    [
        "OBSERVATION_V2",
        "OVERVIEW\t神社の参道に狐耳の人物が立っている",
        "PRIMARY_SUBJECT\t成人の狐巫女",
        "HINT_ASSESSMENT\tnot_used\t",
        "SUBJECT_FEATURE\tface\t少し吊り目の顔\tclear",
        "SUBJECT_FEATURE\thair\t長い淡い金髪\tclear",
        "SUBJECT_FEATURE\teyebrows\t短く太い丸い眉毛\tpartial",
        "SUBJECT_FEATURE\ttail\t白い先端の狐尻尾らしき形\tuncertain",
        "SUBJECT_POSE\t片手を前へ伸ばして立つ",
        "SCENE_SETTING\t深い森の神社参道",
        "SCENE_ELEMENT\t朱色の鳥居",
        "SCENE_ELEMENT\t石畳",
        "LIGHTING\t木漏れ日の柔らかな逆光",
        "TIME_WEATHER\t晴れた昼",
        "COMPOSITION\tshot_size\t全身ショット",
        "COMPOSITION\tviewpoint\t低い位置から見上げる",
        "COMPOSITION\tsubject_placement\t中央",
        "COMPOSITION\tdepth\t前景の鳥居と遠景の社殿に奥行きがある",
        "STYLE\tmedium\t2Dイラスト",
        "STYLE\trendering\tセル調の陰影",
        "STYLE\tpalette\t赤と緑を基調とする",
        "VISIBLE_TEXT\t神社の額に文字がある",
        "UNCERTAINTY\t尻尾の付け根は衣装で隠れている",
        "END_OBSERVATION",
    ]
)


class FakeFolderPaths:
    def __init__(self, input_dir: Path, models_dir: Path | None = None) -> None:
        self._input = input_dir
        self.models_dir = str(models_dir or input_dir / "models")
        self.folder_names_and_paths = {}

    def get_input_directory(self) -> str:
        return str(self._input)

    def get_annotated_filepath(self, name: str) -> str:
        return str(self._input / name)


class FakeVisionBackend:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.ensure_calls = []
        self.complete_calls = []
        self.clear_count = 0
        self.llm = None

    def ensure_loaded(self, model_path, projector_path, **kwargs):
        self.ensure_calls.append((model_path, projector_path, kwargs))
        self.llm = self
        return self

    def complete_chat(self, **kwargs):
        self.complete_calls.append(kwargs)
        image_parts = [
            part
            for part in kwargs["messages"][1]["content"]
            if part.get("type") == "image_url"
        ]
        assert len(image_parts) == 1
        uri = image_parts[0]["image_url"]["url"]
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]).startswith(b"\x89PNG")
        callback = kwargs.get("progress_callback")
        if callback:
            callback(1)
        content = self.responses.pop(0)
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    def clear_model(self):
        self.clear_count += 1
        self.llm = None


class VisionAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        cache_mod.ObservationCache.clear_memory()

    def test_registration_and_ui_contract(self):
        self.assertIs(PKG.NODE_CLASS_MAPPINGS["CLImageAnalyzerVisionGGUF"], node_mod.CLImageAnalyzerVisionGGUF)
        self.assertEqual(PKG.NODE_DISPLAY_NAME_MAPPINGS["CLImageAnalyzerVisionGGUF"], "CL Image Analyzer (Vision GGUF)")
        node = node_mod.CLImageAnalyzerVisionGGUF
        self.assertEqual(node.RETURN_TYPES, ("IMAGE", "MASK", "STRING", "STRING"))
        self.assertEqual(node.RETURN_NAMES, ("image", "mask", "result", "status"))
        with patch.object(node_mod, "discover_input_images", return_value=["test.png"]), patch.object(node_mod, "discover_vision_model_names", return_value=["vision.gguf"]):
            inputs = node.INPUT_TYPES()
        self.assertTrue(inputs["required"]["image"][1]["image_upload"])
        self.assertEqual(inputs["required"]["cache_mode"][1]["default"], "reuse")
        self.assertEqual(inputs["required"]["picture_reference_mode"][1]["default"], "auto_h3")
        self.assertFalse(inputs["required"]["keep_model_loaded"][1]["default"])
        self.assertEqual(inputs["optional"]["hint_mode"][1]["default"], "lock_identity")
        self.assertEqual(inputs["optional"]["hint_conflict"][1]["default"], "warn")
        self.assertTrue(inputs["optional"]["subject_hint"][1]["multiline"])
        self.assertEqual(inputs["optional"]["image_override"], ("IMAGE",))
        frontend = (ROOT / "web" / "cl_image_analyzer_vision.js").read_text(encoding="utf-8")
        self.assertIn("Picture参照:", frontend)
        self.assertIn("serialize = false", frontend)
        self.assertIn("migrateLegacyWidgets", frontend)

    def test_protocol_parser_and_python_renderers(self):
        observation, warnings = validation.parse_observation_response(VALID_RESPONSE)
        self.assertEqual(warnings, ())
        self.assertEqual(observation.primary_subject.features[0].category, "face")
        for profile in renderer.ANALYSIS_PROFILES:
            result, render_warnings = renderer.render_observation(
                observation, profile=profile, subject_index=2, picture_index=3
            )
            validation.validate_rendered_result(profile, result)
            self.assertIn("Uncertain feature excluded", render_warnings[0])
        subject, _ = renderer.render_observation(
            observation, profile="subject_only", subject_index=2, picture_index=3
        )
        self.assertEqual(subject.count("# サブジェクト"), 1)
        self.assertIn("<Picture 3>", subject)
        self.assertIn("<Subject 2>", subject)
        self.assertNotIn("白い先端の狐尻尾", subject)
        self.assertNotIn("朱色の鳥居", subject)

    def test_protocol_rejects_tags_fences_and_unknown_order(self):
        with self.assertRaises(Exception):
            validation.parse_observation_response(VALID_RESPONSE.replace("成人の狐巫女", "<Subject 1>"))
        with self.assertRaises(Exception):
            validation.parse_observation_response("```\n" + VALID_RESPONSE + "\n```")
        repaired, warnings = validation.parse_observation_response(
            VALID_RESPONSE.rsplit("\n", 1)[0]
        )
        self.assertEqual(repaired.overview, "神社の参道に狐耳の人物が立っている")
        self.assertEqual(len(warnings), 1)
        visible_response = VALID_RESPONSE.replace(
            "少し吊り目の顔\tclear",
            "少し吊り目の顔\tvisible",
        )
        normalized, warnings = validation.parse_observation_response(
            visible_response
        )
        self.assertEqual(
            normalized.primary_subject.features[0].visibility,
            "clear",
        )
        self.assertIn("Normalized SUBJECT_FEATURE visibility", warnings[0])
        repaired_response = VALID_RESPONSE.replace(
            "SUBJECT_FEATURE\tface\t少し吊り目の顔\tclear",
            "SUBJECT_FEATURE\tface\teyes\t少し吊り目の顔.clear",
        )
        repaired, warnings = validation.parse_observation_response(
            repaired_response
        )
        self.assertEqual(
            repaired.primary_subject.features[0].description,
            "少し吊り目の顔",
        )
        self.assertEqual(
            repaired.primary_subject.features[0].visibility,
            "clear",
        )
        self.assertIn("Repaired SUBJECT_FEATURE columns", warnings[0])
        with self.assertRaisesRegex(Exception, "generic image label"):
            validation.parse_observation_response(
                VALID_RESPONSE.replace("成人の狐巫女", "image")
            )
        with self.assertRaisesRegex(Exception, "instead of merely saying"):
            validation.parse_observation_response(
                VALID_RESPONSE.replace("少し吊り目の顔", "顔が見える")
            )
        swapped = VALID_RESPONSE.replace(
            "SUBJECT_FEATURE\tface\t少し吊り目の顔\tclear",
            "SUBJECT_FEATURE\tface\tclear\t少し吊り目の顔",
        )
        repaired, warnings = validation.parse_observation_response(swapped)
        self.assertEqual(
            repaired.primary_subject.features[0].description,
            "少し吊り目の顔",
        )
        self.assertEqual(repaired.primary_subject.features[0].visibility, "clear")
        self.assertIn("Repaired SUBJECT_FEATURE columns", warnings[0])

    def test_legacy_upload_sentinel_is_not_used_as_subject_hint(self):
        normalized, warning = node_mod.normalize_subject_hint_compat("image")
        self.assertEqual(normalized, "")
        self.assertIn("legacy IMAGEUPLOAD", warning)
        normalized, warning = node_mod.normalize_subject_hint_compat("成人の狐巫女")
        self.assertEqual(normalized, "成人の狐巫女")
        self.assertIsNone(warning)
        normalized, warning = node_mod.normalize_subject_hint_compat(
            "subject_hint: 成人の狐巫女"
        )
        self.assertEqual(normalized, "成人の狐巫女")
        self.assertIn("Removed pasted", warning)

    def test_runtime_suppresses_raw_mtmd_preprocessing_output(self):
        events = []

        class QuietContext:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, *_):
                events.append("exit")

        class FakeLoadedModel:
            verbose = False

            def reset(self):
                events.append("reset")

            def create_chat_completion(self, **kwargs):
                events.append(("call", kwargs))
                return {"choices": [{"message": {"content": "ok"}}]}

        backend = vision_runtime.VisionBackend(
            llama_module=None,
            llama_class=None,
            handler_class=None,
        )
        backend.llm = FakeLoadedModel()
        with patch.object(
            vision_runtime,
            "_suppress_native_output",
            side_effect=lambda *, disable: QuietContext(),
        ):
            response = backend.complete_chat(messages=[])
        self.assertEqual(response["choices"][0]["message"]["content"], "ok")
        self.assertEqual(events[1:4], ["enter", ("call", {"messages": []}), "exit"])

    def test_subject_hint_locks_identity_without_replacing_visual_features(self):
        hinted_response = VALID_RESPONSE.replace(
            "HINT_ASSESSMENT\tnot_used\t",
            "HINT_ASSESSMENT\tambiguous\t三角形の獣耳だけでは種別を断定できない",
        )
        observation, _ = validation.parse_observation_response(hinted_response)
        validation.validate_hint_assessment(
            observation,
            subject_hint="成人の狼娘",
            hint_mode="lock_identity",
        )
        result, warnings = renderer.render_observation(
            observation,
            profile="subject_only",
            subject_index=1,
            picture_index=1,
            subject_hint="成人の狼娘",
            hint_mode="lock_identity",
        )
        self.assertIn("使用する成人の狼娘", result)
        self.assertIn("ユーザー指定の成人の狼娘", result)
        self.assertIn("長い淡い金髪", result)
        self.assertNotIn("使用する成人の狐巫女", result)
        validation.validate_rendered_result("subject_only", result)
        self.assertTrue(warnings)

    def test_hint_assessment_requires_active_hint_and_safe_hint_text(self):
        hinted_response = VALID_RESPONSE.replace(
            "HINT_ASSESSMENT\tnot_used\t",
            "HINT_ASSESSMENT\tconsistent\t獣耳と尻尾が設定に整合する",
        )
        observation, _ = validation.parse_observation_response(hinted_response)
        with self.assertRaises(Exception):
            validation.validate_hint_assessment(
                observation,
                subject_hint="",
                hint_mode="observe_only",
            )
        with self.assertRaises(Exception):
            node_mod.normalize_subject_hint("# サブジェクト\n* 狼娘")
        with self.assertRaises(Exception):
            node_mod.normalize_subject_hint("<Subject 1> 狼娘")

    def test_graph_binding_resolves_h3_and_rejects_conflict(self):
        prompt = {
            "20": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"ref_images.ref_image_0": ["10", 0]}},
            "21": {"class_type": "PreviewImage", "inputs": {"images": ["10", 0]}},
        }
        binding = graph_binding.resolve_picture_binding(
            "auto_h3", picture_index=9, prompt=prompt, unique_id="10"
        )
        self.assertEqual(binding.picture_index, 1)
        prompt["22"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"ref_images": {"ref_image_2": ["10", 0]}}}
        with self.assertRaises(Exception):
            graph_binding.resolve_picture_binding(
                "auto_h3", picture_index=1, prompt=prompt, unique_id="10"
            )
        self.assertIsNone(graph_binding.resolve_picture_binding("none", picture_index=1, prompt=prompt, unique_id="10").picture_index)

    def test_image_decode_mask_and_analysis_resize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rgba = Image.new("RGBA", (20, 10), (10, 20, 30, 128))
            rgba.save(root / "alpha.png")
            loaded = image_io.load_image_asset("alpha.png", FakeFolderPaths(root))
            self.assertEqual(tuple(loaded.image.shape), (1, 10, 20, 3))
            self.assertEqual(tuple(loaded.mask.shape), (1, 10, 20))
            self.assertAlmostEqual(float(loaded.mask[0, 0, 0]), 1.0 - 128 / 255.0, places=5)
            analysis = image_io.prepare_analysis_image(loaded.pil_image, 256)
            self.assertEqual((analysis.width, analysis.height), (20, 10))
            large = Image.new("RGB", (1000, 500), "red")
            resized = image_io.prepare_analysis_image(large, 256)
            self.assertEqual((resized.width, resized.height), (256, 128))
            outside = root.parent / "outside.png"
            Image.new("RGB", (1, 1)).save(outside)
            with self.assertRaises(Exception):
                image_io.load_image_asset("../outside.png", FakeFolderPaths(root))

        override = torch.zeros((2, 6, 10, 3), dtype=torch.float32)
        override[0, :, :, 0] = 1.0
        loaded_override = image_io.load_image_tensor_asset(override)
        self.assertIs(loaded_override.image, override)
        self.assertEqual(tuple(loaded_override.mask.shape), (2, 6, 10))
        self.assertEqual((loaded_override.width, loaded_override.height), (10, 6))
        self.assertIn("only the first was analyzed", loaded_override.warnings[0])

        rgba = torch.zeros((1, 4, 5, 4), dtype=torch.float32)
        rgba[..., :3] = 0.5
        rgba[..., 3] = 0.25
        loaded_rgba = image_io.load_image_tensor_asset(rgba)
        self.assertEqual(tuple(loaded_rgba.image.shape), (1, 4, 5, 3))
        self.assertAlmostEqual(float(loaded_rgba.mask[0, 0, 0]), 0.75)
        with self.assertRaises(Exception):
            image_io.load_image_tensor_asset(torch.full((1, 2, 2, 3), 2.0))

    def test_discovery_pairs_model_and_f16_projector(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            target = models / "LLM" / "GGUF" / "Qwen3-VL-4B-Instruct"
            target.mkdir(parents=True)
            (target / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf").write_bytes(b"GGUFmodel")
            (target / "mmproj-Q8_0.gguf").write_bytes(b"GGUFprojector")
            (target / "mmproj-F16.gguf").write_bytes(b"GGUFprojector")
            (target / "text-only.gguf").write_bytes(b"GGUFmodel")
            fake = FakeFolderPaths(models / "input", models)
            found = discovery.discover_vision_model_map(fake)
            selected = found["Qwen3-VL-4B-Instruct/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"]
            self.assertEqual(selected.projector_path.name, "mmproj-F16.gguf")

    def test_node_retries_then_reuses_observation_cache_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "vision.gguf"
            projector = root / "mmproj.gguf"
            model.write_bytes(b"GGUFmodel")
            projector.write_bytes(b"GGUFprojector")
            pair = discovery.VisionModelPair(model, projector)
            pil = Image.new("RGB", (16, 8), "blue")
            loaded = image_io.LoadedImage(
                image=torch.zeros((1, 8, 16, 3)),
                mask=torch.zeros((1, 8, 16)),
                pil_image=pil,
                image_hash="a" * 64,
                image_mode="RGB",
                width=16,
                height=8,
                frame_count=1,
                warnings=(),
            )
            backend = FakeVisionBackend(["bad response", VALID_RESPONSE])
            instance = node_mod.CLImageAnalyzerVisionGGUF()
            instance._backend = backend
            instance._cache = cache_mod.ObservationCache(lambda: root / "cache")
            kwargs = dict(
                image="sample.png", model_name="vision", analysis_profile="planner_brief",
                additional_instruction="眉毛を確認", cache_mode="reuse",
                picture_reference_mode="manual", picture_index=2, subject_index=1,
                analysis_max_edge=1024, max_tokens=1024, temperature=0.1, top_p=0.9,
                repetition_penalty=1.05, gpu_layers=-1, n_batch=256, n_ctx=4096,
                flash_attn=True, kv_cache_type="q8_0", op_offload=True,
                keep_model_loaded=False, seed=1, retry_max=2,
                subject_hint="", hint_mode="lock_identity", hint_conflict="warn",
            )
            with patch.object(node_mod, "load_image_asset", return_value=loaded), patch.object(node_mod, "resolve_vision_model_name", return_value=pair):
                first = instance.analyze_image(**kwargs)
                second = instance.analyze_image(**kwargs)
                backend.responses.append(
                    VALID_RESPONSE.replace(
                        "HINT_ASSESSMENT\tnot_used\t",
                        "HINT_ASSESSMENT\tconsistent\t獣耳と尻尾が設定に整合する",
                    )
                )
                hinted_kwargs = {
                    **kwargs,
                    "subject_hint": "成人の狼娘",
                    "hint_mode": "lock_identity",
                }
                hinted = instance.analyze_image(**hinted_kwargs)
                backend.responses.append(
                    VALID_RESPONSE.replace(
                        "HINT_ASSESSMENT\tnot_used\t",
                        "HINT_ASSESSMENT\tconflict\t画像には明瞭な人間の耳だけが見える",
                    )
                )
                with self.assertRaises(node_mod.VisionAnalysisError):
                    instance.analyze_image(
                        **{
                            **hinted_kwargs,
                            "cache_mode": "disabled",
                            "hint_conflict": "strict",
                            "retry_max": 0,
                        }
                    )
            self.assertIn("retries: 1", first[3])
            self.assertIn("cache: hit (memory)", second[3])
            self.assertIn("<Picture 2>", second[2])
            self.assertIn("成人の狼娘", hinted[2])
            self.assertIn("subject_hint: lock_identity -> consistent", hinted[3])
            self.assertEqual(len(backend.complete_calls), 4)
            self.assertGreaterEqual(backend.clear_count, 2)

    def test_external_image_override_bypasses_internal_loader_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "vision.gguf"
            projector = root / "mmproj.gguf"
            model.write_bytes(b"GGUFmodel")
            projector.write_bytes(b"GGUFprojector")
            pair = discovery.VisionModelPair(model, projector)
            override = torch.zeros((1, 8, 16, 3), dtype=torch.float32)
            override[..., 2] = 1.0
            backend = FakeVisionBackend([VALID_RESPONSE])
            instance = node_mod.CLImageAnalyzerVisionGGUF()
            instance._backend = backend
            instance._cache = cache_mod.ObservationCache(lambda: root / "cache")
            kwargs = dict(
                image=image_io.NO_INPUT_IMAGES_PLACEHOLDER,
                image_override=override,
                model_name="vision",
                analysis_profile="general",
                additional_instruction="",
                cache_mode="reuse",
                picture_reference_mode="none",
                picture_index=1,
                subject_index=1,
                analysis_max_edge=1024,
                max_tokens=1024,
                temperature=0.1,
                top_p=0.9,
                repetition_penalty=1.05,
                gpu_layers=-1,
                n_batch=256,
                n_ctx=4096,
                flash_attn=True,
                kv_cache_type="q8_0",
                op_offload=True,
                keep_model_loaded=False,
                seed=1,
                retry_max=2,
            )
            with patch.object(
                node_mod,
                "load_image_asset",
                side_effect=AssertionError("internal loader must not run"),
            ), patch.object(
                node_mod,
                "resolve_vision_model_name",
                return_value=pair,
            ):
                first = instance.analyze_image(**kwargs)
                second = instance.analyze_image(**kwargs)
            self.assertIs(first[0], override)
            self.assertEqual(tuple(first[1].shape), (1, 8, 16))
            self.assertIn("image_source: connected IMAGE override", first[3])
            self.assertIn("cache: hit (memory)", second[3])
            self.assertEqual(len(backend.complete_calls), 1)
            self.assertTrue(
                node_mod.CLImageAnalyzerVisionGGUF.VALIDATE_INPUTS(
                    image_io.NO_INPUT_IMAGES_PLACEHOLDER,
                    image_override=override,
                )
            )
            changed = node_mod.CLImageAnalyzerVisionGGUF.IS_CHANGED(
                image_io.NO_INPUT_IMAGES_PLACEHOLDER,
                "vision",
                image_override=override,
            )
            self.assertTrue(changed != changed)


if __name__ == "__main__":
    unittest.main()
