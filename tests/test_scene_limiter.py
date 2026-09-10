from __future__ import annotations

import unittest

from .helpers import FakeLLM, PKG, module


limiter = module("node_scene_limiter.node")
errors = module("node_scene_limiter.errors")
llmj2e = module("node_japanese_to_json.compiler.llmj2e")
mdparse = module("node_japanese_to_json.compiler.mdparse")
jsongen = module("node_japanese_to_json.compiler.jsongen")


SOURCE = """# サブジェクト
// サブジェクトの注記
* <Picture 1>を参照する人物。

# 保持分析
* <Subject 1> 完全に保持: 顔と衣装を維持する。

# 共通プロンプト
/* 共通プロンプトの注記 */
* 夜景を維持する。

// シーン 1
# シーン 3秒
// 検出状態: silent。ソース範囲 00:00.000-00:03.000。
## ショット
* <Subject 1>は口を閉じて歩く。
## 音響
* 発声: なし

// シーン 2
// Scene 2固有の注記
# シーン 4秒 継続
// 歌詞: 二番目のSceneに属する歌詞
## ショット
* <Subject 1>は腕を伸ばす。
## 音響
* 発声: なし

// シーン 3
# シーン 5秒 継続
## ショット
* <Subject 1>は振り返る。
## 音響
* 発声: なし
"""


class SceneLimiterTests(unittest.TestCase):
    def test_registration_and_ui_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLSceneLimiter"]
        self.assertIs(cls, limiter.CLSceneLimiter)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLSceneLimiter"],
            "CL Scene Limiter (Reduced Markdown)",
        )
        self.assertEqual(cls.RETURN_NAMES, ("limited_markdown",))
        required = cls.INPUT_TYPES()["required"]
        self.assertEqual(
            list(required),
            ["reduced_markdown", "scene_limit_count", "disable"],
        )
        self.assertTrue(required["reduced_markdown"][1]["forceInput"])
        self.assertEqual(required["scene_limit_count"][1]["default"], 1)
        self.assertEqual(required["scene_limit_count"][1]["max"], 128)
        self.assertEqual(required["disable"][0], "BOOLEAN")
        self.assertFalse(required["disable"][1]["default"])

    def test_keeps_first_two_scenes_and_all_related_comments(self) -> None:
        output = limiter.limit_reduced_markdown_scenes(SOURCE, 2)
        self.assertIn("// サブジェクトの注記", output)
        self.assertIn("/* 共通プロンプトの注記 */", output)
        self.assertIn("// シーン 1", output)
        self.assertIn("// シーン 2", output)
        self.assertIn("// Scene 2固有の注記", output)
        self.assertIn("// 歌詞: 二番目のSceneに属する歌詞", output)
        self.assertNotIn("// シーン 3", output)
        self.assertNotIn("振り返る", output)
        self.assertEqual(output.count("# シーン "), 2)

    def test_limit_at_or_above_scene_count_returns_exact_source(self) -> None:
        self.assertIs(limiter.limit_reduced_markdown_scenes(SOURCE, 3), SOURCE)
        self.assertIs(limiter.limit_reduced_markdown_scenes(SOURCE, 99), SOURCE)

    def test_crlf_and_retained_text_are_not_normalized(self) -> None:
        source = SOURCE.replace("\n", "\r\n")
        output = limiter.limit_reduced_markdown_scenes(source, 1)
        self.assertNotIn("\n", output.replace("\r\n", ""))
        self.assertEqual(output, source[: source.index("// シーン 2")])

    def test_disable_returns_the_exact_original_without_parsing(self) -> None:
        for source in (SOURCE, "", "/* unfinished", "編集中の文字列"):
            with self.subTest(source=source[:20]):
                output = limiter.limit_reduced_markdown_scenes(
                    source,
                    0,
                    disable=True,
                )
                self.assertIs(output, source)

        node_output = limiter.CLSceneLimiter.limit_scenes(
            "未完成",
            1,
            True,
        )
        self.assertEqual(node_output, ("未完成",))

    def test_marker_like_line_inside_block_comment_is_not_a_boundary(self) -> None:
        source = SOURCE.replace(
            "// シーン 2\n",
            "/*\n// シーン 2\n偽の番号コメント\n*/\n// シーン 2\n",
        )
        output = limiter.limit_reduced_markdown_scenes(source, 1)
        self.assertIn("偽の番号コメント", output)
        self.assertEqual(output.count("// シーン 2"), 1)
        self.assertNotIn("# シーン 4秒 継続", output)
        self.assertTrue(output.rstrip().endswith("*/"))

    def test_without_number_comment_does_not_guess_about_prior_comments(self) -> None:
        source = SOURCE.replace("// シーン 2\n", "// 編集者の独立メモ\n")
        output = limiter.limit_reduced_markdown_scenes(source, 1)
        self.assertIn("// 編集者の独立メモ", output)
        self.assertNotIn("# シーン 4秒 継続", output)

    def test_invalid_limit_or_markdown_is_rejected(self) -> None:
        for value in (0, 129, True):
            with self.subTest(value=value), self.assertRaises(
                errors.SceneLimiterError
            ):
                limiter.limit_reduced_markdown_scenes(SOURCE, value)
        with self.assertRaises(errors.SceneLimiterError):
            limiter.limit_reduced_markdown_scenes("/* unclosed", 1)
        with self.assertRaises(errors.SceneLimiterError):
            limiter.limit_reduced_markdown_scenes(
                "# サブジェクト\n* 人物。\n", 1
            )
        with self.assertRaises(errors.SceneLimiterError):
            limiter.limit_reduced_markdown_scenes(SOURCE, 1, disable=1)

    def test_node_returns_single_string_tuple(self) -> None:
        with self.assertLogs("cl_scene_limiter", level="INFO") as captured:
            result = limiter.CLSceneLimiter.limit_scenes(SOURCE, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].count("# シーン "), 1)
        output = "\n".join(captured.output)
        self.assertIn("\x1b[96m", output)
        self.assertIn(
            "[cl_scene_limiter] success: retained the first 1 scene(s)",
            output,
        )

    def test_limited_output_compiles_to_the_same_number_of_plan_scenes(self) -> None:
        limited = limiter.limit_reduced_markdown_scenes(SOURCE, 2)
        canonical = llmj2e.translate_markdown(
            limited,
            FakeLLM(),
            "system",
            max_tokens=128,
        )
        emd = mdparse.parse_markdown(canonical)
        plan = jsongen.validate_final_json(jsongen.generate_json(emd))
        self.assertEqual(len(plan["shots"]), 2)


if __name__ == "__main__":
    unittest.main()
