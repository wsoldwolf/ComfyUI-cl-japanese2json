from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import FakeLLM, PKG, ROOT, module


brief_parser = module("node_mv_prompt_planner.brief_parser")
errors = module("node_mv_prompt_planner.errors")
debug_output = module("node_mv_prompt_planner.debug_output")
planning = module("node_mv_prompt_planner.planning")
planner_node = module("node_mv_prompt_planner.node")
prompt_loader = module("node_mv_prompt_planner.prompt_loader")
renderer = module("node_mv_prompt_planner.renderer")
structures = module("node_mv_prompt_planner.structures")
timeline_parser = module("node_mv_prompt_planner.timeline_parser")
validation = module("node_mv_prompt_planner.validation")
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
            "CAMERA_STRATEGY\t静止からアーク移動へ展開する。",
            "SECTION_MOTIF\t[Chorus]\t強い白光。",
            "END_SONG_BIBLE",
        )
    )


def scene_payload(
    scene_id: int,
    *,
    valid: bool = True,
    actions: tuple[str, ...] | None = None,
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
        "SHOT\t0",
        "COMPOSITION\t人物と背景の奥行きを斜め構図で示す。",
    ]
    lines.extend(
        f"ACTION\t{index}\t{value}"
        for index, value in enumerate(action_values, start=1)
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


class FakePlannerBackend:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def complete_chat(self, **kwargs):
        self.calls.append(kwargs)
        content = self.payloads.pop(0)
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
    def test_song_bible_target_spec_records_approved_design_boundaries(self) -> None:
        text = (ROOT / "docs" / "cl_mv_prompt_planner_song_bible_spec.md").read_text(
            encoding="utf-8"
        )
        for marker in (
            "clmv-song-bible-line-v2",
            '"hard_requirements"',
            '"lyric_groups"',
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
        self.assertIn("active_section_motifs", scene_prompt)
        self.assertIn("previous_scene_tail", scene_prompt)
        self.assertIn("Never add, delete, merge, split, reorder, or renumber Scenes", scene_prompt)
        for camera_type in validation.CAMERA_TYPES:
            self.assertIn(camera_type, scene_prompt)
        self.assertIn("static must use amplitude none", scene_prompt)
        self.assertIn("speed none", scene_prompt)
        self.assertIn("requested_scene_ids", scene_prompt)
        self.assertIn("Default to exactly one Shot per Scene", scene_prompt)
        self.assertIn("ACTION order is a binding time sequence", scene_prompt)
        self.assertIn("ACTION\t1\t", scene_prompt)
        with self.assertRaisesRegex(errors.MVPlannerError, "Invalid"):
            prompt_loader.load_planner_prompt("../outside.txt")

    def test_planning_brief_uses_existing_subset_only(self) -> None:
        brief = brief_parser.parse_planning_brief(BRIEF)
        self.assertEqual(len(brief.subjects), 1)
        self.assertEqual(len(brief.retention), 1)
        self.assertEqual(len(brief.common), 1)
        self.assertEqual(brief.to_markdown().strip(), BRIEF.strip())
        with self.assertRaisesRegex(errors.PlanningBriefError, "Unsupported"):
            brief_parser.parse_planning_brief(BRIEF + "\n# シーン 5秒\n* 不正。\n")

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
        self.assertEqual(plan.metadata["response_protocol"], "clmv-line-v2")
        song_input = json.loads(backend.calls[0]["messages"][1]["content"])
        self.assertNotIn("planning_brief", song_input)
        self.assertEqual(
            song_input["hard_requirements"]["subjects"],
            ["CLMPSUB1Xとして描く中性的な人物。"],
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
        groups = planning._lyric_groups(timeline.scenes[1], protector)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["lyrics"], ["闇を越えて進め"] * 2)
        self.assertEqual(groups[1]["section"], "[Verse 2]")

    def test_complete_duplicate_scene_is_retried_without_losing_first_scene(self) -> None:
        duplicate_second = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t2", 1
        ).replace("Scene 1の映像意図。", "Scene 2の別の映像意図。")
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
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。")
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
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。")
        duplicate_third = scene_payload(1).replace(
            "SCENE\t1", "SCENE\t3", 1
        ).replace("Scene 1の映像意図。", "Scene 3の映像意図。")
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
        ).replace("Scene 1の映像意図。", "Scene 2の映像意図。")
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

    def test_vocal_guard_warn_preserves_plan_and_strict_retries_scene(self) -> None:
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        brief = brief_parser.parse_planning_brief(BRIEF)
        silent_vocalized = scene_payload(1).replace(
            "CLMPSUB1Xは口を閉じたまま肩を引き、ゆっくり振り返る。",
            "CLMPSUB1Xは空を見上げて力強く歌う。",
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
