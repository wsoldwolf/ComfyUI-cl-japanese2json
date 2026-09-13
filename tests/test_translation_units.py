from __future__ import annotations

import unittest

from .helpers import FakeLLM, default_stream_translation, module, request_records


llmj2e = module("node_japanese_to_json.compiler.llmj2e")
units = module("node_japanese_to_json.compiler.translation_units")
errors = module("node_japanese_to_json.compiler.errors")


class TranslationUnitsTests(unittest.TestCase):
    def test_real_polarity_regressions_are_rejected(self):
        pairs = [
            ("眉毛を細い線にせず、丸い形を維持する。",
             "Keep the eyebrows as thin lines and maintain their round shape."),
            ("発声区間中に口を閉じたまま固定しない。",
             "Keep the mouth closed during speech intervals."),
            ("カメラを固定せず、側面へ回り込む。",
             "Keep the camera fixed and move around to the side."),
        ]
        for section in ("# サブジェクト", "# 保持分析", "# 共通プロンプト"):
            for source, translated in pairs:
                with self.subTest(section=section, source=source):
                    prefix = "<Subject 1> 完全に保持: " if section == "# 保持分析" else ""
                    record = llmj2e.lex_japanese_markdown(
                        f"{section}\n* {prefix}{source}\n# シーン\n## ショット\n* 動作。"
                    ).records[0]
                    with self.assertRaisesRegex(errors.TranslationError, "lost explicit negation"):
                        llmj2e._validate_translation_text(record, translated)

    def test_correct_negation_and_positive_following_clause(self):
        record = llmj2e.lex_japanese_markdown(
            "# シーン\n## ショット\n* カメラを固定せず、側面へ回り込む。"
        ).records[0]
        for value in (
            "Do not lock the camera; move around to the side.",
            "Move around to the side without locking the camera.",
            "Avoid locking the camera; move around to the side.",
            "The camera doesn’t remain fixed; it moves around to the side.",
        ):
            self.assertEqual(llmj2e._validate_translation_text(record, value), value)

    def test_obligation_double_negation_and_adjectives_are_not_prohibitions(self):
        for value in ("動作を維持しなければならない。", "動作を維持しないといけない。",
                      "変更しないわけではない。", "光が少ない。", "危ない道。"):
            with self.subTest(value=value):
                self.assertFalse(units.needs_explicit_negation(value))
        self.assertTrue(units.needs_explicit_negation("変更しないといけないが、複製しない。"))

    def test_protected_speech_does_not_affect_boundaries_or_negation(self):
        source = '# サブジェクト\n* <Picture 1>の人物。<d>[English]Do not move. Never run!</d>という文字、2.5cmの装飾。\n# シーン\n## ショット\n* 動作。'
        records = llmj2e.lex_japanese_markdown(source).records
        parts, groups = llmj2e._sentence_records(records[:1])
        self.assertEqual(len(parts), 2)
        self.assertIn("2.5", parts[1].payload.text)
        self.assertFalse(units.needs_explicit_negation(parts[0].payload.text))
        self.assertEqual(set(parts[0].payload.tokens) | set(parts[1].payload.tokens),
                         set(records[0].payload.tokens))
        self.assertEqual(groups[records[0].record_id], parts)
        self.assertTrue(set(parts[0].payload.tokens).isdisjoint(parts[1].payload.tokens))

    def test_common_and_shot_keep_local_positive_negative_context(self):
        source = '# 共通プロンプト\n* 足を使って歩く。滑走で移動させない。\n# シーン\n## ショット\n* <Subject 1>が前へ歩く。最後に<Subject 1>が止まる。'
        records = llmj2e.lex_japanese_markdown(source).records
        parts, _ = llmj2e._sentence_records(records)
        self.assertEqual([part.payload.text for part in parts],
                         [record.payload.text for record in records])

    def test_only_bad_sentence_retried_and_original_subject_bullet_rebuilt(self):
        source = '# サブジェクト\n* <Picture 1>の人物。眉毛を細い線にせず、丸い形を維持する。\n# シーン\n## ショット\n* <Subject 1>が歩く。'

        def response(kwargs, repair=False):
            def transform(record):
                tokens = " ".join(record["protected_placeholders"])
                if "せず" in record["text"]:
                    return ("Do not make the eyebrows thin lines; keep their round shape."
                            if repair else "Keep the eyebrows as thin lines and round shapes.")
                return f"A character from {tokens}." if record["section"] == "Subjects" else f"{tokens} walks."
            return default_stream_translation(kwargs["messages"], transform)

        llm = FakeLLM([response, response, lambda kwargs: response(kwargs, True), response])
        output = llmj2e.translate_markdown(source, llm, "system", max_tokens=512, retry_max=1)
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(len(request_records(llm.calls[2]["messages"])), 1)
        self.assertIn("lost explicit negation", llm.calls[2]["messages"][-1]["content"])
        self.assertIn("* A character from <Picture 1>. Do not make the eyebrows thin lines; keep their round shape.", output)
        self.assertEqual(output.count("* "), 2)

    def test_protected_negative_dialogue_cannot_satisfy_prose_guard(self):
        record = llmj2e.lex_japanese_markdown(
            '# シーン\n## ショット\n* <Subject 1>が<d>[English]Do not move.</d>と言い、身体を固定しない。'
        ).records[0]
        text = " ".join(record.payload.tokens) + " keeps the body fixed."
        with self.assertRaisesRegex(errors.TranslationError, "lost explicit negation"):
            llmj2e._validate_translation_text(record, text)

    def test_truncated_response_salvage_cannot_bypass_negation_guard(self):
        source = '# シーン\n## ショット\n* カメラを固定しない。'
        def response(kwargs):
            content = default_stream_translation(kwargs["messages"], lambda _: "Keep the camera fixed.")
            return {"choices": [{"message": {"content": content}, "finish_reason": "length"}]}
        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(source, FakeLLM([response]), "system", max_tokens=128, retry_max=0)


if __name__ == "__main__":
    unittest.main()
