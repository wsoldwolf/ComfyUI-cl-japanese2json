from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import FakeBackend, PKG, module


engine = module("node_prompt_enhancer.engine")
conflicts = module("node_prompt_enhancer.conflicts")
errors = module("node_prompt_enhancer.errors")
markdown = module("node_prompt_enhancer.markdown")
node_mod = module("node_prompt_enhancer.node")
profiles = module("node_prompt_enhancer.profiles")
prompt_loader = module("node_prompt_enhancer.prompt_loader")
protocol = module("node_prompt_enhancer.protocol")


BASE = """# サブジェクト
* 成人の狐巫女を<Subject 1>とする。

# 保持分析
* <Subject 1> 完全に保持: 顔と衣装を維持する。

# 共通プロンプト
// automatic style
* 画風は古い3DCGとする。
* 舞台は昼の都市とする。
* <Subject 1>は常に身体を動かす。
"""

USER = """# サブジェクト
* <Subject 1>: 黄色い虹彩と狐耳を持つ。

# 保持分析
* <Subject 1> 完全に保持: 黄色い虹彩を維持する。

# 共通プロンプト
* 時間帯は深夜とする。
"""


def node_arguments(**overrides):
    values = {
        "source_markdown": BASE,
        "model_name": "model.gguf",
        "chat_format": "auto",
        "style_profile": "passthrough",
        "background_detail": "passthrough",
        "motion_profile": "passthrough",
        "camera_profile": "passthrough",
        "max_tokens": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
        "gpu_layers": -1,
        "n_batch": 256,
        "n_ctx": 0,
        "flash_attn": True,
        "kv_cache_type": "q8_0",
        "op_offload": True,
        "keep_model_loaded": False,
        "seed": 1,
        "retry_max": 2,
        "user_prompt": USER,
        "additional_instruction": "",
        "model_name_override": "",
        "style_profile_override": "",
        "background_detail_override": "",
        "motion_profile_override": "",
        "camera_profile_override": "",
        "save_debug_output": False,
        "semantic_guard": False,  # Legacy transport fixtures; semantic cases have independent verdicts.
    }
    values.update(overrides)
    return values


def response(*, background_lines=(), classes=None):
    classes = classes or {
        "C001": "style",
        "C002": "background",
        "C003": "keep",
    }
    lines = ["ENHANCEMENT_V1"]
    lines.extend(f"SOURCE\t{key}\t{value}" for key, value in classes.items())
    lines.extend(f"BACKGROUND\t{value}" for value in background_lines)
    lines.append("END_ENHANCEMENT")
    return "\n".join(lines)


class PromptEnhancerProfileTests(unittest.TestCase):
    def test_profiles_are_discovered_in_ui_order(self):
        self.assertEqual(
            profiles.discover_style_profile_ids(),
            [
                "passthrough",
                "anime_2020s",
                "anime_2010s",
                "anime_2000s",
                "anime_1990s",
                "anime_1980s",
                "cinematic_live_action",
                "photographic_live_action",
                "rough_sketch",
                "aggressive_sketch",
                "watercolor",
                "illustration",
                "masterpiece",
            ],
        )
        self.assertEqual(
            profiles.discover_background_profile_ids(),
            ["passthrough", "reduce", "low", "medium", "high", "ultra"],
        )
        self.assertEqual(
            profiles.discover_motion_profile_ids(),
            [
                "passthrough",
                "subtle",
                "natural",
                "dynamic",
                "music_video",
                "mv_anime_emotional",
                "limited_anime",
            ],
        )
        self.assertEqual(
            profiles.discover_camera_profile_ids(),
            [
                "passthrough",
                "stable",
                "cinematic",
                "dynamic",
                "orbit_subject",
                "mv_anime_emotional",
                "music_video",
            ],
        )
        for value in profiles.discover_style_profile_ids():
            profiles.load_style_profile(value)
        for value in profiles.discover_background_profile_ids():
            profiles.load_background_profile(value)
        for value in profiles.discover_motion_profile_ids():
            profiles.load_motion_profile(value)
        for value in profiles.discover_camera_profile_ids():
            profiles.load_camera_profile(value)

    def test_unknown_profiles_are_rejected(self):
        with self.assertRaises(errors.EnhancerProfileError):
            profiles.load_style_profile("unknown")
        with self.assertRaises(errors.EnhancerProfileError):
            profiles.load_background_profile("unknown")
        with self.assertRaises(errors.EnhancerProfileError):
            profiles.load_motion_profile("unknown")
        with self.assertRaises(errors.EnhancerProfileError):
            profiles.load_camera_profile("unknown")

    def test_motion_and_camera_profiles_are_external_directives(self):
        motion = "\n".join(profiles.load_motion_profile("music_video").directives)
        camera = "\n".join(profiles.load_camera_profile("orbit_subject").directives)
        self.assertIn("ソースボーカル", motion)
        self.assertIn("足を使わない滑走", motion)
        self.assertIn("広い半円状のアーク移動", camera)
        self.assertIn("開始視点と終了視点", camera)

    def test_mv_anime_emotional_is_split_without_project_specific_nouns(self):
        motion = "\n".join(
            profiles.load_motion_profile("mv_anime_emotional").directives
        )
        camera = "\n".join(
            profiles.load_camera_profile("mv_anime_emotional").directives
        )
        self.assertIn("予備動作、主動作、反動", motion)
        self.assertIn("身体付属物", motion)
        self.assertNotIn("カメラは正面", motion)
        self.assertIn("広いアーク移動", camera)
        self.assertIn("強い視差と奥行き", camera)
        self.assertNotIn("鳥居", motion + camera)
        self.assertNotIn("狐耳", motion + camera)

    def test_anime_profiles_constrain_character_to_limited_animation(self):
        for profile_id in (
            "anime_2020s",
            "anime_2010s",
            "anime_2000s",
            "anime_1990s",
            "anime_1980s",
        ):
            directives = "\n".join(
                profiles.load_style_profile(profile_id).directives
            )
            with self.subTest(profile_id=profile_id):
                self.assertIn("キャラクターの動き", directives)
                self.assertIn("リミテッドアニメーション", directives)
                self.assertIn("二コマ打ち又は三コマ打ち", directives)
                self.assertIn("リップシンク", directives)
                self.assertIn("保持後は次の動作へ進む", directives)
                self.assertIn("カメラ移動中もこのタイミングを維持", directives)
                self.assertIn("発音に合わせて唇と顎を動かし", directives)
                self.assertIn("口形を音素と休止へ同期", directives)
                self.assertIn("足裏の接地", directives)
                self.assertIn("接地中の足を地面の同じ位置に保ち", directives)

    def test_anime_and_emotional_profiles_use_affirmative_descriptions(self):
        selected = [
            profiles.load_style_profile(f"anime_{decade}s")
            for decade in (2020, 2010, 2000, 1990, 1980)
        ] + [
            profiles.load_motion_profile("mv_anime_emotional"),
            profiles.load_motion_profile("limited_anime"),
            profiles.load_camera_profile("mv_anime_emotional"),
        ]
        for profile in selected:
            with self.subTest(kind=type(profile).__name__, profile=profile.profile_id):
                text = "\n".join(profile.directives)
                self.assertNotRegex(text, r"しない|させない|せず|禁止|止め絵")
                for unwanted in ("3DCG", "Live2D", "モーフィング", "滑走", "浮遊"):
                    self.assertNotIn(unwanted, text)

    def test_shared_anime_directives_match_for_exact_deduplication(self):
        shared = profiles.load_motion_profile("limited_anime").directives
        self.assertEqual(len(shared), 4)
        for decade in (2020, 2010, 2000, 1990, 1980):
            with self.subTest(decade=decade):
                style = profiles.load_style_profile(f"anime_{decade}s")
                self.assertEqual(style.directives[2:], shared)
        motion = profiles.load_motion_profile("mv_anime_emotional")
        self.assertEqual(motion.directives[:4], shared)
        performance = "\n".join(motion.directives[4:])
        self.assertIn("予備動作、主動作、反動", performance)
        self.assertIn("付け根を身体につないだまま", performance)
        self.assertIn("人物の演技を補助する", performance)

    def test_emotional_camera_keeps_orbit_and_scopes_visibility(self):
        text = "\n".join(
            profiles.load_camera_profile("mv_anime_emotional").directives
        )
        for phrase in (
            "顔が見える区間では表情と口元",
            "側面や後方からは姿勢、手足の動き",
            "各Shotでは",
            "主となるカメラ移動を一つ選ぶ",
            "広いアーク移動",
            "開始視点",
            "終了視点",
            "強い視差と奥行き",
            "再登場時の外観を連続",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_profile_edit_invalidates_fingerprint_and_loaded_profile(self):
        manifest = profiles._MOTION_ROOT / "mv_anime_emotional" / "profile.json"
        original = manifest.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "mv_anime_emotional" / "profile.json"
            target.parent.mkdir()
            target.write_text(original, encoding="utf-8")
            with patch.object(profiles, "_MOTION_ROOT", root):
                before = profiles.load_motion_profile("mv_anime_emotional")
                fingerprint = prompt_loader.enhancer_prompts_fingerprint()
                stat = target.stat()
                revised = original.replace("次の動作へ進む", "次の動作へ移る")
                self.assertNotEqual(original, revised)
                self.assertEqual(len(original.encode("utf-8")), len(revised.encode("utf-8")))
                target.write_text(revised, encoding="utf-8")
                os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                after = profiles.load_motion_profile("mv_anime_emotional")
                self.assertNotEqual(before.directives, after.directives)
                self.assertNotEqual(fingerprint, prompt_loader.enhancer_prompts_fingerprint())

    def test_system_prompt_composes_only_selected_profile_policy(self):
        value = prompt_loader.load_enhancer_system_prompt(
            profiles.load_style_profile("anime_1990s"),
            profiles.load_background_profile("high"),
        )
        self.assertIn("SELECTED STYLE PROFILE: anime_1990s", value)
        self.assertIn("SELECTED BACKGROUND PROFILE: high", value)
        self.assertNotIn("aggressive_sketch", value)
        self.assertNotIn("SOURCE\tC001\tkeep", value)
        self.assertIn("authoritative_environment.time_of_day", value)


class PromptEnhancerConflictTests(unittest.TestCase):
    def test_time_authority_requires_one_unambiguous_user_concept(self):
        self.assertEqual(
            conflicts.authoritative_time_of_day(
                ["時刻は夜間であり、月が出ている。"]
            ),
            "night",
        )
        self.assertIsNone(
            conflicts.authoritative_time_of_day(
                ["昼から夜へ時間が移る。"]
            )
        )
        self.assertEqual(
            conflicts.authoritative_time_of_day(
                ["時間帯は深夜とする。朝、昼間及び日光は表示しない。"]
            ),
            "night",
        )
        self.assertTrue(
            conflicts.conflicts_with_time_of_day(
                "太陽が高く、青空が広がる。", "night"
            )
        )
        self.assertFalse(
            conflicts.conflicts_with_time_of_day(
                "冷たい月光が石畳を照らす。", "night"
            )
        )
        self.assertFalse(
            conflicts.conflicts_with_time_of_day(
                "太陽と青空は表示しない。", "night"
            )
        )


class PromptEnhancerProtocolTests(unittest.TestCase):
    def test_valid_protocol_and_extra_background_tabs(self):
        parsed = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nSOURCE\tC001\tkeep\n"
            "BACKGROUND\t深い森\t青い月光\nEND_ENHANCEMENT",
            expected_source_ids=("C001",),
            minimum_background_lines=1,
            maximum_background_lines=1,
        )
        self.assertEqual(parsed.classifications, {"C001": "keep"})
        self.assertEqual(parsed.background_lines, ("深い森、青い月光",))
        self.assertEqual(len(parsed.warnings), 1)

    def test_literal_tab_markers_from_small_model_are_normalized(self):
        parsed = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nSOURCE<TAB>C001<TAB>style\n"
            "BACKGROUND\\t夜の森を描く。\nEND_ENHANCEMENT",
            expected_source_ids=("C001",),
            minimum_background_lines=1,
            maximum_background_lines=1,
        )
        self.assertEqual(parsed.classifications, {"C001": "style"})
        self.assertEqual(parsed.background_lines, ("夜の森を描く。",))
        self.assertEqual(len(parsed.warnings), 2)

    def test_missing_final_marker_is_restored_only_after_full_validation(self):
        parsed = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\n"
            "BACKGROUND\t夜霧が石畳の上を流れる。",
            expected_source_ids=("C001",),
            minimum_background_lines=1,
            maximum_background_lines=1,
        )
        self.assertEqual(parsed.classifications, {"C001": "background"})
        self.assertEqual(
            parsed.background_lines,
            ("夜霧が石畳の上を流れる。",),
        )
        self.assertTrue(any("END_ENHANCEMENT" in item for item in parsed.warnings))

        with self.assertRaisesRegex(
            errors.EnhancerResponseError,
            "SOURCE ids differ",
        ):
            protocol.parse_enhancement_response(
                "ENHANCEMENT_V1\nBACKGROUND\t夜霧が石畳の上を流れる。",
                expected_source_ids=("C001",),
                minimum_background_lines=1,
                maximum_background_lines=1,
            )

    def test_non_background_record_is_discarded_when_minimum_remains(self):
        parsed = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\n"
            "BACKGROUND\t夜霧が石畳の上を流れる。\n"
            "BACKGROUND\t鳥居の前で人物がポーズをとっている。\n"
            "BACKGROUND\t冷たい月光が濡れた石畳を照らす。\n"
            "END_ENHANCEMENT",
            expected_source_ids=("C001",),
            minimum_background_lines=2,
            maximum_background_lines=3,
        )
        self.assertEqual(
            parsed.background_lines,
            (
                "夜霧が石畳の上を流れる。",
                "冷たい月光が濡れた石畳を照らす。",
            ),
        )
        self.assertTrue(any("discarded BACKGROUND" in item for item in parsed.warnings))

    def test_safe_background_subset_is_accepted_below_minimum_after_discard(self):
        parsed = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\n"
            "BACKGROUND\t夜霧が石畳の上を流れる。\n"
            "BACKGROUND\t鳥居の前で人物がポーズをとっている。\n"
            "END_ENHANCEMENT",
            expected_source_ids=("C001",),
            minimum_background_lines=2,
            maximum_background_lines=4,
        )
        self.assertEqual(parsed.background_lines, ("夜霧が石畳の上を流れる。",))
        self.assertTrue(any("below the requested minimum" in item
                            for item in parsed.warnings))

    def test_all_non_background_records_still_fail_closed(self):
        with self.assertRaisesRegex(errors.EnhancerResponseError, "environment only"):
            protocol.parse_enhancement_response(
                "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\n"
                "BACKGROUND\t鳥居の前で人物がポーズをとっている。\n"
                "BACKGROUND\t正面からの視点で被写体を捉える。\n"
                "END_ENHANCEMENT",
                expected_source_ids=("C001",),
                minimum_background_lines=2,
                maximum_background_lines=4,
            )

    def test_protocol_rejects_missing_ids_unknown_classes_and_references(self):
        cases = (
            "ENHANCEMENT_V1\nEND_ENHANCEMENT",
            "ENHANCEMENT_V1\nSOURCE\tC001\tmotion\nEND_ENHANCEMENT",
            "ENHANCEMENT_V1\nSOURCE\tC001\tkeep\n"
            "BACKGROUND\t<Subject 1>を照らす\nEND_ENHANCEMENT",
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(errors.EnhancerResponseError):
                protocol.parse_enhancement_response(
                    raw,
                    expected_source_ids=("C001",),
                    minimum_background_lines=1 if "BACKGROUND" in raw else 0,
                    maximum_background_lines=1,
                )

    def test_protocol_rejects_performer_pose_and_framing_as_background(self):
        invalid_lines = (
            "鳥居の前で人物がポーズをとっている。",
            "構図は全身ショットで中央に配置する。",
            "正面からの視点で被写体を捉える。",
        )
        for value in invalid_lines:
            raw = (
                "ENHANCEMENT_V1\n"
                "SOURCE\tC001\tbackground\n"
                f"BACKGROUND\t{value}\n"
                "END_ENHANCEMENT"
            )
            with self.subTest(value=value), self.assertRaisesRegex(
                errors.EnhancerResponseError,
                "environment only",
            ):
                protocol.parse_enhancement_response(
                    raw,
                    expected_source_ids=("C001",),
                    minimum_background_lines=1,
                    maximum_background_lines=1,
                )


class PromptEnhancerMarkdownTests(unittest.TestCase):
    def test_common_rewrite_is_narrow_and_preserves_comments(self):
        inspected = markdown.inspect_prompt(BASE, "source")
        result = markdown.rewrite_common(
            BASE,
            inspected,
            removed_source_ids={"C001"},
            prepended_bullets=("画風は水彩画とする。",),
        )
        self.assertIn("// automatic style", result)
        self.assertNotIn("古い3DCG", result)
        self.assertIn("画風は水彩画", result)
        self.assertIn("<Subject 1>は常に身体を動かす", result)

    def test_scene_directives_are_rejected(self):
        with self.assertRaisesRegex(errors.PromptEnhancerError, "global reduced Markdown"):
            markdown.inspect_prompt("# シーン 5秒\n## ショット\n* 動く。\n", "source")


class PromptEnhancerEngineTests(unittest.TestCase):
    def test_complete_passthrough_merges_user_without_loading_llm(self):
        result = engine.enhance_reduced_markdown(
            BASE,
            USER,
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("passthrough"),
            backend=None,
            max_tokens=256,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=2,
        )
        self.assertEqual(result.request_count, 0)
        self.assertIn("黄色い虹彩と狐耳を持つ。", result.markdown)
        self.assertIn("黄色い虹彩を維持する。", result.markdown)
        self.assertIn("時間帯は深夜とする。", result.markdown)
        self.assertNotIn("舞台は昼の都市", result.markdown)
        self.assertEqual(
            result.report["removed_conflicting_source_ids"], ["C002"]
        )
        self.assertEqual(result.report["user_lines_modified"], 0)
        self.assertEqual(result.report["user_lines_preserved"], 3)

    def test_style_and_background_replace_only_automatic_common(self):
        backend = FakeBackend(
            [
                response(
                    background_lines=(
                        "前景に濡れた石畳を配置する。",
                        "遠景を冷たい月光で照らす。",
                    )
                )
            ]
        )
        result = engine.enhance_reduced_markdown(
            BASE,
            USER,
            style=profiles.load_style_profile("anime_2020s"),
            background=profiles.load_background_profile("medium"),
            backend=backend,
            max_tokens=256,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=10,
            retry_max=2,
        )
        self.assertEqual(result.request_count, 1)
        self.assertNotIn("古い3DCG", result.markdown)
        self.assertNotIn("舞台は昼の都市", result.markdown)
        self.assertIn("2020年代の高品質な手描き2Dアニメ", result.markdown)
        self.assertIn("前景に濡れた石畳", result.markdown)
        self.assertIn("<Subject 1>は常に身体を動かす", result.markdown)
        self.assertIn("時間帯は深夜とする", result.markdown)
        self.assertEqual(result.report["removed_source_common_ids"], ["C001", "C002"])
        request_payload = json.loads(backend.calls[0]["messages"][-1]["content"])
        self.assertIn("immutable_user_prompt", request_payload)
        self.assertNotIn("<Subject 1>", backend.calls[0]["messages"][-1]["content"])
        self.assertIn("CLPESUB1X", backend.calls[0]["messages"][-1]["content"])

    def test_reduce_removes_automatic_background_but_keeps_user_background(self):
        backend = FakeBackend([response(background_lines=("夜の神社を簡潔に描く。",))])
        result = engine.enhance_reduced_markdown(
            BASE,
            USER,
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("reduce"),
            backend=backend,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )
        self.assertNotIn("舞台は昼の都市", result.markdown)
        self.assertIn("夜の神社を簡潔に描く", result.markdown)
        self.assertIn("時間帯は深夜とする", result.markdown)

    def test_user_time_authority_filters_model_and_source_conflicts(self):
        backend = FakeBackend(
            [
                response(
                    classes={
                        "C001": "keep",
                        "C002": "keep",
                        "C003": "keep",
                    },
                    background_lines=(
                        "太陽が高く照らし、青空が広がる。",
                        "明るい日中の木漏れ日が境内へ差し込む。",
                        "冷たい月光が濡れた石畳を浅く照らす。",
                        "前景と遠景の間に薄い夜霧を置く。",
                    ),
                )
            ]
        )
        result = engine.enhance_reduced_markdown(
            BASE,
            USER,
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("high"),
            backend=backend,
            max_tokens=256,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )

        self.assertNotIn("舞台は昼の都市", result.markdown)
        self.assertNotIn("太陽が高く", result.markdown)
        self.assertNotIn("日中の木漏れ日", result.markdown)
        self.assertIn("時間帯は深夜とする", result.markdown)
        self.assertIn("冷たい月光", result.markdown)
        self.assertIn("薄い夜霧", result.markdown)
        self.assertEqual(result.report["authoritative_time_of_day"], "night")
        self.assertEqual(
            result.report["removed_conflicting_source_ids"], ["C002"]
        )
        self.assertEqual(
            result.report["removed_conflicting_background_lines"], 2
        )
        self.assertEqual(result.report["background_lines_added"], 2)
        request_payload = json.loads(backend.calls[0]["messages"][-1]["content"])
        self.assertEqual(
            request_payload["authoritative_environment"]["time_of_day"],
            "night",
        )

    def test_invalid_response_retries_with_error_and_new_seed(self):
        good = response(background_lines=("夜の森を簡潔に描く。",))
        backend = FakeBackend(["bad", good])
        result = engine.enhance_reduced_markdown(
            BASE,
            "",
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("reduce"),
            backend=backend,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=8,
            retry_max=1,
        )
        self.assertEqual(result.request_count, 2)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual([call["seed"] for call in backend.calls], [8, 9])
        retry_payload = json.loads(backend.calls[1]["messages"][-1]["content"])
        self.assertIn("previous_validation_error", retry_payload)

    def test_retry_limit_is_finite(self):
        backend = FakeBackend(["bad one", "bad two"])
        with self.assertRaisesRegex(errors.PromptEnhancerError, "after 2 attempt"):
            engine.enhance_reduced_markdown(
                BASE,
                "",
                style=profiles.load_style_profile("passthrough"),
                background=profiles.load_background_profile("reduce"),
                backend=backend,
                max_tokens=128,
                temperature=0.1,
                top_p=0.9,
                repetition_penalty=1.05,
                seed=1,
                retry_max=1,
            )

    def test_style_without_source_common_needs_no_llm(self):
        result = engine.enhance_reduced_markdown(
            "# サブジェクト\n* 人物を<Subject 1>とする。\n",
            "",
            style=profiles.load_style_profile("watercolor"),
            background=profiles.load_background_profile("passthrough"),
            backend=None,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )
        self.assertEqual(result.request_count, 0)
        self.assertIn("# 共通プロンプト", result.markdown)
        self.assertIn("透明水彩", result.markdown)

    def test_motion_and_camera_profiles_need_no_llm(self):
        result = engine.enhance_reduced_markdown(
            BASE,
            USER,
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("passthrough"),
            motion=profiles.load_motion_profile("dynamic"),
            camera=profiles.load_camera_profile("orbit_subject"),
            backend=None,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )
        self.assertEqual(result.request_count, 0)
        self.assertIn("明確な予備動作", result.markdown)
        self.assertIn("広い半円状のアーク移動", result.markdown)
        self.assertEqual(result.report["motion_profile"], "dynamic")
        self.assertEqual(result.report["camera_profile"], "orbit_subject")
        self.assertGreater(result.report["motion_lines_added"], 0)
        self.assertGreater(result.report["camera_lines_added"], 0)

    def test_anime_motion_combination_deduplicates_and_preserves_user_negation(self):
        user_line = "<Subject 1>は驚いて見開いた目へ変化させない。"
        user_prompt = f"# 共通プロンプト\n* {user_line}\n"
        for decade in (2020, 2010, 2000, 1990, 1980):
            for motion_id in ("mv_anime_emotional", "limited_anime"):
                with self.subTest(decade=decade, motion=motion_id):
                    result = engine.enhance_reduced_markdown(
                        "# サブジェクト\n* 人物を<Subject 1>とする。\n",
                        user_prompt,
                        style=profiles.load_style_profile(f"anime_{decade}s"),
                        background=profiles.load_background_profile("passthrough"),
                        motion=profiles.load_motion_profile(motion_id),
                        camera=profiles.load_camera_profile("mv_anime_emotional"),
                        backend=None,
                        max_tokens=128,
                        temperature=0.1,
                        top_p=0.9,
                        repetition_penalty=1.05,
                        seed=1,
                        retry_max=0,
                    )
                    self.assertEqual(result.request_count, 0)
                    self.assertEqual(result.report["duplicate_common_lines_removed"], 4)
                    for directive in profiles.load_motion_profile("limited_anime").directives:
                        self.assertEqual(result.markdown.count(directive), 1)
                    self.assertIn(user_line, result.markdown)
                    self.assertIn("主となるカメラ移動を一つ選ぶ", result.markdown)

    def test_emotional_motion_alone_keeps_grounding_and_lip_sync_without_llm(self):
        result = engine.enhance_reduced_markdown(
            "# サブジェクト\n* 人物を<Subject 1>とする。\n",
            "",
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("passthrough"),
            motion=profiles.load_motion_profile("mv_anime_emotional"),
            backend=None,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )
        self.assertEqual(result.request_count, 0)
        self.assertIn("リップシンクが指定されたScene", result.markdown)
        self.assertIn("足裏の接地", result.markdown)
        self.assertIn("予備動作、主動作、反動", result.markdown)
        self.assertNotIn("# シーン", result.markdown)

    def test_normalized_exact_common_duplicates_are_removed(self):
        directive = profiles.load_motion_profile("limited_anime").directives[0]
        source = f"# 共通プロンプト\n* {directive}\n"
        result = engine.enhance_reduced_markdown(
            source,
            "",
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("passthrough"),
            motion=profiles.load_motion_profile("limited_anime"),
            camera=profiles.load_camera_profile("passthrough"),
            backend=None,
            max_tokens=128,
            temperature=0.1,
            top_p=0.9,
            repetition_penalty=1.05,
            seed=1,
            retry_max=0,
        )
        self.assertEqual(result.markdown.count(directive), 1)
        self.assertEqual(result.report["duplicate_common_lines_removed"], 1)


class PromptEnhancerNodeTests(unittest.TestCase):
    def test_registration_and_input_contract(self):
        cls = PKG.NODE_CLASS_MAPPINGS["CLPromptEnhancerGGUF"]
        self.assertIs(cls, node_mod.CLPromptEnhancerGGUF)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLPromptEnhancerGGUF"],
            "CL Prompt Enhancer (GGUF)",
        )
        with patch.object(cls, "discover_model_names", return_value=["model.gguf"]):
            inputs = cls.INPUT_TYPES()
        self.assertTrue(inputs["required"]["source_markdown"][1]["forceInput"])
        self.assertEqual(inputs["required"]["style_profile"][1]["default"], "passthrough")
        self.assertEqual(inputs["required"]["background_detail"][1]["default"], "passthrough")
        self.assertFalse(inputs["required"]["keep_model_loaded"][1]["default"])
        self.assertTrue(inputs["optional"]["user_prompt"][1]["forceInput"])
        self.assertTrue(inputs["optional"]["style_profile_override"][1]["forceInput"])
        self.assertEqual(inputs["optional"]["motion_profile"][1]["default"], "passthrough")
        self.assertEqual(inputs["optional"]["camera_profile"][1]["default"], "passthrough")
        self.assertEqual(
            inputs["optional"]["motion_profile_override"][1]["connected_combo_source"],
            "motion_profile",
        )
        self.assertEqual(
            inputs["optional"]["camera_profile_override"][1]["connected_combo_source"],
            "camera_profile",
        )

    def test_node_passthrough_does_not_resolve_or_load_model(self):
        instance = node_mod.CLPromptEnhancerGGUF()
        backend = FakeBackend()
        instance._backend = backend
        with patch.object(node_mod, "resolve_model_name", side_effect=AssertionError):
            with self.assertLogs("cl_prompt_enhancer", level="INFO") as captured:
                output = instance.enhance_prompt(**node_arguments())
        self.assertEqual(len(output), 3)
        self.assertEqual(backend.ensure_calls, [])
        self.assertEqual(backend.calls, [])
        self.assertIn("\x1b[96m", "\n".join(captured.output))
        self.assertEqual(json.loads(output[1])["llm_requests"], 0)

    def test_external_overrides_select_model_style_and_background(self):
        raw = response(background_lines=("夜の森を簡潔に描く。",))
        instance = node_mod.CLPromptEnhancerGGUF()
        backend = FakeBackend([raw])
        instance._backend = backend
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "override.gguf"
            model.write_bytes(b"x")
            with patch.object(node_mod, "resolve_model_name", return_value=model):
                output = instance.enhance_prompt(
                    **node_arguments(
                        model_name_override="/models/override.gguf",
                        style_profile_override="anime_2020s",
                        background_detail_override="reduce",
                        motion_profile_override="dynamic",
                        camera_profile_override="orbit_subject",
                    )
                )
        report = json.loads(output[1])
        self.assertEqual(report["style_profile"], "anime_2020s")
        self.assertEqual(report["background_detail"], "reduce")
        self.assertEqual(report["motion_profile"], "dynamic")
        self.assertEqual(report["camera_profile"], "orbit_subject")
        self.assertEqual(report["model_name"], "/models/override.gguf")
        self.assertEqual(backend.ensure_calls[0]["model_path"], model)
        self.assertEqual(backend.clear_count, 1)


if __name__ == "__main__":
    unittest.main()
