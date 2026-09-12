from __future__ import annotations

import unittest

from .helpers import FakeLLM, PKG, module


merger = module("node_prompt_merger.merger")
node_mod = module("node_prompt_merger.node")
errors = module("node_prompt_merger.errors")
llmj2e = module("node_japanese_to_json.compiler.llmj2e")
mdparse = module("node_japanese_to_json.compiler.mdparse")
jsongen = module("node_japanese_to_json.compiler.jsongen")


ORIGINAL = """# サブジェクト
// 元のSubject 1
* <Picture 1>を外観参照として使用する狐巫女。
* <Picture 2>を外観参照として使用する旅人。

# 保持分析
* <Subject 1> 完全に保持: 顔と衣装を維持する。

# 共通プロンプト
* 画風はリアルな3DCGとする。
* 夜間の神社を舞台とする。
"""

ADDITION = """# サブジェクト
// Subject 2だけへの追加
* <Subject 2>: 青い外套と銀色の留め具を持つ。

# 保持分析
* <Subject 1> 完全に保持: 狐耳と一本の尻尾を維持する。

# 共通プロンプト
* 手描き水彩画として描く。
"""


class PromptMergerTests(unittest.TestCase):
    def test_registration_and_ui_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLPromptMerger"]
        self.assertIs(cls, node_mod.CLPromptMerger)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLPromptMerger"],
            "CL Prompt Merger (Reduced Markdown)",
        )
        self.assertEqual(cls.RETURN_NAMES, ("merged_markdown",))
        required = cls.INPUT_TYPES()["required"]
        self.assertEqual(
            list(required),
            ["original_markdown", "merge_markdown", "common_omit_rules"],
        )
        self.assertTrue(required["original_markdown"][1]["forceInput"])
        self.assertTrue(required["merge_markdown"][1]["forceInput"])
        self.assertEqual(required["common_omit_rules"][1]["default"], "")
        self.assertTrue(required["common_omit_rules"][1]["multiline"])

    def test_merges_subject_retention_and_prepends_common(self) -> None:
        output = merger.merge_reduced_markdown(ORIGINAL, ADDITION)
        self.assertIn(
            "* <Picture 2>を外観参照として使用する旅人。 青い外套と銀色の留め具を持つ。",
            output,
        )
        self.assertIn(
            "* <Subject 1> 完全に保持: 顔と衣装を維持する。 狐耳と一本の尻尾を維持する。",
            output,
        )
        self.assertLess(
            output.index("* 手描き水彩画として描く。"),
            output.index("* 画風はリアルな3DCGとする。"),
        )
        self.assertIn("// 元のSubject 1", output)
        self.assertIn("// Subject 2だけへの追加", output)
        self.assertEqual(output.count("# サブジェクト"), 1)
        self.assertEqual(output.count("# 保持分析"), 1)
        self.assertEqual(output.count("# 共通プロンプト"), 1)

    def test_empty_merge_returns_original_object_exactly_after_validation(self) -> None:
        source = ORIGINAL.replace("\n", "\r\n")
        output = merger.merge_reduced_markdown(source, "")
        self.assertIs(output, source)
        comments_only = "// no semantic merge\n/* still empty */\n"
        self.assertIs(merger.merge_reduced_markdown(source, comments_only), source)

    def test_empty_merge_does_not_bypass_original_validation(self) -> None:
        for source in (
            "# シーン 5秒\n",
            "# 共通プロンプト\n",
            "自由本文\n",
            "/* unclosed",
        ):
            with self.subTest(source=source), self.assertRaises(
                errors.PromptMergerError
            ):
                merger.merge_reduced_markdown(source, "")

    def test_sections_may_be_absent_and_both_inputs_may_be_empty(self) -> None:
        original = "# 共通プロンプト\n* 元の照明。\n"
        addition = "# 共通プロンプト\n* 追加の世界観。\n"
        output = merger.merge_reduced_markdown(original, addition)
        self.assertNotIn("# サブジェクト", output)
        self.assertNotIn("# 保持分析", output)
        self.assertLess(output.index("追加の世界観"), output.index("元の照明"))
        self.assertEqual(merger.merge_reduced_markdown("", ""), "")

    def test_subject_target_uses_position_or_one_explicit_tag(self) -> None:
        positional = """# サブジェクト
* 金色の瞳を持つ。
* 青い外套を持つ。
"""
        output = merger.merge_reduced_markdown(ORIGINAL, positional)
        self.assertIn("狐巫女。 金色の瞳を持つ。", output)
        self.assertIn("旅人。 青い外套を持つ。", output)

        sparse = "# サブジェクト\n* <Subject 2>: 赤い帽子を持つ。\n"
        output = merger.merge_reduced_markdown(ORIGINAL, sparse)
        self.assertNotIn("<Subject 2>:", output)
        self.assertIn("旅人。 赤い帽子を持つ。", output)

    def test_subject_numbering_gaps_and_ambiguous_tags_are_rejected(self) -> None:
        with self.assertRaisesRegex(errors.PromptMergerError, "numbering gap"):
            merger.merge_reduced_markdown(
                "",
                "# サブジェクト\n* <Subject 2>: 旅人。\n",
            )
        with self.assertRaisesRegex(errors.PromptMergerError, "multiple Subject"):
            merger.merge_reduced_markdown(
                ORIGINAL,
                "# サブジェクト\n* <Subject 1>と<Subject 2>を混同しない。\n",
            )
        with self.assertRaises(errors.PromptMergerError):
            merger.merge_reduced_markdown(
                "",
                "# サブジェクト\n" + "".join(f"* 人物{i}。\n" for i in range(5)),
            )

    def test_conflicting_retention_relationships_are_rejected(self) -> None:
        conflicting = """# 保持分析
* <Subject 1> 部分的に保持: 衣装を維持する。
"""
        with self.assertRaisesRegex(
            errors.PromptMergerError,
            "Conflicting retention relationships for Subject 1",
        ):
            merger.merge_reduced_markdown(ORIGINAL, conflicting)

        original_transfer = """# 保持分析
* <Subject 1> 属性転送 -> <Subject 2>: 光を転送する。
"""
        changed_target = """# 保持分析
* <Subject 1> 属性転送 -> <Subject 3>: 色を転送する。
"""
        with self.assertRaises(errors.PromptMergerError):
            merger.merge_reduced_markdown(original_transfer, changed_target)

    def test_retention_only_fragment_is_allowed_but_defined_subjects_are_checked(self) -> None:
        fragment = """# 保持分析
* <Subject 2> 完全に保持: 衣装を維持する。
"""
        self.assertIn(
            "<Subject 2>",
            merger.merge_reduced_markdown("", fragment),
        )
        with self.assertRaisesRegex(errors.PromptMergerError, "undefined Subject 2"):
            merger.merge_reduced_markdown(
                "# サブジェクト\n* 人物。\n",
                fragment,
            )

    def test_common_omit_rules_remove_only_original_common_lines(self) -> None:
        addition = """# 共通プロンプト
* 画風は手描きアニメとする。
* STYLE is intentionally specified here.
"""
        outcome = merger.merge_reduced_markdown_detailed(
            ORIGINAL,
            addition,
            "画風|ｓｔｙｌｅ",
        )
        self.assertIn("画風は手描きアニメ", outcome.text)
        self.assertIn("STYLE is intentionally", outcome.text)
        self.assertNotIn("リアルな3DCG", outcome.text)
        self.assertIn("夜間の神社", outcome.text)
        self.assertEqual(outcome.omitted_original_common_count, 1)

    def test_omit_rules_reject_empty_pipe_fields_and_preserve_duplicates(self) -> None:
        with self.assertRaisesRegex(errors.PromptMergerError, r"empty \| field"):
            merger.parse_common_omit_rules("画風||作画")
        common = "# 共通プロンプト\n* 同じ行。\n"
        output = merger.merge_reduced_markdown(common, common)
        self.assertEqual(output.count("* 同じ行。"), 2)

    def test_unknown_duplicate_or_out_of_order_directives_are_rejected(self) -> None:
        cases = (
            "# シーン 5秒\n* 動作。\n",
            "# 共通プロンプト\n* A。\n# 保持分析\n* <Subject 1> 完全に保持: B。\n",
            "# 共通プロンプト\n* A。\n# 共通プロンプト\n* B。\n",
            "# 共通プロンプト\n本文。\n",
        )
        for content in cases:
            with self.subTest(content=content), self.assertRaises(
                errors.PromptMergerError
            ):
                merger.merge_reduced_markdown("", content)

    def test_common_direct_speech_is_rejected_before_merge(self) -> None:
        with self.assertRaisesRegex(
            errors.PromptMergerError,
            r"# 共通プロンプト cannot contain direct speech at line 2",
        ):
            merger.merge_reduced_markdown(
                "# 共通プロンプト\n* 額に「天満宮」と表示する。\n",
                "",
            )

    def test_directive_text_inside_comments_is_not_parsed(self) -> None:
        original = """// # シーン 5秒
/*
# 音響
*/
# 共通プロンプト
* URL https://example.com/path を維持する。
"""
        addition = "# 共通プロンプト\n* 夜間にする。\n"
        output = merger.merge_reduced_markdown(original, addition)
        self.assertIn("// # シーン 5秒", output)
        self.assertIn("# 音響", output)
        self.assertIn("https://example.com/path", output)

    def test_crlf_is_used_for_reconstructed_output(self) -> None:
        original = ORIGINAL.replace("\n", "\r\n")
        output = merger.merge_reduced_markdown(original, ADDITION)
        self.assertNotIn("\n", output.replace("\r\n", ""))

    def test_node_logs_cyan_success_and_returns_one_string(self) -> None:
        with self.assertLogs("cl_prompt_merger", level="INFO") as captured:
            result = node_mod.CLPromptMerger.merge_prompts(
                ORIGINAL,
                ADDITION,
                "画風",
            )
        self.assertEqual(len(result), 1)
        logs = "\n".join(captured.output)
        self.assertIn("\x1b[96m", logs)
        self.assertIn("[cl_prompt_merger] success: merged 2 subject(s)", logs)
        self.assertIn("omitted 1 original common line(s)", logs)

    def test_merged_header_compiles_when_followed_by_valid_scene(self) -> None:
        merged = merger.merge_reduced_markdown(ORIGINAL, ADDITION, "画風")
        source = merged + """\n# シーン 5秒
## ショット
* <Subject 1>は石畳を歩く。
## 音響
* 発声: なし
"""
        canonical = llmj2e.translate_markdown(
            source,
            FakeLLM(),
            "system",
            max_tokens=256,
        )
        emd = mdparse.parse_markdown(canonical)
        plan = jsongen.validate_final_json(jsongen.generate_json(emd))
        self.assertEqual(len(plan["shots"]), 1)
        subject_block = plan["shots"][0]["prompt"][0]
        self.assertIn("<Subject 1>", subject_block)
        self.assertNotIn("<Subject 2>", subject_block)


if __name__ == "__main__":
    unittest.main()
