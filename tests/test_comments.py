from __future__ import annotations

import unittest

from .helpers import FakeLLM, module


comments = module("compiler.comments")
errors = module("compiler.errors")
llmj2e = module("compiler.llmj2e")
mdparse = module("compiler.mdparse")


class CommentScannerTests(unittest.TestCase):
    def test_line_and_block_comments_are_replaced_without_losing_lines(self) -> None:
        source = (
            "  // line comment\n"
            "# シーン\n"
            "/* first block line\n"
            "\n"
            "last block line */\n"
            "## ショット\n"
            "* 前 /* inline block */ 後\n"
        )
        result = comments.strip_c_comments(source)
        self.assertEqual(len(result.text), len(source))
        self.assertEqual(result.text.count("\n"), source.count("\n"))
        self.assertNotIn("line comment", result.text)
        self.assertNotIn("first block line", result.text)
        self.assertNotIn("inline block", result.text)
        self.assertIn("* 前", result.text)
        self.assertIn("後", result.text)
        self.assertTrue({1, 3, 4, 5}.issubset(result.comment_only_lines))
        self.assertNotIn(7, result.comment_only_lines)

    def test_comment_tokens_inside_dialogue_and_url_slashes_are_literal(self) -> None:
        source = (
            "* 「// literal /* Japanese */」と発声する。\n"
            "* <d>[Japanese]// literal /* direct */</d>\n"
            "* URLはhttps://example.com/pathである。\n"
        )
        result = comments.strip_c_comments(source)
        self.assertEqual(result.text, source)
        self.assertEqual(result.comment_only_lines, frozenset())

    def test_invalid_block_comment_syntax_is_rejected(self) -> None:
        invalid = (
            "/* outer /* nested */",
            "/* unclosed",
            "unmatched */",
            "<!-- unsupported -->",
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(
                errors.CommentSyntaxError
            ):
                comments.strip_c_comments(source)

    def test_comments_are_transparent_to_japanese_lexer_blocks(self) -> None:
        source = """# サブジェクト
// このコメントは翻訳しない。
* 人物。/* <Subject 99>も無視する。 */

/*
# シーン 60秒
* 偽の本文。
*/
# シーン 5秒 /* duration note */
## ショット
// ショット内コメント。
* <Subject 1>が「//台詞内」と言う。"""
        document = llmj2e.lex_japanese_markdown(source)
        self.assertEqual(document.directive_count, 3)
        self.assertEqual(document.bullet_count, 2)
        self.assertEqual(len(document.records), 2)
        protected_values = {
            value
            for record in document.records
            if record.payload is not None
            for value in record.payload.replacements.values()
        }
        self.assertIn("<d>[Japanese]//台詞内</d>", protected_values)
        self.assertNotIn("<Subject 99>", protected_values)

    def test_comments_never_reach_the_translation_request(self) -> None:
        llm = FakeLLM()
        canonical = llmj2e.translate_markdown(
            """/* 秘密のブロックメモ */
# シーン 5秒
## ショット
// 秘密の行メモ
* 動作する。/* 秘密の行内メモ */""",
            llm,
            "system",
            max_tokens=128,
        )
        request = llm.calls[0]["messages"][-1]["content"]
        self.assertNotIn("秘密", request)
        self.assertNotIn("メモ", request)
        self.assertIn("# Scene 5sec", canonical)

    def test_invalid_comment_fails_before_inference(self) -> None:
        llm = FakeLLM()
        with self.assertRaises(errors.CommentSyntaxError):
            llmj2e.translate_markdown(
                "# シーン\n## ショット\n* 動作。/* unclosed",
                llm,
                "system",
                max_tokens=128,
            )
        self.assertEqual(llm.calls, [])

    def test_comments_are_transparent_to_canonical_parser_blocks(self) -> None:
        canonical = """# Subjects
// ignored
* one.
/* ignored between blocks */
# Scene 5sec /* duration note */
## Shot
// ignored inside the shot
* <Subject 1> acts /* implementation note */ silently."""
        emd = mdparse.parse_markdown(canonical)
        self.assertEqual(emd.subjects, ["one."])
        line = emd.scenes[0].shots[0].lines[0]
        self.assertIn("<Subject 1> acts", line)
        self.assertIn("silently.", line)
        self.assertNotIn("implementation note", line)

    def test_double_slash_after_source_text_is_not_a_comment(self) -> None:
        source = "* URL https://example.com // trailing text"
        result = comments.strip_c_comments(source)
        self.assertEqual(result.text, source)


if __name__ == "__main__":
    unittest.main()
