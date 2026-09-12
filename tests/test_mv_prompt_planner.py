from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from .helpers import FakeLLM, PKG, ROOT, module


brief_parser = module("node_mv_prompt_planner.brief_parser")
errors = module("node_mv_prompt_planner.errors")
debug_output = module("node_mv_prompt_planner.debug_output")
planning = module("node_mv_prompt_planner.planning")
planner_node = module("node_mv_prompt_planner.node")
prompt_loader = module("node_mv_prompt_planner.prompt_loader")
visual_profiles = module("node_mv_prompt_planner.visual_profiles")
renderer = module("node_mv_prompt_planner.renderer")
structures = module("node_mv_prompt_planner.structures")
timeline_parser = module("node_mv_prompt_planner.timeline_parser")
validation = module("node_mv_prompt_planner.validation")
scene_limiter = module("node_scene_limiter.node")
llmj2e = module("node_japanese_to_json.compiler.llmj2e")
mdparse = module("node_japanese_to_json.compiler.mdparse")
jsongen = module("node_japanese_to_json.compiler.jsongen")


BRIEF = """# サブジェクト
* <Subject 1>として描く中性的な人物。

# 保持分析
* <Subject 1> 完全に保持: 顔、頭部、体格及び衣装を維持する。

# 共通プロンプト
* 荒い線による立体的なラフスケッチとして描く。
"""

TIMELINE = """# サブジェクト
* 人物。

# 共通プロンプト
* 外観を維持する。

// シーン 1
# シーン 4秒
// 検出状態: silent。ソース範囲 00:00.000-00:04.000。
## ショット
* <Subject 1>は口を閉じて自然に演技する。
## 音響
* 発声: なし
* ソース音声: 完全維持

// シーン 2
# シーン 5秒 継続
// 検出状態: voiced。ソース範囲 00:04.000-00:09.000。
// 楽曲セクション: [Chorus]
// 歌詞: 闇を越えて進め
## ショット
* <Subject 1>は自然に演技する。
* リップシンク: <Subject 1> <- ソースボーカル
## 音響
* 発声: ソースボーカルのみ
* ソース音声: 完全維持
"""


def song_bible() -> str:
    return "\n".join(
        (
            "SONG_BIBLE",
            "VISUAL_ARC\t暗闇から光へ進む。",
            "VISUAL_ENRICHMENT_STRATEGY\t選択プロファイルに従う。",
            "CAMERA_STRATEGY\t静止からアーク移動へ展開する。",
            "SECTION_MOTIF\t[Chorus]\t強い白光。",
            "END_SONG_BIBLE",
        )
    )


def lyric_blueprint(scene_id: int = 2) -> str:
    return "\n".join(
        (
            f"LYRIC_SCENE\t{scene_id}",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
            "COMPOSITION_REQUIREMENT\tCLMPSUB1Xの前に奥行きのある硬い対象面を配置する。",
            "ACTION\t1\tCLMPSUB1Xは対象面へ踏み込みながら片腕を引く。",
            "ACTION\t2\tCLMPSUB1Xは指先を対象面へ接触させて軌跡を刻む。",
            "VISIBLE_RESULT\t対象面に不均一な軌跡が残り、CLMPSUB1Xは指先を離す。",
            "END_LYRIC_SCENE",
        )
    )


def parse_lyric_blueprint(
    payload: str,
) -> tuple[dict[int, object], dict[int, str]]:
    timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
    protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
    protector.protect(BRIEF)
    return validation.parse_lyric_action_response(
        payload,
        {2: timeline.scenes[1]},
        protector,
    )


def raw_subject_lyric_blueprint(scene_id: int = 2) -> str:
    return lyric_blueprint(scene_id).replace("CLMPSUB1X", "<Subject 1>")


def scene_payload(
    scene_id: int,
    *,
    valid: bool = True,
    actions: tuple[str, ...] | None = None,
    auxiliary_visuals: tuple[tuple[str, str], ...] = (),
) -> str:
    if scene_id == 1:
        camera = ("static", "none", "none", "斜め後方の低い位置から輪郭を捉える。")
        motion = "CLMPSUB1Xは口を閉じたまま肩を引き、ゆっくり振り返る。"
    else:
        camera = (
            "arc" if valid else "orbit-ish",
            "large",
            "fast",
            "CLMPSUB1Xの左側から背後を通って右前方へ大きく回り込む。",
        )
        motion = "CLMPSUB1Xは胸を開き、片腕を空へ伸ばして踏み出す。"
    action_values = actions or (motion,)
    lines = [
        f"SCENE\t{scene_id}",
        f"SCENE_INTENT\tScene {scene_id}の映像意図。",
        (
            "LYRIC_RESPONSE\t0\tinstrumental_continuity"
            if scene_id == 1
            else "LYRIC_RESPONSE\t1\tdirect_subject_action"
        ),
        "SHOT\t0",
        "COMPOSITION\t人物と背景の奥行きを斜め構図で示す。",
    ]
    lines.extend(
        f"ACTION\t{index}\t{value}"
        for index, value in enumerate(action_values, start=1)
    )
    lines.extend(
        f"AUX_VISUAL\t{kind}\t{description}"
        for kind, description in auxiliary_visuals
    )
    lines.extend(
        (
            "ENVIRONMENT\t黒いインクが透明な水へ広がり、側光が輪郭を刻む。",
            "CAMERA\t" + "\t".join(camera),
            "END_SHOT",
            "END_SCENE",
        )
    )
    return "\n".join(lines)


def scheduled_scene_payload(scene_id: int, **kwargs) -> str:
    """Return the deterministic one-Shot camera required by visual profiles."""

    payload = scene_payload(scene_id, **kwargs)
    if scene_id == 1:
        return payload.replace(
            "CAMERA\tstatic\tnone\tnone\t斜め後方の低い位置から輪郭を捉える。",
            "CAMERA\tarc\tlarge\tmoderate\t人物の左前方斜めから側面を通って右後方斜めまで大きく回り込み、前景と遠い背景の視差を変える。",
        )
    if scene_id == 2:
        return payload.replace(
            "CAMERA\tarc\tlarge\tfast\tCLMPSUB1Xの左側から背後を通って右前方へ大きく回り込む。",
            "CAMERA\tpush\tlarge\tfast\t前景の対象越しの斜め遠景から人物へ素早く接近し、遠い背景との視差を広げて異なる斜め近景で終える。",
        )
    raise AssertionError("scheduled_scene_payload fixture supports Scene 1-2")


class FakePlannerBackend:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def complete_chat(self, **kwargs):
        self.calls.append(kwargs)
        content = self.payloads.pop(0)
        if isinstance(content, BaseException):
            raise content
        callback = kwargs.get("progress_callback")
        if callback is not None:
            callback(1)
        return {
            "choices": [
                {"message": {"content": content}, "finish_reason": "stop"}
            ]
        }


class FakePlannerNodeBackend(FakePlannerBackend):
    def __init__(self, payloads: list[str]) -> None:
        super().__init__(payloads)
        self.ensure_calls: list[dict] = []
        self.clear_count = 0

    def ensure_loaded(self, model_path: Path, **kwargs):
        self.ensure_calls.append({"model_path": model_path, **kwargs})
        return self

    def clear_model(self) -> None:
        self.clear_count += 1


def node_arguments(**overrides):
    values = {
        "prompt_segments": TIMELINE,
        "planning_markdown": BRIEF,
        "model_name": "planner.gguf",
        "chat_format": "auto",
        "max_tokens": 2048,
        "temperature": 0.3,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
        "gpu_layers": -1,
        "n_batch": 256,
        "n_ctx": 16384,
        "flash_attn": True,
        "kv_cache_type": "q8_0",
        "op_offload": True,
        "keep_model_loaded": True,
        "seed": 1,
        "scenes_per_batch": 2,
        "retry_max": 2,
    }
    values.update(overrides)
    return values


class MVPromptPlannerTests(unittest.TestCase):
    def test_llm_call_logs_time_driven_heartbeat_without_new_chunks(self) -> None:
        class SlowBackend:
            @staticmethod
            def complete_chat(**kwargs):
                del kwargs
                time.sleep(0.03)
                return {
                    "choices": [
                        {
                            "message": {"content": "VALID"},
                            "finish_reason": "stop",
                        }
                    ]
                }

        with patch.object(planning, "INFERENCE_HEARTBEAT_SECONDS", 0.005):
            with self.assertLogs("cl_mv_prompt_planner", level="INFO") as captured:
                content, _ = planning._call(
                    SlowBackend(),
                    system="system",
                    payload={"request": 1},
                    max_tokens=32,
                    temperature=0.1,
                    top_p=0.9,
                    repetition_penalty=1.05,
                    seed=1,
                    label="heartbeat test",
                    progress_callback=None,
                    interrupt_callback=None,
                    debug_events=None,
                )

        self.assertEqual(content, "VALID")
        output = "\n".join(captured.output)
        self.assertIn("LLM inference active: heartbeat test", output)
        self.assertIn("last_chunk_age=n/a", output)
        self.assertIn("LLM inference completed: heartbeat test", output)

    def test_llm_call_aborts_after_stream_stalls(self) -> None:
        class PollingBackend:
            @staticmethod
            def complete_chat(**kwargs):
                kwargs["progress_callback"](3)
                kwargs["interrupt_callback"]()
                raise AssertionError("stall callback did not abort inference")

        with patch.object(planning, "INFERENCE_STALL_SECONDS", -1.0):
            with self.assertRaisesRegex(
                errors.PlannerInferenceStallError,
                "no new streamed chunk",
            ):
                planning._call(
                    PollingBackend(),
                    system="system",
                    payload={"request": 1},
                    max_tokens=32,
                    temperature=0.1,
                    top_p=0.9,
                    repetition_penalty=1.05,
                    seed=1,
                    label="stall test",
                    progress_callback=None,
                    interrupt_callback=None,
                    debug_events=None,
                )

    def test_stalled_scene_batch_retries_one_scene_at_a_time(self) -> None:
        backend = FakePlannerBackend(
            [
                song_bible(),
                errors.PlannerInferenceStallError("test batch stalled"),
                scene_payload(1),
                scene_payload(2),
            ]
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            plan = planning.generate_mv_plan(
                brief_parser.parse_planning_brief(BRIEF),
                timeline_parser.parse_prompt_timeline(TIMELINE),
                backend,
                scenes_per_batch=2,
                retry_max=1,
            )

        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        request_ids = [
            json.loads(call["messages"][1]["content"])["requested_scene_ids"]
            for call in backend.calls[1:]
        ]
        self.assertEqual(request_ids, [[1, 2], [1], [2]])
        self.assertIn("retry one Scene at a time", "\n".join(captured.output))

    def test_song_bible_target_spec_records_approved_design_boundaries(self) -> None:
        text = (ROOT / "docs" / "cl_mv_prompt_planner_song_bible_spec.md").read_text(
            encoding="utf-8"
        )
        for marker in (
            "clmv-song-bible-line-v4",
            '"planning_brief"',
            '"global_visual_direction"',
            '"lyric_lines"',
            "LYRIC_RESPONSE",
            '"active_section_motifs"',
            '"previous_scene_tail"',
            "重複Scene検出は必須",
            '`camera_guard`',
            '`vocal_guard`',
            "一般自然言語の意味矛盾",
            "未定義かつ本仕様の対象外",
        ):
            self.assertIn(marker, text)

    def test_registration_and_input_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLMVPromptPlannerGGUF"]
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLMVPromptPlannerGGUF"],
            "CL MV Prompt Planner (GGUF)",
        )
        self.assertEqual(
            cls.RETURN_NAMES, ("planned_markdown", "planner_json", "status")
        )
        with patch.object(cls, "discover_model_names", return_value=["planner.gguf"]):
            input_types = cls.INPUT_TYPES()
            required = input_types["required"]
        compiler_cls = PKG.NODE_CLASS_MAPPINGS["CLJapaneseToJSONGGUF"]
        with patch.object(
            compiler_cls,
            "discover_model_names",
            return_value=["planner.gguf"],
        ):
            compiler_required = compiler_cls.INPUT_TYPES()["required"]
        self.assertTrue(required["prompt_segments"][1]["forceInput"])
        self.assertTrue(required["planning_markdown"][1]["forceInput"])
        self.assertNotIn("default", required["planning_markdown"][1])
        self.assertEqual(required["chat_format"][1]["default"], "auto")
        self.assertEqual(required["scenes_per_batch"][1]["default"], 6)
        self.assertEqual(
            input_types["optional"]["camera_guard"][1]["default"], "warn"
        )
        self.assertEqual(
            input_types["optional"]["vocal_guard"][1]["default"], "warn"
        )
        self.assertEqual(
            input_types["optional"]["visual_enrichment_profile"][1][
                "default"
            ],
            "performance_only",
        )
        self.assertTrue(
            input_types["optional"]["model_name_override"][1]["forceInput"]
        )
        self.assertTrue(
            input_types["optional"]["visual_enrichment_profile_override"][1][
                "forceInput"
            ]
        )
        self.assertFalse(
            input_types["optional"]["save_debug_output"][1]["default"]
        )
        for name in (
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
            "retry_max",
        ):
            self.assertEqual(
                required[name][1]["default"],
                compiler_required[name][1]["default"],
                name,
            )
        fingerprint = cls.IS_CHANGED("missing-planner.gguf")
        self.assertIn("model-resolution-error", fingerprint)
        self.assertIn("song_bible_system_prompt.txt", fingerprint)
        self.assertIn("scene_plan_system_prompt.txt", fingerprint)
        self.assertIn("lyric_action_system_prompt.txt", fingerprint)
        self.assertIn("auxiliary_visual_repair_system_prompt.txt", fingerprint)
        self.assertIn("performance_only/profile.json", fingerprint)

    def test_string_overrides_trim_non_empty_values_and_fall_back_on_empty(self) -> None:
        self.assertEqual(
            planner_node._select_string_override(
                "widget.gguf",
                "  external.gguf  ",
                name="model_name",
            ),
            ("external.gguf", True),
        )
        self.assertEqual(
            planner_node._select_string_override(
                "widget.gguf",
                "  ",
                name="model_name",
            ),
            ("widget.gguf", False),
        )

        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "external.gguf"
            model_path.write_bytes(b"gguf")
            model_size = model_path.stat().st_size
            with patch.object(
                planner_node,
                "resolve_model_name",
                return_value=model_path,
            ) as resolve:
                fingerprint = planner_node.CLMVPromptPlannerGGUF.IS_CHANGED(
                    "widget.gguf",
                    model_name_override=" external.gguf ",
                )
        resolve.assert_called_once_with(
            "external.gguf",
            log_name="cl_mv_prompt_planner",
        )
        self.assertIn(model_size, fingerprint)

    def test_8b_model_warning_is_exactly_three_conspicuous_lines(self) -> None:
        with patch.object(planner_node.LOGGER, "warning") as warning:
            self.assertTrue(
                planner_node._warn_for_8b_model(
                    "qwen3-8b-abliterated-Q4_K_M.gguf",
                    "lyric_visuals_full",
                )
            )

        self.assertEqual(warning.call_count, 3)
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertEqual(
            messages[0], planner_node._CRITICAL_WARNING_BORDER
        )
        self.assertEqual(
            messages[2], planner_node._CRITICAL_WARNING_BORDER
        )
        self.assertIn("CRITICAL / FATAL-RISK", messages[1])
        self.assertIn("8B model detected", messages[1])
        self.assertIn("lyric_visuals_full", messages[1])
        self.assertIn("lyric_visuals_light_8b", messages[1])
        self.assertIn("14B+", messages[1])

    def test_8b_model_warning_is_silent_for_supported_profiles(self) -> None:
        with patch.object(planner_node.LOGGER, "warning") as warning:
            for profile_id in (
                "lyric_visuals_light_8b",
                "performance_only",
            ):
                self.assertFalse(
                    planner_node._warn_for_8b_model(
                        "qwen3-8b-abliterated-Q4_K_M.gguf",
                        profile_id,
                    ),
                    profile_id,
                )
        warning.assert_not_called()

    def test_8b_model_warning_does_not_misdetect_other_sizes(self) -> None:
        with patch.object(planner_node.LOGGER, "warning") as warning:
            for model_name in (
                "Qwen3.8-27B-heretic-ara.Q5_K_M.gguf",
                "qwen3-18b.gguf",
                "qwen3-80B.gguf",
                "gemma-3-12b.gguf",
            ):
                self.assertFalse(
                    planner_node._warn_for_8b_model(
                        model_name,
                        "lyric_visuals_full",
                    ),
                    model_name,
                )
        warning.assert_not_called()

    def test_system_prompts_lock_line_protocol_timeline_and_h3_camera_contract(self) -> None:
        bible_prompt = prompt_loader.load_planner_prompt(
            "song_bible_system_prompt.txt"
        )
        scene_prompt = prompt_loader.load_planner_prompt(
            "scene_plan_system_prompt.txt"
        )
        self.assertIn("Return only the tab-separated line protocol", bible_prompt)
        self.assertNotIn("Return exactly one JSON object", bible_prompt)
        self.assertIn("CONTINUITY_RULE is removed and forbidden", bible_prompt)
        self.assertIn("recent_scene_patterns", scene_prompt)
        self.assertIn("auxiliary_visual_repair", scene_prompt)
        self.assertIn("active_section_motifs", scene_prompt)
        self.assertIn("previous_scene_tail", scene_prompt)
        self.assertIn("Use previous_scene_tail only as internal state data", scene_prompt)
        self.assertIn("without requiring H3 or the downstream compiler", scene_prompt)
        self.assertIn("Never add, delete, merge, split, reorder, or renumber Scenes", scene_prompt)
        for camera_type in validation.CAMERA_TYPES:
            self.assertIn(camera_type, scene_prompt)
        self.assertIn("static must use amplitude none", scene_prompt)
        self.assertIn("speed none", scene_prompt)
        self.assertIn("requested_scene_ids", scene_prompt)
        self.assertIn("Default to exactly one Shot per Scene", scene_prompt)
        self.assertIn("ACTION order is a binding time sequence", scene_prompt)
        self.assertIn("ACTION\t1\t", scene_prompt)
        self.assertIn("LYRIC_RESPONSE\t", scene_prompt)
        self.assertIn("retention_execution_contract", scene_prompt)
        self.assertIn("retained Subject body part", scene_prompt)
        self.assertIn("camera_choreography_contract", scene_prompt)
        self.assertIn("distinct starting view", scene_prompt)
        self.assertIn("foreground/background depth anchors", scene_prompt)
        self.assertIn("required_camera_sequence", scene_prompt)
        self.assertNotIn(
            "CAMERA\tpush\tlarge\tmoderate\t前景要素越し",
            scene_prompt,
        )
        self.assertNotIn(
            "CAMERA\ttracking\tmedium\tmoderate\t"
            "動く主要被写体を斜め側方から一定距離で追従する。",
            scene_prompt,
        )
        self.assertIn("a hand, face, gaze, mouth, or local gesture", scene_prompt)
        self.assertNotIn("ROLE LOCK", scene_prompt)
        self.assertNotIn("high-capacity creative policy", scene_prompt)
        self.assertNotIn("character-performance creative policy", scene_prompt)
        self.assertNotIn("one to three AUX_VISUAL", scene_prompt)
        self.assertNotIn("Output no AUX_VISUAL", scene_prompt)
        self.assertNotIn("We carve our names", scene_prompt)
        self.assertNotIn("ink motion", scene_prompt)
        with self.assertRaisesRegex(errors.MVPlannerError, "Invalid"):
            prompt_loader.load_planner_prompt("../outside.txt")
        auxiliary_repair_prompt = prompt_loader.load_planner_prompt(
            "auxiliary_visual_repair_system_prompt.txt"
        )
        self.assertIn("exactly one physical tab-separated line", auxiliary_repair_prompt)
        self.assertIn("forbidden_exact_description", auxiliary_repair_prompt)

    def test_visual_profiles_are_discovered_and_composed_from_directories(self) -> None:
        self.assertEqual(
            visual_profiles.discover_visual_profile_ids(),
            [
                "performance_only",
                "lyric_visuals_light_8b",
                "lyric_visuals_full",
            ],
        )
        light = visual_profiles.load_visual_profile(
            "lyric_visuals_light_8b"
        )
        self.assertEqual(light.minimum_aux_visuals_per_scene, 1)
        self.assertEqual(light.maximum_aux_visuals_per_scene, 1)
        self.assertEqual(light.long_lyric_scene_minimum_shots, 2)
        self.assertEqual(light.long_lyric_scene_maximum_shots, 2)
        self.assertEqual(
            light.scene_contract(1)["required_kind"], "symbolic_object"
        )
        composed = prompt_loader.load_profiled_planner_prompt(
            "scene_plan_system_prompt.txt", light
        )
        self.assertIn(
            "VISUAL ENRICHMENT PROFILE: lyric_visuals_light_8b", composed
        )
        self.assertIn("exactly one AUX_VISUAL", composed)
        self.assertIn("compact creative policy for an 8B", composed)
        self.assertIn("1. LYRIC FIRST", composed)
        self.assertIn("2. OBSERVABLE MECHANICS", composed)
        self.assertIn("ROLE LOCK", composed)
        self.assertIn("never a target-like shape", composed)
        self.assertIn("usable face, thickness, edges", composed)
        self.assertIn("detached glyph-shaped content", composed)
        self.assertIn("deliberate mark-making", composed)
        self.assertIn("Never replace mark-making with", composed)
        self.assertIn("4. MOVING CAMERA", composed)
        self.assertIn("start view, travel path", composed)
        self.assertIn("Do not use tracking unless", composed)
        self.assertIn("5. AUXILIARY LAST", composed)
        self.assertIn("earliest complete depictable predicate", composed)
        self.assertIn("A different operation on the same target is invalid", composed)
        self.assertIn("exact CONTACT POINT", composed)
        self.assertIn("locked_lyric_action_blueprint", composed)
        self.assertTrue(light.lyric_action_preplan)
        self.assertEqual(light.lyric_action_scenes_per_request, 1)
        self.assertIn("exactly two Shots", composed)
        self.assertIn("retained Subject body part", composed)
        self.assertNotIn("high-capacity creative policy", composed)
        self.assertNotIn("character-performance creative policy", composed)
        light_song = prompt_loader.load_profiled_planner_prompt(
            "song_bible_system_prompt.txt", light
        )
        self.assertIn("Keep the Song Bible external", light_song)
        self.assertIn("must never prescribe a Subject action", light_song)
        self.assertIn("Do not name a CLMP Subject token", light_song)
        full = visual_profiles.load_visual_profile("lyric_visuals_full")
        self.assertEqual(full.lyric_action_scenes_per_request, 0)
        self.assertEqual(full.long_lyric_scene_minimum_shots, 2)
        self.assertEqual(full.long_lyric_scene_maximum_shots, 3)
        full_composed = prompt_loader.load_profiled_planner_prompt(
            "scene_plan_system_prompt.txt", full
        )
        self.assertIn("high-capacity creative policy", full_composed)
        self.assertIn("useful micro-choreography", full_composed)
        self.assertIn("controlled and varied", full_composed)
        self.assertIn("two or three motivated Shots", full_composed)
        self.assertIn("retained Subject property", full_composed)
        self.assertNotIn("ROLE LOCK", full_composed)
        self.assertNotIn("compact creative policy for an 8B", full_composed)
        self.assertNotIn("character-performance creative policy", full_composed)
        full_song = prompt_loader.load_profiled_planner_prompt(
            "song_bible_system_prompt.txt", full
        )
        self.assertIn("high-capacity creative policy", full_song)
        self.assertNotIn("Keep the Song Bible external", full_song)

        performance = visual_profiles.load_visual_profile("performance_only")
        self.assertEqual(performance.lyric_action_scenes_per_request, 0)
        performance_scene = prompt_loader.load_profiled_planner_prompt(
            "scene_plan_system_prompt.txt", performance
        )
        self.assertIn("character-performance creative policy", performance_scene)
        self.assertIn("Output no AUX_VISUAL", performance_scene)
        self.assertNotIn("ROLE LOCK", performance_scene)
        self.assertNotIn("high-capacity creative policy", performance_scene)
        performance_song = prompt_loader.load_profiled_planner_prompt(
            "song_bible_system_prompt.txt", performance
        )
        self.assertIn("character-performance creative policy", performance_song)
        self.assertIn("no auxiliary lyric-derived visual animation", performance_song)
        self.assertNotIn("Keep the Song Bible external", performance_song)
        with self.assertRaisesRegex(errors.MVPlannerError, "Unknown"):
            visual_profiles.load_visual_profile("missing_profile")

    def test_lyric_action_blueprint_protocol_is_strict_and_recoverable(self) -> None:
        recovered, response_errors = parse_lyric_blueprint(lyric_blueprint())
        self.assertEqual(response_errors, {})
        self.assertEqual(recovered[2].lyric_anchor_index, 1)
        self.assertEqual(len(recovered[2].subject_actions), 2)

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            raw_recovered, raw_errors = parse_lyric_blueprint(
                raw_subject_lyric_blueprint()
            )
        self.assertEqual(raw_errors, {})
        self.assertIn("<Subject 1>", raw_recovered[2].subject_actions[0])
        self.assertIn("normalized known raw Subject", "\n".join(captured.output))

        unknown_raw = raw_subject_lyric_blueprint().replace(
            "<Subject 1>", "<Subject 9>"
        )
        unknown_recovered, unknown_errors = parse_lyric_blueprint(unknown_raw)
        self.assertEqual(unknown_recovered, {})
        self.assertIn("unprotected reference tag '<Subject 9>'", unknown_errors[2])

        missing_subject = lyric_blueprint().replace(
            "CLMPSUB1Xは対象面へ踏み込みながら片腕を引く。",
            "人物のいない対象面を前景に置く。",
        ).replace(
            "CLMPSUB1Xは指先を対象面へ接触させて軌跡を刻む。",
            "道具が対象面へ接触して軌跡を刻む。",
        )
        rejected, reject_errors = parse_lyric_blueprint(missing_subject)
        self.assertEqual(rejected, {})
        self.assertIn("requires an ACTION naming <Subject N>", reject_errors[2])

        vocalized = lyric_blueprint().replace(
            "CLMPSUB1Xは対象面へ踏み込みながら片腕を引く。",
            "CLMPSUB1Xは歌いながら対象面へ片腕を伸ばす。",
        )
        rejected, reject_errors = parse_lyric_blueprint(vocalized)
        self.assertEqual(rejected, {})
        self.assertIn("forbidden vocal cue", reject_errors[2])

    def test_light_8b_preplans_one_scene_without_global_visual_direction(self) -> None:
        base = timeline_parser.parse_prompt_timeline(TIMELINE)
        second_lyric_scene = structures.TimelineScene(
            scene_id=3,
            duration_seconds=5,
            is_continue=True,
            state="voiced",
            source_start_ms=9000,
            source_end_ms=14000,
            lyrics=(
                structures.TimelineLyric(
                    text="石の表面を彫る",
                    section_label="[Chorus]",
                    section_kind="custom",
                ),
            ),
            lip_sync_lines=(
                "リップシンク: <Subject 1> <- ソースボーカル",
            ),
            soundscape_lines=(
                "発声: ソースボーカルのみ",
                "ソース音声: 完全維持",
            ),
        )
        timeline = structures.TimelineDocument(
            scenes=(*base.scenes, second_lyric_scene)
        )
        brief = brief_parser.parse_planning_brief(BRIEF)
        protector, planning_brief, _ = planning._build_protector(
            brief, timeline
        )
        backend = FakePlannerBackend(
            [lyric_blueprint(2), lyric_blueprint(3)]
        )

        recovered, _, retries = planning._plan_lyric_action_blueprints(
            backend,
            scenes=[base.scenes[1], second_lyric_scene],
            planning_brief=planning_brief,
            protector=protector,
            call_settings={
                "max_tokens": 4096,
                "temperature": 0.1,
                "top_p": 0.9,
                "repetition_penalty": 1.05,
                "seed": 1,
            },
            retry_max=1,
            request_index=0,
            batch_number=1,
            progress_callback=None,
            interrupt_callback=None,
            debug_events=None,
            debug_state=None,
            scenes_per_request=1,
        )

        self.assertEqual(sorted(recovered), [2, 3])
        self.assertEqual(retries, 0)
        self.assertEqual(len(backend.calls), 2)
        requests = [
            json.loads(call["messages"][1]["content"])
            for call in backend.calls
        ]
        self.assertEqual(
            [request["requested_scene_ids"] for request in requests],
            [[2], [3]],
        )
        for request in requests:
            self.assertNotIn(
                "global_visual_direction",
                request["planning_constraints"],
            )
            self.assertIn("style_separation", request["scenes"][0])

    def test_visual_profiles_validate_auxiliary_visual_contracts(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module(
            "node_mv_prompt_planner.placeholders"
        ).ReferenceProtector()
        protector.protect(BRIEF)
        performance = visual_profiles.load_visual_profile(
            "performance_only"
        )
        rejected, performance_errors = validation.parse_scene_response(
            scene_payload(
                1,
                auxiliary_visuals=(
                    ("symbolic_object", "黒い環が形成される。"),
                ),
            ),
            {1: timeline.scenes[0]},
            protector,
            visual_profile=performance,
        )
        self.assertEqual(rejected, {})
        self.assertIn("forbids AUX_VISUAL", performance_errors[1])

        light = visual_profiles.load_visual_profile(
            "lyric_visuals_light_8b"
        )
        light_payload = scheduled_scene_payload(
            1,
            auxiliary_visuals=(
                (
                    "symbolic_object",
                    "黒い石片が集まり、途切れた環を形成して砕ける。",
                ),
            ),
        )
        recovered, scene_errors = validation.parse_scene_response(
            light_payload,
            {1: timeline.scenes[0]},
            protector,
            visual_profile=light,
        )
        self.assertEqual(scene_errors, {})
        self.assertEqual(
            recovered[1].shots[0].auxiliary_visuals[0].kind,
            "symbolic_object",
        )

        missing, missing_errors = validation.parse_scene_response(
            scheduled_scene_payload(1),
            {1: timeline.scenes[0]},
            protector,
            visual_profile=light,
        )
        self.assertEqual(missing, {})
        self.assertIn("requires 1-1 AUX_VISUAL", missing_errors[1])

        wrong, wrong_errors = validation.parse_scene_response(
            scheduled_scene_payload(
                1,
                auxiliary_visuals=(("light_shadow", "白い光が脈動する。"),),
            ),
            {1: timeline.scenes[0]},
            protector,
            visual_profile=light,
        )
        self.assertEqual(wrong, {})
        self.assertIn("requires AUX_VISUAL kind", wrong_errors[1])

        full = visual_profiles.load_visual_profile("lyric_visuals_full")
        self.assertIn("material_transformation", full.allowed_kinds)
        self.assertIn("spatial_trajectory", full.allowed_kinds)
        self.assertNotIn("ink_transformation", full.allowed_kinds)
        full_payload = scheduled_scene_payload(
            1,
            auxiliary_visuals=(
                ("spatial_metaphor", "暗い道が奥へ伸びる。"),
                ("impact_effect", "橙色の波紋が道を砕く。"),
            ),
        )
        recovered, scene_errors = validation.parse_scene_response(
            full_payload,
            {1: timeline.scenes[0]},
            protector,
            visual_profile=full,
        )
        self.assertEqual(scene_errors, {})
        self.assertEqual(len(recovered[1].shots[0].auxiliary_visuals), 2)

        excessive, excessive_errors = validation.parse_scene_response(
            scheduled_scene_payload(
                1,
                auxiliary_visuals=(
                    ("symbolic_object", "黒い環が形成される。"),
                    ("spatial_metaphor", "白い道が奥へ伸びる。"),
                    ("impact_effect", "橙色の波紋が道を砕く。"),
                    ("light_shadow", "影が脈動する。"),
                ),
            ),
            {1: timeline.scenes[0]},
            protector,
            visual_profile=full,
        )
        self.assertEqual(excessive, {})
        self.assertIn("requires 1-3 AUX_VISUAL", excessive_errors[1])

    def test_light_profile_is_sent_to_both_planning_stages_and_rendered(self) -> None:
        backend = FakePlannerBackend(
            [
                song_bible(),
                lyric_blueprint(),
                scheduled_scene_payload(
                    1,
                    auxiliary_visuals=(
                        ("symbolic_object", "黒い環が形成されて砕ける。"),
                    ),
                )
                + "\n"
                + scheduled_scene_payload(
                    2,
                    auxiliary_visuals=(
                        ("spatial_metaphor", "白い道が奥へ伸びる。"),
                    ),
                ),
            ]
        )
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        plan = planning.generate_mv_plan(
            brief,
            timeline,
            backend,
            scenes_per_batch=2,
            retry_max=1,
            visual_enrichment_profile="lyric_visuals_light_8b",
        )
        self.assertEqual(
            plan.metadata["visual_enrichment_profile"]["profile_id"],
            "lyric_visuals_light_8b",
        )
        song_request = json.loads(backend.calls[0]["messages"][1]["content"])
        lyric_request = json.loads(backend.calls[1]["messages"][1]["content"])
        scene_request = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertEqual(
            song_request["visual_enrichment_profile"]["assignment_mode"],
            "cycle",
        )
        self.assertEqual(
            scene_request["scenes"][0]["auxiliary_visual_contract"][
                "required_kind"
            ],
            "symbolic_object",
        )
        self.assertEqual(
            lyric_request["protocol"], "clmv-lyric-action-line-v1"
        )
        self.assertEqual(
            scene_request["scenes"][1]["locked_lyric_action_blueprint"][
                "lyric_anchor_index"
            ],
            1,
        )
        self.assertIn(
            "VISUAL ENRICHMENT PROFILE: lyric_visuals_light_8b",
            backend.calls[0]["messages"][0]["content"],
        )
        rendered = renderer.render_planned_markdown(brief, timeline, plan)
        self.assertIn("* 付加映像として、黒い環が形成されて砕ける。", rendered)
        self.assertIn(
            "<Subject 1>は指先を対象面へ接触させて軌跡を刻む。",
            rendered,
        )
        self.assertNotIn("片腕を空へ伸ばして踏み出す", rendered)

    def test_planning_brief_uses_existing_subset_only(self) -> None:
        brief = brief_parser.parse_planning_brief(BRIEF)
        self.assertEqual(len(brief.subjects), 1)
        self.assertEqual(len(brief.retention), 1)
        self.assertEqual(len(brief.common), 1)
        self.assertEqual(brief.to_markdown().strip(), BRIEF.strip())
        with self.assertRaisesRegex(errors.PlanningBriefError, "Unsupported"):
            brief_parser.parse_planning_brief(BRIEF + "\n# シーン 5秒\n* 不正。\n")

        with self.assertRaisesRegex(
            errors.PlanningBriefError,
            r"# 共通プロンプト cannot contain direct speech at line 2",
        ):
            brief_parser.parse_planning_brief(
                "# 共通プロンプト\n* 額に「天満宮」と表示する。\n"
            )

    def test_pseudo_text_wording_in_planning_brief_is_preserved(self) -> None:
        source = BRIEF.replace(
            "荒い線による立体的なラフスケッチとして描く。",
            "歌詞本文を読める形で表示せず、非可読の文字らしい筆跡又は彫り跡として表現してよい。",
        )
        parsed = brief_parser.parse_planning_brief(source)

        self.assertIn("文字らしい", parsed.common[0])
        self.assertEqual(parsed.to_markdown().strip(), source.strip())

    def test_song_bible_accepts_only_a_complete_body_when_end_marker_is_omitted(self) -> None:
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        without_end = song_bible().removesuffix("\nEND_SONG_BIBLE")

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            parsed = validation.parse_song_bible_response(
                without_end,
                protector,
                expected_sections=("[Chorus]",),
            )

        self.assertEqual(parsed.visual_arc, "暗闇から光へ進む。")
        self.assertIn("omitted END_SONG_BIBLE", "\n".join(captured.output))

        with self.assertRaisesRegex(
            errors.PlannerResponseError,
            "unknown or misplaced field",
        ):
            validation.parse_song_bible_response(
                without_end + "\nUNEXPECTED\t余分な行",
                protector,
                expected_sections=("[Chorus]",),
            )

    def test_song_bible_recovers_only_an_omitted_visual_arc(self) -> None:
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        without_arc = "\n".join(
            line
            for line in song_bible().splitlines()
            if not line.startswith("VISUAL_ARC\t")
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            parsed = validation.parse_song_bible_response(
                without_arc,
                protector,
                expected_sections=("[Chorus]",),
            )

        self.assertIn("各楽曲セクション固有の視覚変化", parsed.visual_arc)
        self.assertEqual(
            parsed.visual_enrichment_strategy,
            "選択プロファイルに従う。",
        )
        self.assertIn("omitted VISUAL_ARC", "\n".join(captured.output))

        without_arc_or_enrichment = "\n".join(
            line
            for line in without_arc.splitlines()
            if not line.startswith("VISUAL_ENRICHMENT_STRATEGY\t")
        )
        with self.assertRaisesRegex(
            errors.PlannerResponseError,
            "expected VISUAL_ARC",
        ):
            validation.parse_song_bible_response(
                without_arc_or_enrichment,
                protector,
                expected_sections=("[Chorus]",),
            )

        with self.assertRaisesRegex(
            errors.PlannerResponseError,
            "cannot recover both",
        ):
            validation.parse_song_bible_response(
                without_arc.removesuffix("\nEND_SONG_BIBLE"),
                protector,
                expected_sections=("[Chorus]",),
            )

    def test_song_bible_rejects_trailing_content_after_end_marker(self) -> None:
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        with self.assertRaisesRegex(
            errors.PlannerResponseError,
            "must start with SONG_BIBLE and end with END_SONG_BIBLE",
        ):
            validation.parse_song_bible_response(
                song_bible() + "\n余分な末尾",
                protector,
                expected_sections=("[Chorus]",),
            )

    def test_timeline_parser_locks_sections_lyrics_and_audio(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        self.assertEqual(len(timeline.scenes), 2)
        self.assertEqual(timeline.scenes[1].source_start_ms, 4000)
        self.assertEqual(timeline.scenes[1].lyrics[0].section_kind, "chorus")
        self.assertEqual(timeline.scenes[1].lyrics[0].text, "闇を越えて進め")
        with self.assertRaisesRegex(errors.TimelineParseError, "range length"):
            timeline_parser.parse_prompt_timeline(
                TIMELINE.replace("00:04.000-00:09.000", "00:04.000-00:08.000")
            )
        silent_with_lyrics = TIMELINE.replace(
            "// 検出状態: silent。ソース範囲 00:00.000-00:04.000。",
            "// 検出状態: silent。ソース範囲 00:00.000-00:04.000。\n"
            "// 歌詞: 無音区間には置けない",
        )
        with self.assertRaisesRegex(errors.TimelineParseError, "resolved Lyrics"):
            timeline_parser.parse_prompt_timeline(silent_with_lyrics)

    def test_timeline_parser_accepts_a_contiguous_limiter_subset(self) -> None:
        subset = TIMELINE.replace("// シーン 1", "// シーン 8", 1).replace(
            "// シーン 2", "// シーン 9", 1
        ).replace(
            "# シーン 4秒\n", "# シーン 4秒 継続\n", 1
        ).replace(
            "00:00.000-00:04.000", "01:06.000-01:10.000", 1
        ).replace(
            "00:04.000-00:09.000", "01:10.000-01:15.000", 1
        )

        timeline = timeline_parser.parse_prompt_timeline(subset)

        self.assertEqual([scene.scene_id for scene in timeline.scenes], [8, 9])
        self.assertEqual(timeline.scenes[0].source_start_ms, 66_000)
        self.assertEqual(timeline.scenes[1].source_end_ms, 75_000)
        self.assertTrue(timeline.scenes[0].is_continue)

    def test_scene_limiter_output_can_feed_planner_directly(self) -> None:
        limited = scene_limiter.limit_reduced_markdown_scenes(
            TIMELINE,
            1,
            scene_start_number=2,
        )

        timeline = timeline_parser.parse_prompt_timeline(limited)

        self.assertEqual([scene.scene_id for scene in timeline.scenes], [2])
        self.assertEqual(timeline.scenes[0].source_start_ms, 4_000)
        self.assertEqual(timeline.scenes[0].source_end_ms, 9_000)
        self.assertTrue(timeline.scenes[0].is_continue)

        backend = FakePlannerBackend([song_bible(), scene_payload(2)])
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline,
            backend,
            scenes_per_batch=1,
            retry_max=0,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [2])
        rendered = renderer.render_planned_markdown(
            brief_parser.parse_planning_brief(BRIEF),
            timeline,
            plan,
        )
        self.assertIn("// シーン 2\n# シーン 5秒 継続", rendered)
        self.assertEqual(timeline_parser.parse_prompt_timeline(rendered), timeline)

    def test_timeline_parser_rejects_a_gap_inside_limiter_subset(self) -> None:
        subset = TIMELINE.replace("// シーン 1", "// シーン 8", 1).replace(
            "// シーン 2", "// シーン 10", 1
        ).replace(
            "# シーン 4秒\n", "# シーン 4秒 継続\n", 1
        ).replace(
            "00:00.000-00:04.000", "01:06.000-01:10.000", 1
        ).replace(
            "00:04.000-00:09.000", "01:10.000-01:15.000", 1
        )

        with self.assertRaisesRegex(errors.TimelineParseError, "// シーン 9"):
            timeline_parser.parse_prompt_timeline(subset)

    def test_complete_timeline_still_requires_zero_start_and_no_continuation(self) -> None:
        with self.assertRaisesRegex(errors.TimelineParseError, "start at 0ms"):
            timeline_parser.parse_prompt_timeline(
                TIMELINE.replace(
                    "00:00.000-00:04.000", "00:01.000-00:05.000", 1
                ).replace(
                    "00:04.000-00:09.000", "00:05.000-00:10.000", 1
                )
            )
        with self.assertRaisesRegex(errors.TimelineParseError, "cannot be a continuation"):
            timeline_parser.parse_prompt_timeline(
                TIMELINE.replace("# シーン 4秒\n", "# シーン 4秒 継続\n", 1)
            )

    def test_scene_validation_and_renderer_preserve_timeline(self) -> None:
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(brief.to_markdown())
        plans = []
        for scene in timeline.scenes:
            recovered, scene_errors = validation.parse_scene_response(
                scene_payload(scene.scene_id),
                {scene.scene_id: scene},
                protector,
            )
            self.assertEqual(scene_errors, {})
            plans.append(recovered[scene.scene_id])
        bible = validation.parse_song_bible_response(
            song_bible(), protector, expected_sections=("[Chorus]",)
        )
        output = renderer.render_planned_markdown(
            brief, timeline, structures.MVPlan(bible, tuple(plans))
        )
        self.assertIn("// 楽曲セクション: [Chorus]", output)
        self.assertIn("// 歌詞: 闇を越えて進め", output)
        self.assertIn("* リップシンク: <Subject 1> <- ソースボーカル", output)
        self.assertIn("反復字形", output)
        self.assertIn("鏡文字及び反射文字を作らない", output)
        self.assertEqual(timeline_parser.parse_prompt_timeline(output), timeline)

        removed_field = song_bible().replace(
            "CAMERA_STRATEGY\t静止からアーク移動へ展開する。",
            "CONTINUITY_RULE\t人物を維持する。\n"
            "CAMERA_STRATEGY\t静止からアーク移動へ展開する。",
        )
        with self.assertRaisesRegex(errors.PlannerResponseError, "removed"):
            validation.parse_song_bible_response(
                removed_field, protector, expected_sections=("[Chorus]",)
            )

    def test_scene_parser_accepts_scene_wide_action_indices_across_shots(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "\nEND_SCENE",
            "\n".join(
                (
                    "",
                    "SHOT\t2500",
                    "COMPOSITION\t人物と変化後の対象を斜め後方から示す。",
                    "ACTION\t2\tCLMPSUB1Xは対象から片手を離して身体を起こす。",
                    "ACTION\t3\tCLMPSUB1Xは一歩後退して両腕を体側へ戻す。",
                    "ENVIRONMENT\t黒い層が奥へ流れ、対象の輪郭が残る。",
                    "CAMERA\tpull\tlarge\tmoderate\t対象の斜め後方から人物の横を通って後退し、前景の対象と背景の黒い層に視差を作り、人物の全身を含む広い視点で終える。",
                    "END_SHOT",
                    "END_SCENE",
                )
            ),
        )

        recovered, scene_errors = validation.parse_scene_response(
            response,
            {2: timeline.scenes[1]},
            protector,
        )

        self.assertEqual(scene_errors, {})
        self.assertEqual(len(recovered[2].shots), 2)
        self.assertEqual(len(recovered[2].shots[1].subject_actions), 2)

    def test_scene_parser_rejects_ambiguous_action_index_gap(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "\nEND_SCENE",
            "\n".join(
                (
                    "",
                    "SHOT\t2500",
                    "COMPOSITION\t人物と変化後の対象を斜め後方から示す。",
                    "ACTION\t3\tCLMPSUB1Xは対象から片手を離して身体を起こす。",
                    "ENVIRONMENT\t黒い層が奥へ流れ、対象の輪郭が残る。",
                    "CAMERA\tpull\tlarge\tmoderate\t対象の斜め後方から人物の横を通って後退し、前景の対象と背景の黒い層に視差を作り、人物の全身を含む広い視点で終える。",
                    "END_SHOT",
                    "END_SCENE",
                )
            ),
        )

        recovered, scene_errors = validation.parse_scene_response(
            response,
            {2: timeline.scenes[1]},
            protector,
        )

        self.assertEqual(recovered, {})
        self.assertIn("ACTION indices must start at 1 or 2", scene_errors[2])

    def test_camera_surface_variants_are_normalized_without_scene_retry(self) -> None:
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(brief.to_markdown())
        payload = scene_payload(1).replace(
            "CAMERA\tstatic\tnone\tnone\t斜め後方の低い位置から輪郭を捉える。",
            "CAMERA\tstatic\tnone\tnone\tカメラは斜め後方の低い位置から輪郭を捉える。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            payload, {1: timeline.scenes[0]}, protector
        )
        self.assertEqual(scene_errors, {})
        planned = recovered[1]
        self.assertEqual(planned.shots[0].camera.speed, "none")
        self.assertEqual(
            planned.shots[0].camera.description,
            "斜め後方の低い位置から輪郭を捉える。",
        )
        output = renderer.render_planned_markdown(
            brief,
            timeline,
            structures.MVPlan(
                validation.parse_song_bible_response(
                    song_bible(), protector, expected_sections=("[Chorus]",)
                ),
                (
                    planned,
                    validation.parse_scene_response(
                        scene_payload(2), {2: timeline.scenes[1]}, protector
                    )[0][2],
                ),
            ),
        )
        self.assertIn("* カメラは固定し、移動せず、斜め後方", output)

        legacy_payload = scene_payload(1).replace(
            "CAMERA\tstatic\tnone\tnone\t",
            "CAMERA\tstatic\tnone\tslow\t",
        )
        legacy_plan = validation.parse_scene_response(
            legacy_payload, {1: timeline.scenes[0]}, protector
        )[0][1]
        self.assertEqual(legacy_plan.shots[0].camera.speed, "none")

    def test_scene_parser_removes_one_duplicated_camera_field_token(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "CAMERA\tarc\tlarge\tfast\t",
            "CAMERA\tCAMERA\tarc\tlarge\tfast\t",
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, response_errors = validation.parse_scene_response(
                response,
                {2: timeline.scenes[1]},
                protector,
            )

        self.assertEqual(response_errors, {})
        self.assertEqual(recovered[2].shots[0].camera.type, "arc")
        self.assertTrue(
            any("repeated the CAMERA field token" in line for line in captured.output)
        )

    def test_only_invalid_scene_is_retried(self) -> None:
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + scene_payload(2, valid=False),
                scene_payload(2),
            ]
        )
        plan = planning.generate_mv_plan(
            brief, timeline, backend, scenes_per_batch=2, retry_max=2
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        self.assertEqual(plan.metadata["request_count"], 3)
        self.assertEqual(plan.metadata["response_protocol"], "clmv-line-v5")
        song_input = json.loads(backend.calls[0]["messages"][1]["content"])
        self.assertNotIn("hard_requirements", song_input)
        self.assertEqual(
            song_input["planning_brief"]["subject_identity"],
            ["CLMPSUB1Xとして描く中性的な人物。"],
        )
        self.assertEqual(
            song_input["planning_brief"]["global_visual_direction"],
            ["荒い線による立体的なラフスケッチとして描く。"],
        )
        self.assertEqual(
            song_input["section_sources"],
            [
                {
                    "label": "[Chorus]",
                    "kind": "chorus",
                    "lyrics": ["闇を越えて進め"],
                }
            ],
        )
        retry_input = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertEqual(
            [scene["scene_id"] for scene in retry_input["scenes"]], [2]
        )
        self.assertEqual(retry_input["requested_scene_ids"], [2])
        self.assertEqual(retry_input["requested_scene_count"], 1)
        self.assertIn("unknown camera type", retry_input["retry_feedback"])
        self.assertNotIn("previous_scene_intent", retry_input)
        self.assertEqual(
            retry_input["scenes"][0]["previous_scene_tail"]["scene_id"], 1
        )
        self.assertEqual(
            retry_input["scenes"][0]["active_section_motifs"],
            [{"section": "[Chorus]", "motif": "強い白光。"}],
        )
        self.assertEqual(
            retry_input["scenes"][0]["lyric_lines"],
            [
                {
                    "index": 1,
                    "section": "[Chorus]",
                    "kind": "chorus",
                    "text": "闇を越えて進め",
                }
            ],
        )
        self.assertEqual(
            retry_input["scenes"][0]["lyric_response_contract"][
                "anchor_policy"
            ],
            "earliest complete depictable predicate with a physical head verb "
            "and all compatible explicit roles, otherwise 1",
        )
        self.assertIn(
            "recognizable concrete target or object",
            retry_input["scenes"][0]["lyric_response_contract"][
                "semantic_fidelity"
            ],
        )
        self.assertIn(
            "actual head verb",
            retry_input["scenes"][0]["lyric_response_contract"][
                "semantic_fidelity"
            ],
        )
        self.assertIn(
            "exact contact point",
            retry_input["scenes"][0]["lyric_response_contract"][
                "interaction_choreography"
            ],
        )
        self.assertIn(
            "before section energy or motifs",
            retry_input["scenes"][0]["lyric_response_contract"][
                "interaction_choreography"
            ],
        )
        self.assertIn(
            "retained Subject property",
            retry_input["scenes"][0]["retention_execution_contract"][
                "transformation_boundary"
            ],
        )
        self.assertIn(
            "isolated non-linguistic short scratches or grooves",
            retry_input["scenes"][0]["lyric_response_contract"][
                "inscription_fallback"
            ],
        )
        self.assertEqual(
            retry_input["scenes"][0]["recent_scene_patterns"],
            [
                {
                    "scene_id": 1,
                    "camera_types": ["static"],
                    "camera_motion_signatures": ["static:none:none"],
                    "auxiliary_visual_kinds": [],
                }
            ],
        )
        camera_contract = retry_input["scenes"][0][
            "camera_choreography_contract"
        ]
        self.assertIn("distinct start view", camera_contract["start_path_end"])
        self.assertIn("foreground", camera_contract["parallax"])
        self.assertIn("never track only a hand", camera_contract["tracking"])
        self.assertEqual(camera_contract["long_scene"], "not applicable")

        long_scene = structures.TimelineScene(
            scene_id=99,
            duration_seconds=15,
            is_continue=True,
            state="voiced",
            source_start_ms=0,
            source_end_ms=15_000,
            lyrics=(),
            lip_sync_lines=(),
            soundscape_lines=(),
        )
        self.assertIn(
            "2-3 motivated Shots",
            planning._camera_choreography_contract(long_scene)["long_scene"],
        )
        full = visual_profiles.load_visual_profile("lyric_visuals_full")
        long_lyric = structures.TimelineScene(
            scene_id=2,
            duration_seconds=14,
            is_continue=True,
            state="voiced",
            source_start_ms=0,
            source_end_ms=14_000,
            lyrics=(
                structures.TimelineLyric(
                    text="痕跡を刻む",
                    section_label="[Chorus]",
                    section_kind="chorus",
                ),
            ),
            lip_sync_lines=(),
            soundscape_lines=(),
        )
        self.assertIn(
            "required 2-3 Shots",
            planning._camera_choreography_contract(long_lyric, full)[
                "shot_count"
            ],
        )
        self.assertIn(
            "2-3 motivated Shots",
            planning._camera_choreography_contract(long_lyric, full)[
                "long_scene"
            ],
        )
        light = visual_profiles.load_visual_profile(
            "lyric_visuals_light_8b"
        )
        light_contract = planning._camera_choreography_contract(
            long_lyric, light
        )
        self.assertIn("required 2-2 Shots", light_contract["shot_count"])
        self.assertIn("exactly 2 motivated Shots", light_contract["long_scene"])
        self.assertNotIn("2-3 motivated Shots", light_contract["long_scene"])
        self.assertEqual(
            [
                value["type"]
                for value in light_contract["required_camera_sequence"]
            ],
            ["push", "truck"],
        )
        self.assertEqual(
            light_contract["required_camera_sequence"][0]["speed"],
            "fast",
        )
        camera_families = []
        arc_scene_count = 0
        for scene_id in range(1, 7):
            varied_scene = structures.TimelineScene(
                scene_id=scene_id,
                duration_seconds=14,
                is_continue=scene_id > 1,
                state="voiced",
                source_start_ms=0,
                source_end_ms=14_000,
                lyrics=long_lyric.lyrics,
                lip_sync_lines=(),
                soundscape_lines=(),
            )
            sequence = planning._camera_choreography_contract(
                varied_scene, light
            )["required_camera_sequence"]
            types = tuple(value["type"] for value in sequence)
            camera_families.append(types)
            if "arc" in types:
                arc_scene_count += 1
        self.assertEqual(len(set(camera_families)), 6)
        self.assertEqual(arc_scene_count, 3)
        line_shape = planning._line_protocol_shape_contract(long_lyric, light)
        self.assertEqual(line_shape["minimum_shot_blocks"], 2)
        self.assertEqual(line_shape["maximum_shot_blocks"], 2)
        self.assertEqual(line_shape["exact_shot_blocks"], 2)
        self.assertEqual(line_shape["first_shot_start_ms"], 0)
        self.assertEqual(line_shape["end_scene_after_shot_blocks"], 2)
        self.assertEqual(
            line_shape["allowed_lyric_response_mode_tokens"],
            [
                "direct_subject_action",
                "direct_object_action",
                "spatial_metaphor",
            ],
        )
        self.assertEqual(line_shape["exact_auxiliary_visual_lines"], 1)
        self.assertEqual(line_shape["auxiliary_visual_lines_per_shot"], [0, 1])
        reminder = planning._retry_record_shape_reminder(long_lyric, light)
        self.assertIn("exactly 2 complete SHOT-through-END_SHOT blocks", reminder)
        self.assertIn("only after END_SHOT number 2", reminder)
        self.assertIn("literal ASCII tokens without translation", reminder)
        self.assertIn("[0, 1] in Shot order", reminder)
        self.assertIn("Shot 1=push/large/fast", reminder)
        self.assertIn("Shot 2=truck/large/moderate", reminder)
        self.assertIn("do not quote or repeat source lyric text", reminder)

    def test_scene_intent_quotes_are_normalized_because_intent_is_not_rendered(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "Scene 2の映像意図。",
            '歌詞の「落下」と"重圧"を映像化する。',
        )

        recovered, recovered_errors = validation.parse_scene_response(
            response,
            {2: timeline.scenes[1]},
            protector,
        )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(
            recovered[2].scene_intent,
            "歌詞の落下と重圧を映像化する。",
        )

    def test_ambiguous_localized_direct_mode_uses_action_subject_binding(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
            "LYRIC_RESPONSE\t1\t直接動作",
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, recovered_errors = validation.parse_scene_response(
                response,
                {2: timeline.scenes[1]},
                protector,
            )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(
            recovered[2].lyric_response_mode,
            "direct_subject_action",
        )
        self.assertIn("normalized ambiguous localized", "\n".join(captured.output))

    def test_visual_profile_enforces_long_lyric_scene_shot_count(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 12秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:16.000"
            )
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        full = visual_profiles.load_visual_profile("lyric_visuals_full")
        first_shot_only = scene_payload(
            2,
            auxiliary_visuals=(("symbolic_object", "黒い環が砕ける。"),),
        ).replace(
            "CAMERA\tarc\tlarge\tfast\tCLMPSUB1Xの左側から背後を通って右前方へ大きく回り込む。",
            "CAMERA\tpush\tlarge\tfast\t前景の対象越しの斜め遠景から人物へ接近し、遠い背景との視差を広げて接触前の斜め近景で終える。",
        )
        rejected, rejected_errors = validation.parse_scene_response(
            first_shot_only,
            {2: timeline.scenes[1]},
            protector,
            visual_profile=full,
        )
        self.assertEqual(rejected, {})
        self.assertIn("requires 2-3 Shots", rejected_errors[2])

        second_shot = "\n".join(
            (
                "SHOT\t7000",
                "COMPOSITION\t人物と対象を斜め側方の奥行きで示す。",
                "ACTION\t1\tCLMPSUB1Xは対象から手を離して結果を確認する。",
                "ENVIRONMENT\t黒い層が奥へ流れ、対象の輪郭が残る。",
                "CAMERA\ttruck\tlarge\tmoderate\t対象の左斜め側方から前景を横切って右斜め側方へ平行移動し、人物の全身と遠い背景の視差を示す。",
                "END_SHOT",
            )
        )
        two_shots = first_shot_only.replace(
            "\nEND_SCENE", f"\n{second_shot}\nEND_SCENE"
        )
        recovered, recovered_errors = validation.parse_scene_response(
            two_shots,
            {2: timeline.scenes[1]},
            protector,
            visual_profile=full,
        )
        self.assertEqual(recovered_errors, {})
        self.assertEqual(len(recovered[2].shots), 2)

    def test_light_8b_recovers_aux_visual_after_end_shot(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 12秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:16.000"
            )
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile("lyric_visuals_light_8b")
        timeline_scene = structures.TimelineScene(
            scene_id=9,
            duration_seconds=12,
            is_continue=True,
            state=timeline.scenes[1].state,
            source_start_ms=timeline.scenes[1].source_start_ms,
            source_end_ms=timeline.scenes[1].source_end_ms,
            lyrics=timeline.scenes[1].lyrics,
            lip_sync_lines=timeline.scenes[1].lip_sync_lines,
            soundscape_lines=timeline.scenes[1].soundscape_lines,
        )
        required_kind = light.scene_contract(9)["required_kind"]
        self.assertEqual(required_kind, "light_shadow")
        response = "\n".join(
            (
                "SCENE\t9",
                "SCENE_INTENT\t人物が歌詞を身体動作として示す。",
                f"LYRIC_RESPONSE\t1\t{required_kind}",
                "SHOT\t0",
                "COMPOSITION\t人物と前景の対象を斜め構図で示す。",
                "ACTION\t1\tCLMPSUB1Xは対象へ片手を伸ばす。",
                "ENVIRONMENT\t黒い層が透明な水の奥を流れる。",
                "CAMERA\tpull\tlarge\tmoderate\t手元の斜め近景から前景の対象を横切って後退し、人物と遠い黒い層の視差を広げた斜め遠景で終える。",
                "END_SHOT",
                f"AUX_VISUAL\t{required_kind}\t細い黒線が対象から奥へ伸びて二方向へ分岐する。",
                "SHOT\t5000",
                "COMPOSITION\t人物と変化した対象を奥行き方向に並べる。",
                "ACTION\t1\tCLMPSUB1Xは対象から手を離して一歩後退する。",
                "ENVIRONMENT\t黒い層が水中の奥へ流れ続ける。",
                "CAMERA\tarc\tlarge\tfast\t人物の左後方斜めから側面を通って右前方斜めまで広く回り込み、前景の対象と遠い黒い層の視差を変えて接触結果を見せる。",
                "END_SHOT",
                "END_SCENE",
            )
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, recovered_errors = validation.parse_scene_response(
                response,
                {9: timeline_scene},
                protector,
                visual_profile=light,
            )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(len(recovered[9].shots), 2)
        self.assertEqual(len(recovered[9].shots[0].auxiliary_visuals), 1)
        self.assertEqual(len(recovered[9].shots[1].auxiliary_visuals), 0)
        self.assertEqual(
            recovered[9].lyric_response_mode,
            "direct_subject_action",
        )
        self.assertTrue(
            any("after END_SHOT" in line for line in captured.output)
        )
        self.assertTrue(
            any("misbound AUX_VISUAL required_kind" in line for line in captured.output)
        )

    def test_light_8b_reassembles_scene_closed_between_shots(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 14秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:18.000"
            )
        )
        source_scene = timeline.scenes[1]
        timeline_scene = structures.TimelineScene(
            scene_id=16,
            duration_seconds=source_scene.duration_seconds,
            is_continue=source_scene.is_continue,
            state=source_scene.state,
            source_start_ms=source_scene.source_start_ms,
            source_end_ms=source_scene.source_end_ms,
            lyrics=source_scene.lyrics,
            lip_sync_lines=source_scene.lip_sync_lines,
            soundscape_lines=source_scene.soundscape_lines,
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile("lyric_visuals_light_8b")
        required_kind = light.scene_contract(16)["required_kind"]
        response = "\n".join(
            (
                "SCENE\t16",
                "SCENE_INTENT\t人物が外部の対象へ働きかける。",
                "LYRIC_RESPONSE\t1\tdirect_subject_action",
                "SHOT\t0",
                "COMPOSITION\t人物と前景の対象を低い斜め構図で示す。",
                "ACTION\t1\tCLMPSUB1Xは右腕を対象へ伸ばす。",
                "ACTION\t2\tCLMPSUB1Xは左足を踏み出して対象を押す。",
                "ENVIRONMENT\t黒い層が透明な水の奥を流れる。",
                "CAMERA\ttruck\tlarge\tfast\t低い左斜め前方から前景の黒い層を横切って右斜め側方へ移動し、遠い背景との視差を広げた全身像で終える。",
                "END_SHOT",
                "END_SCENE",
                "SCENE\t16",
                "SHOT\t1200",
                "COMPOSITION\t人物と変化した対象を奥行き方向に並べる。",
                "ACTION\t1\tCLMPSUB1Xは対象から両手を離す。",
                "ACTION\t2\tCLMPSUB1Xは一歩後退して両腕を体側へ戻す。",
                f"AUX_VISUAL\t{required_kind}\t濃い影が対象から奥へ伸びて停止する。",
                "ENVIRONMENT\t黒い層が透明な水の奥へ流れ続ける。",
                "CAMERA\tpush\tmedium\tmoderate\t人物と対象を含む斜め遠景から前景の黒い層を抜けて接触点へ進み、遠い背景との視差を広げた斜め近景で終える。",
                "END_SHOT",
                "END_SCENE",
            )
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, recovered_errors = validation.parse_scene_response(
                response,
                {16: timeline_scene},
                protector,
                visual_profile=light,
            )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(len(recovered[16].shots), 2)
        self.assertEqual([shot.start_ms for shot in recovered[16].shots], [0, 1200])
        self.assertTrue(any("reassembled" in line for line in captured.output))

    def test_scene_parser_rejects_two_complete_records_for_same_scene(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = "\n".join((scene_payload(2), scene_payload(2)))

        recovered, recovered_errors = validation.parse_scene_response(
            response,
            {2: timeline.scenes[1]},
            protector,
        )

        self.assertEqual(recovered, {})
        self.assertEqual(recovered_errors[2], "Scene 2 appeared more than once")

    def test_light_8b_deduplicates_identical_scene_aux_visuals(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 12秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:16.000"
            )
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile("lyric_visuals_light_8b")
        required_kind = light.scene_contract(2)["required_kind"]
        aux_line = (
            f"AUX_VISUAL\t{required_kind}\t"
            "細い黒線が対象から奥へ伸びて二方向へ分岐する。"
        )
        response = "\n".join(
            (
                "SCENE\t2",
                "SCENE_INTENT\t人物が歌詞を身体動作として示す。",
                "LYRIC_RESPONSE\t1\tdirect_subject_action",
                "SHOT\t0",
                "COMPOSITION\t人物と前景の対象を斜め構図で示す。",
                "ACTION\t1\tCLMPSUB1Xは対象へ片手を伸ばす。",
                "ENVIRONMENT\t黒い層が透明な水の奥を流れる。",
                "CAMERA\tpush\tlarge\tfast\t前景の対象越しの斜め遠方から人物へ素早く接近し、背景の黒い層との視差を広げて手の接触が見える近景で終える。",
                "END_SHOT",
                aux_line,
                "SHOT\t5000",
                "COMPOSITION\t人物と変化した対象を奥行き方向に並べる。",
                "ACTION\t1\tCLMPSUB1Xは対象から手を離して一歩後退する。",
                "ENVIRONMENT\t黒い層が水中の奥へ流れ続ける。",
                "CAMERA\ttruck\tlarge\tmoderate\t人物の左斜め側方から対象を前景に残して右斜め側方へ平行移動し、背景の黒い層との視差を作って人物の全身を示す。",
                "END_SHOT",
                aux_line,
                "END_SCENE",
            )
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, recovered_errors = validation.parse_scene_response(
                response,
                {2: timeline.scenes[1]},
                protector,
                visual_profile=light,
            )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(
            sum(
                len(shot.auxiliary_visuals)
                for shot in recovered[2].shots
            ),
            1,
        )
        self.assertTrue(
            any("retained its first occurrence" in line for line in captured.output)
        )

    def test_light_8b_keeps_only_final_aux_when_each_shot_has_a_different_one(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 12秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:16.000"
            )
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile("lyric_visuals_light_8b")
        required_kind = light.scene_contract(2)["required_kind"]
        response = "\n".join(
            (
                "SCENE\t2",
                "SCENE_INTENT\t人物が対象へ働きかける。",
                "LYRIC_RESPONSE\t1\tdirect_subject_action",
                "SHOT\t0",
                "COMPOSITION\t人物と対象を斜め構図で示す。",
                "ACTION\t1\tCLMPSUB1Xは対象へ片手を伸ばす。",
                f"AUX_VISUAL\t{required_kind}\t細い影が対象の手前へ集まる。",
                "ENVIRONMENT\t黒い層が透明な水の奥を流れる。",
                "CAMERA\tpush\tlarge\tfast\t前景の対象越しの遠景から人物へ素早く接近し、背景との視差を広げて接触前の斜め近景で終える。",
                "END_SHOT",
                "SHOT\t6000",
                "COMPOSITION\t人物の手と変化した対象を斜め側方から示す。",
                "ACTION\t1\tCLMPSUB1Xは対象へ触れてから手を離す。",
                f"AUX_VISUAL\t{required_kind}\t濃い影が対象から奥へ伸びて停止する。",
                "ENVIRONMENT\t黒い層が透明な水の奥へ流れ続ける。",
                "CAMERA\ttruck\tlarge\tmoderate\t対象の左斜め側方から前景を横切って右斜め側方へ平行移動し、遠い黒い層との視差を変えて結果を示す。",
                "END_SHOT",
                "END_SCENE",
            )
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, recovered_errors = validation.parse_scene_response(
                response,
                {2: timeline.scenes[1]},
                protector,
                visual_profile=light,
            )

        self.assertEqual(recovered_errors, {})
        self.assertEqual(len(recovered[2].shots[0].auxiliary_visuals), 0)
        self.assertEqual(len(recovered[2].shots[1].auxiliary_visuals), 1)
        self.assertIn(
            "対象から奥へ伸びて停止",
            recovered[2].shots[1].auxiliary_visuals[0].description,
        )
        self.assertTrue(
            any("exact-one contract" in line for line in captured.output)
        )

    def test_song_sections_and_scene_lyrics_are_grouped_deterministically(self) -> None:
        source = TIMELINE.replace(
            "// 歌詞: 闇を越えて進め",
            "// 歌詞: 闇を越えて進め\n"
            "// 歌詞: 闇を越えて進め\n"
            "// 楽曲セクション: [Verse 2]\n"
            "// 歌詞: 朝を待つ",
        )
        timeline = timeline_parser.parse_prompt_timeline(source)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        section_sources = planning._section_sources(timeline, protector)
        self.assertEqual(
            section_sources,
            [
                {
                    "label": "[Chorus]",
                    "kind": "chorus",
                    "lyrics": ["闇を越えて進め"],
                },
                {
                    "label": "[Verse 2]",
                    "kind": "verse",
                    "lyrics": ["朝を待つ"],
                },
            ],
        )
        lyric_lines = planning._lyric_lines(timeline.scenes[1], protector)
        self.assertEqual(len(lyric_lines), 3)
        self.assertEqual(
            [line["index"] for line in lyric_lines], [1, 2, 3]
        )
        self.assertEqual(
            [line["text"] for line in lyric_lines[:2]],
            ["闇を越えて進め"] * 2,
        )
        self.assertEqual(lyric_lines[2]["section"], "[Verse 2]")

    def test_scene_records_explicit_direct_lyric_response(self) -> None:
        source = TIMELINE.replace("闇を越えて進め", "I have fallen down")
        timeline = timeline_parser.parse_prompt_timeline(source)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(
            2,
            actions=(
                "CLMPSUB1Xは足場を失い、身体を下方へ落下させる。",
            ),
        )

        planned, errors_by_scene = validation.parse_scene_response(
            response, {2: timeline.scenes[1]}, protector
        )

        self.assertEqual(errors_by_scene, {})
        self.assertEqual(planned[2].lyric_anchor_index, 1)
        self.assertEqual(
            planned[2].lyric_response_mode, "direct_subject_action"
        )
        self.assertIn("下方へ落下", planned[2].shots[0].subject_actions[0])

    def test_screen_text_cues_are_rejected_from_blueprint_and_scene_prose(self) -> None:
        blueprint = lyric_blueprint().replace(
            "対象面に不均一な軌跡が残り",
            "石に刻まれた名前の痕跡が残り",
        )
        recovered, blueprint_errors = parse_lyric_blueprint(blueprint)
        self.assertEqual(recovered, {})
        self.assertIn("screen-text cue '名前'", blueprint_errors[2])

        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(2).replace(
            "人物と背景の奥行きを斜め構図で示す。",
            "人物の背後に横一列の文字らしい痕跡を配置する。",
        )
        planned, scene_errors = validation.parse_scene_response(
            response, {2: timeline.scenes[1]}, protector
        )
        self.assertEqual(planned, {})
        self.assertIn("screen-text cue '文字'", scene_errors[2])

    def test_direct_subject_lyric_response_requires_named_subject_action(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(
            2,
            actions=("足場が崩れ、黒い粒子が下方へ落下する。",),
        )

        planned, errors_by_scene = validation.parse_scene_response(
            response, {2: timeline.scenes[1]}, protector
        )

        self.assertEqual(planned, {})
        self.assertIn("requires at least one ACTION", errors_by_scene[2])

    def test_numbered_scene_cross_reference_is_rejected_from_visible_prose(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        for numbered_reference in (
            "Scene 1で割れた対象を足元の前景に置く。",
            "第1シーンで割れた対象を足元の前景に置く。",
            "ショット2から残る対象を足元の前景に置く。",
        ):
            with self.subTest(numbered_reference=numbered_reference):
                response = scene_payload(2).replace(
                    "人物と背景の奥行きを斜め構図で示す。",
                    numbered_reference,
                )
                planned, errors_by_scene = validation.parse_scene_response(
                    response, {2: timeline.scenes[1]}, protector
                )
                self.assertEqual(planned, {})
                self.assertIn(
                    "internal numbered reference",
                    errors_by_scene[2],
                )
                self.assertIn(
                    "restate the inherited visible state directly",
                    errors_by_scene[2],
                )

    def test_complete_duplicate_scene_is_retried_without_losing_first_scene(self) -> None:
        duplicate_second = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t2", 1
        ).replace("Scene 1の映像意図。", "Scene 2の別の映像意図。").replace(
            "LYRIC_RESPONSE\t0\tinstrumental_continuity",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
        )
        repaired_second = scene_payload(
            2,
            actions=(
                "CLMPSUB1Xは胸を開き、片腕を空へ伸ばす。",
                "CLMPSUB1Xは重心を前へ移して踏み出す。",
            ),
        )
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + duplicate_second,
                repaired_second,
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=2,
            retry_max=2,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        self.assertEqual(
            plan.metadata["duplicate_scene_diagnostics"],
            [{"scene_id": 2, "duplicate_of": 1}],
        )
        retry_input = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertEqual(retry_input["requested_scene_ids"], [2])
        self.assertIn("duplicates the complete visual plan", retry_input["retry_feedback"])
        self.assertEqual(
            retry_input["scenes"][0]["avoid_duplicate_plans"][0]["scene_id"],
            1,
        )
        self.assertNotIn(
            "shots", retry_input["scenes"][0]["avoid_duplicate_plans"][0]
        )
        self.assertEqual(
            retry_input["scenes"][0]["duplicate_repair"],
            {
                "retry_index": 1,
                "duplicate_of_scene_ids": [1],
                "required_shot_count": 1,
                "required_first_shot_action_count": 2,
            },
        )
        self.assertEqual(
            retry_input["scenes"][0]["previous_scene_tail"]["scene_id"], 1
        )

    def test_duplicate_repair_shape_is_a_hint_not_an_acceptance_gate(self) -> None:
        duplicate_second = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t2", 1
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。").replace(
            "LYRIC_RESPONSE\t0\tinstrumental_continuity",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
        )
        distinct_second = scene_payload(
            2,
            actions=(
                "CLMPSUB1Xは両腕を横へ広げる。",
                "CLMPSUB1Xは身体を半回転させる。",
                "CLMPSUB1Xは視線を光へ向けて姿勢を保つ。",
            ),
        )
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + duplicate_second,
                distinct_second,
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=2,
            retry_max=1,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        retry_input = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertEqual(
            retry_input["scenes"][0]["duplicate_repair"][
                "required_first_shot_action_count"
            ],
            2,
        )
        self.assertEqual(len(plan.scenes[1].shots[0].subject_actions), 3)

    def test_exact_auxiliary_visual_repetition_retries_only_later_scene(self) -> None:
        repeated = "細い帯が奥へ伸び、二方向へ分岐して停止する。"
        backend = FakePlannerBackend(
            [
                song_bible(),
                scheduled_scene_payload(
                    1,
                    auxiliary_visuals=(("spatial_trajectory", repeated),),
                )
                + "\n"
                + scheduled_scene_payload(
                    2,
                    auxiliary_visuals=(("spatial_metaphor", repeated),),
                ),
                scheduled_scene_payload(
                    2,
                    auxiliary_visuals=(
                        (
                            "material_transformation",
                            "硬い層が崩れ、粒子へほどけて奥へ消える。",
                        ),
                    ),
                ),
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=2,
            retry_max=2,
            visual_enrichment_profile="lyric_visuals_full",
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        self.assertEqual(
            plan.metadata["duplicate_auxiliary_visual_diagnostics"],
            [{"scene_id": 2, "duplicate_of": 1}],
        )
        retry_input = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertEqual(retry_input["requested_scene_ids"], [2])
        self.assertIn(
            "repeats an exact AUX_VISUAL",
            retry_input["retry_feedback"],
        )
        self.assertEqual(
            retry_input["scenes"][0]["auxiliary_visual_repair"],
            {
                "retry_index": 1,
                "duplicate_of_scene_ids": [1],
                "required_development_operation": "reveal_or_occlude",
            },
        )

    def test_light_8b_repairs_only_duplicated_auxiliary_line(self) -> None:
        repeated = "暗い帯が人物の周囲へ集まり、そのまま下へ流れる。"
        replacement = "前景の薄い膜が左右へ開き、奥の硬い輪を露出させる。"
        backend = FakePlannerBackend(
            [
                song_bible(),
                lyric_blueprint(),
                scheduled_scene_payload(
                    1,
                    auxiliary_visuals=(("symbolic_object", repeated),),
                )
                + "\n"
                + scheduled_scene_payload(
                    2,
                    auxiliary_visuals=(("spatial_metaphor", repeated),),
                ),
                f"AUX_VISUAL\tspatial_metaphor\t{replacement}",
            ]
        )

        with self.assertLogs("cl_mv_prompt_planner", level="INFO") as captured:
            plan = planning.generate_mv_plan(
                brief_parser.parse_planning_brief(BRIEF),
                timeline_parser.parse_prompt_timeline(TIMELINE),
                backend,
                scenes_per_batch=2,
                retry_max=10,
                visual_enrichment_profile="lyric_visuals_light_8b",
            )

        self.assertEqual(len(backend.calls), 4)
        repair_input = json.loads(backend.calls[3]["messages"][1]["content"])
        self.assertEqual(
            repair_input["protocol"],
            "clmv-auxiliary-visual-repair-line-v1",
        )
        self.assertEqual(repair_input["expected_kind"], "spatial_metaphor")
        self.assertEqual(
            repair_input["forbidden_exact_descriptions"], [repeated]
        )
        self.assertEqual(
            plan.scenes[1].shots[0].auxiliary_visuals[0].description,
            replacement,
        )
        self.assertIn("targeted one-line inference", "\n".join(captured.output))

    def test_light_8b_accepts_valid_scene_when_targeted_aux_repair_fails(self) -> None:
        repeated = "暗い帯が人物の周囲へ集まり、そのまま下へ流れる。"
        backend = FakePlannerBackend(
            [
                song_bible(),
                lyric_blueprint(),
                scheduled_scene_payload(
                    1,
                    auxiliary_visuals=(("symbolic_object", repeated),),
                )
                + "\n"
                + scheduled_scene_payload(
                    2,
                    auxiliary_visuals=(("spatial_metaphor", repeated),),
                ),
                "INVALID",
                "AUX_VISUAL\twrong_kind\t別の動き。",
            ]
        )

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            plan = planning.generate_mv_plan(
                brief_parser.parse_planning_brief(BRIEF),
                timeline_parser.parse_prompt_timeline(TIMELINE),
                backend,
                scenes_per_batch=2,
                retry_max=10,
                visual_enrichment_profile="lyric_visuals_light_8b",
            )

        self.assertEqual(len(backend.calls), 5)
        self.assertEqual(
            plan.scenes[1].shots[0].auxiliary_visuals[0].description,
            repeated,
        )
        self.assertIn(
            "instead of entering a full-Scene retry loop",
            "\n".join(captured.output),
        )

    def test_previous_scene_tail_omits_auxiliary_creative_prose(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        full = visual_profiles.load_visual_profile("lyric_visuals_full")
        recovered, recovered_errors = validation.parse_scene_response(
            scheduled_scene_payload(
                1,
                auxiliary_visuals=(("symbolic_object", "黒い輪が砕ける。"),),
            ),
            {1: timeline.scenes[0]},
            protector,
            visual_profile=full,
        )

        self.assertEqual(recovered_errors, {})
        tail = planning._previous_scene_tail(recovered[1], protector)
        self.assertNotIn("last_auxiliary_visuals", tail)
        self.assertIn("final_action", tail)
        self.assertIn("environment", tail)

    def test_duplicate_retry_switches_to_chronological_single_scene_requests(self) -> None:
        base_timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        second = base_timeline.scenes[1]
        timeline = structures.TimelineDocument(
            scenes=(
                base_timeline.scenes[0],
                second,
                structures.TimelineScene(
                    scene_id=3,
                    duration_seconds=5,
                    is_continue=True,
                    state=second.state,
                    source_start_ms=9000,
                    source_end_ms=14000,
                    lyrics=second.lyrics,
                    lip_sync_lines=second.lip_sync_lines,
                    soundscape_lines=second.soundscape_lines,
                ),
            )
        )
        duplicate_second = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t2", 1
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。").replace(
            "LYRIC_RESPONSE\t0\tinstrumental_continuity",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
        )
        duplicate_third = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t3", 1
        ).replace("Scene 1の映像意図。", "Scene 3の映像意図。").replace(
            "LYRIC_RESPONSE\t0\tinstrumental_continuity",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
        )
        repaired_second = scene_payload(
            2,
            actions=(
                "CLMPSUB1Xは胸を開き、片腕を空へ伸ばす。",
                "CLMPSUB1Xは重心を前へ移して踏み出す。",
            ),
        )
        distinct_third = scene_payload(
            3,
            actions=(
                "CLMPSUB1Xは両腕を横へ広げる。",
                "CLMPSUB1Xは身体を半回転させる。",
                "CLMPSUB1Xは視線を光へ向けて姿勢を保つ。",
            ),
        )
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + duplicate_second + "\n" + duplicate_third,
                repaired_second,
                distinct_third,
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline,
            backend,
            scenes_per_batch=3,
            retry_max=1,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2, 3])
        scene_requests = [
            json.loads(call["messages"][1]["content"])
            for call in backend.calls[1:]
        ]
        self.assertEqual(
            [request["requested_scene_ids"] for request in scene_requests],
            [[1, 2, 3], [2], [3]],
        )
        self.assertEqual(
            scene_requests[2]["scenes"][0]["previous_scene_tail"]["scene_id"],
            2,
        )
        self.assertEqual(
            [
                value["scene_id"]
                for value in scene_requests[2]["scenes"][0][
                    "avoid_duplicate_plans"
                ]
            ],
            [1, 2],
        )
        self.assertEqual(
            scene_requests[2]["scenes"][0]["duplicate_repair"],
            {
                "retry_index": 1,
                "duplicate_of_scene_ids": [1, 2],
                "required_shot_count": 1,
                "required_first_shot_action_count": 3,
            },
        )

    def test_repeated_duplicate_repair_changes_binding_shape_each_retry(self) -> None:
        duplicate_second = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t2", 1
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。").replace(
            "LYRIC_RESPONSE\t0\tinstrumental_continuity",
            "LYRIC_RESPONSE\t1\tdirect_subject_action",
        )
        repaired_second = scene_payload(
            2,
            actions=(
                "CLMPSUB1Xは両肩を引いて光を見る。",
                "CLMPSUB1Xは片腕を横へ伸ばす。",
                "CLMPSUB1Xは重心を前へ移して姿勢を保つ。",
            ),
        )
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + duplicate_second,
                duplicate_second,
                repaired_second,
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=2,
            retry_max=3,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        requests = [
            json.loads(call["messages"][1]["content"])
            for call in backend.calls[2:]
        ]
        self.assertEqual(
            [
                request["scenes"][0]["duplicate_repair"][
                    "required_first_shot_action_count"
                ]
                for request in requests
            ],
            [2, 3],
        )

    def test_all_invalid_camera_schema_falls_back_to_serial_repairs(self) -> None:
        invalid_first = scene_payload(1).replace(
            "CAMERA\tstatic\tnone\tnone",
            "CAMERA\ttype\tamplitude\tspeed",
        )
        invalid_second = scene_payload(2).replace(
            "CAMERA\tarc\tlarge\tfast",
            "CAMERA\ttype\tamplitude\tspeed",
        )
        backend = FakePlannerBackend(
            [
                song_bible(),
                invalid_first + "\n" + invalid_second,
                scene_payload(1),
                scene_payload(2),
            ]
        )
        plan = planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=2,
            retry_max=2,
        )
        self.assertEqual([scene.scene_id for scene in plan.scenes], [1, 2])
        requests = [
            json.loads(call["messages"][1]["content"])
            for call in backend.calls[1:]
        ]
        self.assertEqual(
            [request["requested_scene_ids"] for request in requests],
            [[1, 2], [1], [2]],
        )
        self.assertIn("Scene 1 Shot 1", requests[1]["retry_feedback"])
        self.assertNotIn("Scene 2 Shot 1", requests[1]["retry_feedback"])
        self.assertIn("Scene 2 Shot 1", requests[2]["retry_feedback"])
        self.assertNotIn("Scene 1 Shot 1", requests[2]["retry_feedback"])
        for request in requests[1:]:
            repair = request["scenes"][0]["camera_protocol_repair"]
            self.assertEqual(repair["required_camera_type"], "arc")
            self.assertEqual(repair["required_camera_amplitude"], "medium")
            self.assertEqual(repair["required_camera_speed"], "moderate")
            self.assertEqual(
                repair["forbidden_literal_values"],
                ["type", "amplitude", "speed"],
            )

    def test_camera_guard_warn_preserves_plan_and_strict_retries_scene(self) -> None:
        conflicting = scene_payload(1).replace(
            "CAMERA\tstatic\tnone\tnone\t斜め後方の低い位置から輪郭を捉える。",
            "CAMERA\tpush\tmedium\tmoderate\t被写体から後退して遠ざかる。",
        )
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)

        warn_backend = FakePlannerBackend(
            [song_bible(), conflicting + "\n" + scene_payload(2)]
        )
        warned = planning.generate_mv_plan(
            brief,
            timeline,
            warn_backend,
            scenes_per_batch=2,
            retry_max=1,
            camera_guard="warn",
        )
        self.assertEqual(len(warn_backend.calls), 2)
        self.assertEqual(
            warned.scenes[0].shots[0].camera.description,
            "被写体から後退して遠ざかる。",
        )
        self.assertEqual(len(warned.metadata["camera_warnings"]), 1)

        strict_backend = FakePlannerBackend(
            [
                song_bible(),
                conflicting + "\n" + scene_payload(2),
                scene_payload(1),
            ]
        )
        strict = planning.generate_mv_plan(
            brief,
            timeline,
            strict_backend,
            scenes_per_batch=2,
            retry_max=1,
            camera_guard="strict",
        )
        self.assertEqual(len(strict_backend.calls), 3)
        self.assertEqual(strict.metadata["camera_warnings"], [])
        strict_retry = json.loads(strict_backend.calls[2]["messages"][1]["content"])
        self.assertEqual(strict_retry["requested_scene_ids"], [1])
        self.assertIn("camera type 'push'", strict_retry["retry_feedback"])

    def test_camera_guard_does_not_interpret_an_ordinary_push_action(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(
            1, actions=("CLMPSUB1Xは重い扉を両手で前方へ押す。",)
        )
        planned = validation.parse_scene_response(
            response, {1: timeline.scenes[0]}, protector
        )[0][1]
        self.assertEqual(validation.camera_guard_issues(planned), [])

        static_subject_motion = scene_payload(1).replace(
            "斜め後方の低い位置から輪郭を捉える。",
            "固定位置で被写体が接近する姿を捉える。",
        )
        static_plan = validation.parse_scene_response(
            static_subject_motion, {1: timeline.scenes[0]}, protector
        )[0][1]
        self.assertEqual(validation.camera_guard_issues(static_plan), [])

    def test_subject_motion_contract_rejects_passive_performance(self) -> None:
        passive = structures.PlannedScene(
            scene_id=1,
            scene_intent="人物を表示する。",
            shots=(
                structures.PlannedShot(
                    start_ms=0,
                    composition="<Subject 1>を中央に置く。",
                    subject_actions=(
                        "<Subject 1>は上下に浮遊して左右へゆっくり揺れる。",
                        "<Subject 1>は視線と口元だけを動かす。",
                    ),
                    environment="背景が流れる。",
                    camera=structures.CameraPlan(
                        "arc", "large", "moderate", "周囲を回り込む。"
                    ),
                ),
            ),
        )

        issues = validation.subject_motion_issues(
            passive, duration_seconds=12
        )

        self.assertEqual(issues[0]["code"], "subject_deliberate_motion_missing")
        self.assertEqual(issues[0]["required_phases"], 2)

    def test_subject_motion_contract_accepts_distinct_body_phases(self) -> None:
        active = structures.PlannedScene(
            scene_id=1,
            scene_intent="人物が空間を横断する。",
            shots=(
                structures.PlannedShot(
                    start_ms=0,
                    composition="<Subject 1>を斜め前方に置く。",
                    subject_actions=(
                        "<Subject 1>は重心を前足へ移して深く踏み込む。",
                        "<Subject 1>は胴体をひねり、右腕を上へ伸ばす。",
                    ),
                    environment="背景が流れる。",
                    camera=structures.CameraPlan(
                        "arc", "large", "moderate", "周囲を回り込む。"
                    ),
                ),
            ),
        )

        self.assertEqual(
            validation.subject_motion_issues(active, duration_seconds=12),
            [],
        )

    def test_subject_motion_contract_accepts_debug_motion_vocabulary(self) -> None:
        action_pairs = (
            (
                "<Subject 1>は左の眼窩に手を当てて顔を傾ける。",
                "<Subject 1>は右の眼窩に手を置き、頭部を左右に揺すぶる。",
            ),
            (
                "<Subject 1>は左の眼窩からインクを引き抜く。",
                "<Subject 1>は右の眼窩からインクを引き抜き、手に集める。",
            ),
            (
                "<Subject 1>は左の眼窩を指でなぞりながら顔を傾ける。",
                "<Subject 1>は右の眼窩を指でなぞり、頭部を左右に動かす。",
            ),
        )
        for actions in action_pairs:
            scene = structures.PlannedScene(
                scene_id=8,
                scene_intent="人物が動作する。",
                shots=(
                    structures.PlannedShot(
                        start_ms=0,
                        composition="<Subject 1>を中央に置く。",
                        subject_actions=actions,
                        environment="背景が流れる。",
                        camera=structures.CameraPlan(
                            "arc", "large", "moderate", "周囲を回り込む。"
                        ),
                    ),
                ),
            )
            self.assertEqual(
                validation.subject_motion_issues(
                    scene, duration_seconds=14
                ),
                [],
            )

    def test_lyric_blueprint_repairs_passive_motion_without_retry(self) -> None:
        passive = lyric_blueprint().replace(
            "CLMPSUB1Xは対象面へ踏み込みながら片腕を引く。",
            "CLMPSUB1Xはその場で静かに浮遊する。",
        ).replace(
            "CLMPSUB1Xは指先を対象面へ接触させて軌跡を刻む。",
            "CLMPSUB1Xは視線と口元だけを動かす。",
        )
        timeline = timeline_parser.parse_prompt_timeline(
            TIMELINE.replace("# シーン 5秒 継続", "# シーン 14秒 継続").replace(
                "00:04.000-00:09.000", "00:04.000-00:18.000"
            )
        )
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)

        with self.assertLogs("cl_mv_prompt_planner", level="WARNING") as captured:
            recovered, response_errors = validation.parse_lyric_action_response(
                passive,
                {2: timeline.scenes[1]},
                protector,
            )

        self.assertEqual(response_errors, {})
        self.assertEqual(len(recovered[2].subject_actions), 4)
        self.assertEqual(
            validation._subject_action_motion_issues(
                scene_id=2,
                actions=recovered[2].subject_actions,
                duration_seconds=14,
            ),
            [],
        )
        self.assertTrue(
            any("instead of retrying the LLM" in line for line in captured.output)
        )

    def test_non_blueprint_motion_is_repaired_without_scene_regeneration(self) -> None:
        passive = structures.PlannedScene(
            scene_id=13,
            scene_intent="周辺の線が変化する。",
            shots=(
                structures.PlannedShot(
                    start_ms=0,
                    composition="<Subject 1>を中央に置く。",
                    subject_actions=(
                        "<Subject 1>の右足に濁った線が浮き上がる。",
                    ),
                    environment="背景の画材が流れる。",
                    camera=structures.CameraPlan(
                        "arc", "large", "moderate", "周囲を回り込む。"
                    ),
                ),
            ),
        )

        repaired, additions = validation.repair_subject_motion(
            passive,
            duration_seconds=4,
        )

        self.assertEqual(len(additions), 1)
        self.assertEqual(repaired.shots[0].subject_actions[0], passive.shots[0].subject_actions[0])
        self.assertIn("<Subject 1>は現在の形状と外観を維持", additions[0])
        self.assertEqual(
            validation.subject_motion_issues(
                repaired, duration_seconds=4
            ),
            [],
        )

    def test_scheduled_profile_repairs_generic_arc_description(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module(
            "node_mv_prompt_planner.placeholders"
        ).ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile(
            "lyric_visuals_light_8b"
        )
        generic = scheduled_scene_payload(
            1,
            auxiliary_visuals=(("symbolic_object", "黒い環が砕ける。"),),
        ).replace(
            "人物の左前方斜めから側面を通って右後方斜めまで大きく回り込み、前景と遠い背景の視差を変える。",
            "人物の周囲を横方向へ移動する。",
        )

        recovered, response_errors = validation.parse_scene_response(
            generic,
            {1: timeline.scenes[0]},
            protector,
            visual_profile=light,
        )

        self.assertEqual(response_errors, {})
        description = recovered[1].shots[0].camera.description
        self.assertIn("左前方斜め", description)
        self.assertIn("側面", description)
        self.assertIn("右後方斜め", description)

    def test_scheduled_profile_accepts_qwen_three_angle_arc_terms(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module(
            "node_mv_prompt_planner.placeholders"
        ).ReferenceProtector()
        protector.protect(BRIEF)
        light = visual_profiles.load_visual_profile(
            "lyric_visuals_light_8b"
        )
        qwen_arc = scheduled_scene_payload(
            1,
            auxiliary_visuals=(("symbolic_object", "黒い環が砕ける。"),),
        ).replace(
            "人物の左前方斜めから側面を通って右後方斜めまで大きく回り込み、前景と遠い背景の視差を変える。",
            "前左三方角のビューから始まり、広い半円を描いて後右三方角へ移動し、前景と背景の視差を強調する。",
        )

        recovered, response_errors = validation.parse_scene_response(
            qwen_arc,
            {1: timeline.scenes[0]},
            protector,
            visual_profile=light,
        )

        self.assertEqual(response_errors, {})
        self.assertIn("前左三方角", recovered[1].shots[0].camera.description)

    def test_camera_guard_flags_orbit_language_for_non_arc_camera(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(1).replace(
            "CAMERA\tstatic\tnone\tnone\t斜め後方の低い位置から輪郭を捉える。",
            "CAMERA\ttilt\tmedium\tmoderate\t被写体の背後を通って右側へ回り込む。",
        )
        planned = validation.parse_scene_response(
            response, {1: timeline.scenes[0]}, protector
        )[0][1]
        issues = validation.camera_guard_issues(planned)
        self.assertEqual(issues[0]["code"], "camera_non_arc_orbit_conflict")

    def test_vocal_guard_warn_preserves_plan_and_strict_retries_scene(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        brief = brief_parser.parse_planning_brief(BRIEF)
        silent_vocalized = scene_payload(1).replace(
            "CLMPSUB1Xは口を閉じたまま肩を引き、ゆっくり振り返る。",
            "CLMPSUB1Xは一歩踏み出して空を見上げ、力強く歌う。",
        )

        warn_backend = FakePlannerBackend(
            [song_bible(), silent_vocalized + "\n" + scene_payload(2)]
        )
        warned = planning.generate_mv_plan(
            brief,
            timeline,
            warn_backend,
            scenes_per_batch=2,
            retry_max=1,
            vocal_guard="warn",
        )
        self.assertEqual(len(warn_backend.calls), 2)
        self.assertEqual(len(warned.metadata["vocal_warnings"]), 1)
        self.assertEqual(warned.metadata["vocal_warnings"][0]["cue"], "歌う")

        strict_backend = FakePlannerBackend(
            [
                song_bible(),
                silent_vocalized + "\n" + scene_payload(2),
                scene_payload(1),
            ]
        )
        strict = planning.generate_mv_plan(
            brief,
            timeline,
            strict_backend,
            scenes_per_batch=2,
            retry_max=1,
            vocal_guard="strict",
        )
        self.assertEqual(len(strict_backend.calls), 3)
        self.assertEqual(strict.metadata["vocal_warnings"], [])
        strict_retry = json.loads(
            strict_backend.calls[2]["messages"][1]["content"]
        )
        self.assertEqual(strict_retry["requested_scene_ids"], [1])
        self.assertIn("vocal cue", strict_retry["retry_feedback"])

    def test_previous_scene_tail_is_sent_across_batch_boundary(self) -> None:
        backend = FakePlannerBackend(
            [song_bible(), scene_payload(1), scene_payload(2)]
        )
        planning.generate_mv_plan(
            brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE),
            backend,
            scenes_per_batch=1,
            retry_max=1,
        )
        first_scene_input = json.loads(backend.calls[1]["messages"][1]["content"])
        second_scene_input = json.loads(backend.calls[2]["messages"][1]["content"])
        self.assertIsNone(first_scene_input["scenes"][0]["previous_scene_tail"])
        tail = second_scene_input["scenes"][0]["previous_scene_tail"]
        self.assertEqual(tail["scene_id"], 1)
        self.assertIn("CLMPSUB1X", tail["final_action"])
        self.assertEqual(tail["camera"]["type"], "static")

    def test_scene_signature_normalizes_surface_only_and_not_partial_similarity(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        first = validation.parse_scene_response(
            scene_payload(1), {1: timeline.scenes[0]}, protector
        )[0][1]
        same_surface = structures.PlannedScene(
            scene_id=2,
            scene_intent="異なる意図。",
            shots=(
                structures.PlannedShot(
                    start_ms=0,
                    composition="人物と背景の奥行きを斜め構図で示す。  ",
                    subject_actions=first.shots[0].subject_actions,
                    environment=first.shots[0].environment,
                    camera=first.shots[0].camera,
                ),
            ),
        )
        self.assertEqual(
            validation.scene_signature(first),
            validation.scene_signature(same_surface),
        )
        changed_action = structures.PlannedScene(
            scene_id=2,
            scene_intent=first.scene_intent,
            shots=(
                structures.PlannedShot(
                    start_ms=0,
                    composition=first.shots[0].composition,
                    subject_actions=("<Subject 1>は反対方向へ一歩進む。",),
                    environment=first.shots[0].environment,
                    camera=first.shots[0].camera,
                ),
            ),
        )
        self.assertNotEqual(
            validation.scene_signature(first),
            validation.scene_signature(changed_action),
        )

    def test_line_protocol_preserves_action_order_and_rejects_bad_indices(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        response = scene_payload(
            1,
            actions=(
                "CLMPSUB1Xは右手を振る。",
                "CLMPSUB1Xは両手を下ろして御辞儀する。",
            ),
        )
        recovered, scene_errors = validation.parse_scene_response(
            response, {1: timeline.scenes[0]}, protector
        )
        self.assertEqual(scene_errors, {})
        self.assertEqual(
            recovered[1].shots[0].subject_actions,
            (
                "<Subject 1>は右手を振る。",
                "<Subject 1>は両手を下ろして御辞儀する。",
            ),
        )
        bible = validation.parse_song_bible_response(
            song_bible(), protector, expected_sections=("[Chorus]",)
        )
        second = validation.parse_scene_response(
            scene_payload(2), {2: timeline.scenes[1]}, protector
        )[0][2]
        output = renderer.render_planned_markdown(
            brief_parser.parse_planning_brief(BRIEF),
            timeline,
            structures.MVPlan(bible, (recovered[1], second)),
        )
        self.assertIn("* 最初に、<Subject 1>は右手を振る。", output)
        self.assertIn(
            "* 最後に、<Subject 1>は両手を下ろして御辞儀する。", output
        )

        invalid = response.replace("ACTION\t2\t", "ACTION\t3\t")
        recovered, scene_errors = validation.parse_scene_response(
            invalid, {1: timeline.scenes[0]}, protector
        )
        self.assertEqual(recovered, {})
        self.assertIn("consecutive from 1", scene_errors[1])

    def test_unprotected_or_invented_references_are_rejected(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        protector.protect(BRIEF)
        raw_reference = scene_payload(2).replace(
            "CLMPSUB1Xは胸を開き、片腕を空へ伸ばして踏み出す。",
            "<Subject 1>が許可されていない生タグのまま動く。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            raw_reference, {2: timeline.scenes[1]}, protector
        )
        self.assertEqual(recovered, {})
        self.assertIn("unprotected reference", scene_errors[2])

        invented = scene_payload(2).replace(
            "CLMPSUB1Xは胸を開き、片腕を空へ伸ばして踏み出す。",
            "CLMPUNKNOWN9Xが現れる。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            invented, {2: timeline.scenes[1]}, protector
        )
        self.assertEqual(recovered, {})
        self.assertIn("invented unknown", scene_errors[2])

        source_vocal_performance = scene_payload(2).replace(
            "CLMPSUB1Xは胸を開き、片腕を空へ伸ばして踏み出す。",
            "CLMPSUB1Xは空を見上げて力強く歌う。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            source_vocal_performance, {2: timeline.scenes[1]}, protector
        )
        self.assertEqual(set(recovered), {2})
        self.assertEqual(scene_errors, {})
        self.assertEqual(
            validation.vocal_guard_issues(
                recovered[2], timeline.scenes[1]
            ),
            [],
        )

        silent_vocalized = scene_payload(1).replace(
            "CLMPSUB1Xは口を閉じたまま肩を引き、ゆっくり振り返る。",
            "CLMPSUB1Xは空を見上げて力強く歌う。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            silent_vocalized, {1: timeline.scenes[0]}, protector
        )
        self.assertEqual(set(recovered), {1})
        self.assertEqual(scene_errors, {})
        self.assertEqual(
            validation.vocal_guard_issues(
                recovered[1], timeline.scenes[0]
            )[0]["cue"],
            "歌う",
        )

        added_shout = scene_payload(2).replace(
            "CLMPSUB1Xは胸を開き、片腕を空へ伸ばして踏み出す。",
            "CLMPSUB1Xは空を見上げて叫ぶ。",
        )
        recovered, scene_errors = validation.parse_scene_response(
            added_shout, {2: timeline.scenes[1]}, protector
        )
        self.assertEqual(set(recovered), {2})
        self.assertEqual(scene_errors, {})
        self.assertEqual(
            validation.vocal_guard_issues(
                recovered[2], timeline.scenes[1]
            )[0]["cue"],
            "叫ぶ",
        )

    def test_node_output_compiles_through_existing_json_pipeline(self) -> None:
        backend = FakePlannerNodeBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + scene_payload(2),
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "planner.gguf"
            model_path.write_bytes(b"gguf")
            node = planner_node.CLMVPromptPlannerGGUF()
            node._backend = backend
            with patch.object(
                planner_node, "resolve_model_name", return_value=model_path
            ), self.assertLogs(
                "cl_mv_prompt_planner", level="INFO"
            ) as captured:
                planned_markdown, planner_json, status = node.plan_mv_prompt(
                    **node_arguments(keep_model_loaded=False)
                )

        self.assertIsNone(backend.ensure_calls[0]["chat_format"])
        self.assertEqual(backend.clear_count, 1)
        planner_payload = json.loads(planner_json)
        self.assertEqual(len(planner_payload["scenes"]), 2)
        self.assertEqual(
            len(planner_payload["metadata"]["locked_timeline"]["scenes"]), 2
        )
        self.assertIn("planned 2 scene(s) in 2 LLM request(s)", status)
        success_output = "\n".join(captured.output)
        self.assertIn("\x1b[96m", success_output)
        self.assertIn(
            "[cl_mv_prompt_planner] success: planned 2 scene(s)",
            success_output,
        )
        self.assertEqual(
            timeline_parser.parse_prompt_timeline(planned_markdown),
            timeline_parser.parse_prompt_timeline(TIMELINE),
        )

        canonical = llmj2e.translate_markdown(
            planned_markdown, FakeLLM(), "system", max_tokens=1024
        )
        generated = jsongen.validate_final_json(
            jsongen.generate_json(mdparse.parse_markdown(canonical))
        )
        self.assertEqual([shot["id"] for shot in generated["shots"]], ["scene_1", "scene_2"])
        self.assertEqual(generated["shots"][1]["continuation_mode"], "guide")

    def test_node_uses_external_model_and_visual_profile_string_overrides(self) -> None:
        backend = FakePlannerNodeBackend(
            [
                song_bible(),
                scene_payload(1) + "\n" + scene_payload(2),
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "external.gguf"
            model_path.write_bytes(b"gguf")
            node = planner_node.CLMVPromptPlannerGGUF()
            node._backend = backend
            with patch.object(
                planner_node,
                "resolve_model_name",
                return_value=model_path,
            ) as resolve:
                _, _, status = node.plan_mv_prompt(
                    **node_arguments(
                        model_name="widget.gguf",
                        model_name_override=" external.gguf ",
                        visual_enrichment_profile="unknown-widget-profile",
                        visual_enrichment_profile_override=" performance_only ",
                    )
                )

        resolve.assert_called_once_with(
            "external.gguf",
            log_name="cl_mv_prompt_planner",
        )
        self.assertEqual(backend.ensure_calls[0]["model_path"], model_path)
        self.assertIn("model=external.gguf", status)
        self.assertIn("visual_enrichment_profile=performance_only", status)

    def test_debug_bundle_preserves_raw_calls_and_final_partial_state(self) -> None:
        backend = FakePlannerBackend(
            [
                song_bible(),
                scene_payload(1),
            ]
        )
        events: list[dict] = []
        final_state: dict = {}
        brief = brief_parser.parse_planning_brief(BRIEF)
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        with self.assertRaisesRegex(errors.MVPlannerError, "unresolved Scene"):
            planning.generate_mv_plan(
                brief,
                timeline,
                backend,
                scenes_per_batch=2,
                retry_max=0,
                debug_events=events,
                debug_state=final_state,
            )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["label"], "song-bible attempt 1")
        self.assertEqual(events[0]["validation_result"], "success")
        self.assertEqual(events[1]["recovered_scene_ids"], [1])
        self.assertEqual(events[1]["pending_scene_ids_after"], [2])
        self.assertEqual(final_state["status"], "error")
        self.assertEqual(final_state["planned_scene_ids"], [1])
        self.assertEqual(final_state["unresolved_scene_ids"], [2])

        with tempfile.TemporaryDirectory() as temp:
            target = debug_output.save_planner_debug_bundle(
                prompt_segments=TIMELINE,
                planning_markdown=BRIEF,
                model_name="planner.gguf",
                settings={"retry_max": 0},
                events=events,
                final_state=final_state,
                error=errors.MVPlannerError("expected test failure"),
                output_directory=Path(temp),
            )
            self.assertEqual(
                (target / "prompt_segments.md").read_text(encoding="utf-8"),
                TIMELINE,
            )
            persisted_state = json.loads(
                (target / "final_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted_state["unresolved_scene_ids"], [2])
            response_files = sorted(target.glob("*_response.txt"))
            self.assertEqual(len(response_files), 2)
            self.assertIn("SCENE\t1", response_files[-1].read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
