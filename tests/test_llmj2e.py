from __future__ import annotations

import re
import unittest

from .helpers import (
    FakeLLM,
    default_stream_translation,
    default_translation,
    module,
    request_records,
    request_stream,
)


llmj2e = module("compiler.llmj2e")
errors = module("compiler.errors")


SOURCE = """# サブジェクト
* <Picture 1>を参照する人物。

# 共通プロンプト
* 夜の街。

# シーン 5秒
* <Subject 1>が「こんにちは」と言う。
"""


class LLMJ2ETests(unittest.TestCase):
    def test_normal_translation_rebuilds_canonical_markdown(self) -> None:
        llm = FakeLLM()
        result = llmj2e.translate_markdown(SOURCE, llm, "system", max_tokens=64)
        self.assertIn("# Subjects", result)
        self.assertIn("# Common", result)
        self.assertIn("# Scene 5sec", result)
        self.assertIn("<Picture 1>", result)
        self.assertIn("<Subject 1>", result)
        self.assertIn("<d>[Japanese]こんにちは</d>", result)
        self.assertNotIn("夜の街", result)
        self.assertEqual(llm.reset_count, 1)
        call = llm.calls[0]
        self.assertNotIn("response_format", call)
        self.assertRegex(call["stop"][0], r"CLJT\d+ENDX")
        self.assertTrue(call["messages"][-1]["content"].endswith("/no_think"))
        stream = request_stream(call["messages"])
        records = request_records(call["messages"])
        self.assertIsInstance(stream, str)
        self.assertNotIn("/no_think", stream)
        self.assertNotIn('{"translation_stream"', call["messages"][-1]["content"])
        self.assertEqual(len(records), 3)

    def test_stream_replaces_directives_references_and_dialogue(self) -> None:
        document = llmj2e.lex_japanese_markdown(SOURCE)
        stream = llmj2e._build_translation_stream(document.records)
        self.assertNotIn("# サブジェクト", stream.text)
        self.assertNotIn("# 共通プロンプト", stream.text)
        self.assertNotIn("# シーン", stream.text)
        self.assertNotIn("<Picture 1>", stream.text)
        self.assertNotIn("<Subject 1>", stream.text)
        self.assertNotIn("こんにちは", stream.text)
        self.assertRegex(stream.text, r"CLJT\d+D0X")
        self.assertRegex(stream.text, r"CLJT\d+SUB1X")
        self.assertRegex(stream.text, r"CLJT\d+COM2X")
        self.assertRegex(stream.text, r"CLJT\d+SCN3X")
        self.assertNotRegex(stream.text, r"CLJT\d+(?:SUB|COM|SCN)\d+EX")
        replacements = {
            value
            for record in document.records
            for value in record.payload.replacements.values()
        }
        self.assertIn("<Picture 1>", replacements)
        self.assertIn("<Subject 1>", replacements)
        self.assertIn("<d>[Japanese]こんにちは</d>", replacements)

    def test_stream_placeholders_are_unique_and_declared_per_record(self) -> None:
        document = llmj2e.lex_japanese_markdown(
            "# シーン\n* <Subject 1>が動く。\n* <Subject 1>が話す。"
        )
        first, second = document.records
        self.assertTrue(set(first.payload.tokens).isdisjoint(second.payload.tokens))
        stream = llmj2e._build_translation_stream(document.records)
        self.assertIn(first.payload.tokens[0], stream.text)
        self.assertIn(second.payload.tokens[0], stream.text)
        subject_stream = llmj2e._build_translation_stream(
            llmj2e.lex_japanese_markdown("# サブジェクト\n* 人物。").records
        )
        self.assertIn("SUB", subject_stream.text)
        self.assertTrue(all(len(token) <= 12 for token in first.payload.tokens))

    def test_stream_prefix_avoids_source_collision(self) -> None:
        document = llmj2e.lex_japanese_markdown(
            "# シーン\n* CLJT0を画面に表示する。"
        )
        stream = llmj2e._build_translation_stream(document.records)
        self.assertEqual(stream.prefix, "CLJT1")

    def test_text_outside_record_boundaries_is_rejected(self) -> None:
        def add_outside_text(kwargs):
            translated = default_stream_translation(kwargs["messages"])
            return "unexpected text " + translated

        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* 動作。",
                FakeLLM([add_outside_text, add_outside_text]),
                "sys",
                max_tokens=64,
            )

    def test_only_failed_records_are_retried(self) -> None:
        def one_missing(kwargs):
            def transform(record):
                text = default_translation(record)
                if record["id"] == "R000002":
                    for token in record["protected_placeholders"]:
                        text = text.replace(token, "")
                return text

            return default_stream_translation(
                kwargs["messages"], transform=transform
            )

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が話す。\n"
            "* <Subject 3>が止まる。"
        )
        llm = FakeLLM([one_missing])
        output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=4096)
        self.assertEqual(output.count("* "), 3)
        self.assertEqual(len(llm.calls), 2)
        retried = request_records(llm.calls[1]["messages"])
        self.assertEqual([record["id"] for record in retried], ["R000002"])

    def test_structurally_truncated_stream_salvages_completed_segments(self) -> None:
        def truncate_before_third_segment(kwargs):
            records = request_records(kwargs["messages"])
            translated = default_stream_translation(kwargs["messages"])
            return translated[: translated.index(records[2]["marker_token"])]

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が話す。\n"
            "* <Subject 3>が止まる。"
        )
        llm = FakeLLM([truncate_before_third_segment])
        output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=4096)
        self.assertEqual(output.count("* "), 3)
        self.assertEqual(len(llm.calls), 2)
        retried = request_records(llm.calls[1]["messages"])
        self.assertEqual(
            [record["id"] for record in retried],
            ["R000002", "R000003"],
        )

    def test_missing_directive_markers_use_intact_record_markers(self) -> None:
        def omit_directives(kwargs):
            translated = default_stream_translation(kwargs["messages"])
            return re.sub(r"CLJT\d+D\d+X\s*", "", translated)

        llm = FakeLLM([omit_directives])
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            output = llmj2e.translate_markdown(SOURCE, llm, "sys", max_tokens=128)
        self.assertEqual(output.count("* "), 3)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(
            any(
                "reconstructing document structure from 3 intact record"
                in line
                for line in captured.output
            )
        )

    def test_exact_leading_subject_wrappers_restore_missing_sub_markers(self) -> None:
        def replace_subject_markers(kwargs):
            records = request_records(kwargs["messages"])
            translated = default_stream_translation(kwargs["messages"])
            first_common = next(
                record for record in records if record["section"] == "Common"
            )
            suffix_start = translated.index(first_common["marker_token"])
            subject_records = [
                record for record in records if record["section"] == "Subjects"
            ]
            wrappers = [
                f"<Subject {index}> is {default_translation(record)}"
                for index, record in enumerate(subject_records, start=1)
            ]
            return "\n".join([*wrappers, translated[suffix_start:]])

        llm = FakeLLM([replace_subject_markers])
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            output = llmj2e.translate_markdown(SOURCE, llm, "sys", max_tokens=128)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("# Subjects\n* a referenced character", output)
        self.assertNotIn("* <Subject 1> is", output)
        self.assertTrue(
            any(
                "Recovered 1 leading SUB record placeholder" in line
                for line in captured.output
            )
        )

    def test_non_copula_subject_wrapper_is_not_repaired(self) -> None:
        def replace_subject_marker(kwargs):
            records = request_records(kwargs["messages"])
            translated = default_stream_translation(kwargs["messages"])
            first_common = next(
                record for record in records if record["section"] == "Common"
            )
            suffix_start = translated.index(first_common["marker_token"])
            subject = next(
                record for record in records if record["section"] == "Subjects"
            )
            return (
                f"<Subject 1> refers to {default_translation(subject)}\n"
                + translated[suffix_start:]
            )

        llm = FakeLLM([replace_subject_marker, replace_subject_marker])
        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(SOURCE, llm, "sys", max_tokens=128)
        self.assertEqual(len(llm.calls), 2)

    def test_markerless_line_aligned_output_is_strictly_validated(self) -> None:
        def markerless_lines(kwargs):
            return "\n".join(
                default_translation(record)
                for record in request_records(kwargs["messages"])
            )

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が話す。\n"
            "* <Subject 3>が止まる。"
        )
        llm = FakeLLM([markerless_lines])
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=128)
        self.assertEqual(output.count("* "), 3)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(
            any("strict line-aligned validation" in line for line in captured.output)
        )

    def test_markerless_wrapped_paragraphs_are_strictly_validated(self) -> None:
        def markerless_paragraphs(kwargs):
            translations = [
                default_translation(record)
                for record in request_records(kwargs["messages"])
            ]
            translations[1] = translations[1].replace(" ", "\n", 1)
            return "\n\n".join(translations)

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が話す。\n"
            "* <Subject 3>が止まる。"
        )
        llm = FakeLLM([markerless_paragraphs])
        with self.assertLogs("cl_japanese2json", level="INFO") as captured:
            output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=128)
        self.assertEqual(output.count("* "), 3)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(
            any(
                "strict paragraph-aligned validation" in line
                for line in captured.output
            )
        )
        self.assertTrue(
            any("paragraphs=3 non_empty_lines=4" in line for line in captured.output)
        )

    def test_markerless_shape_failure_salvages_anchored_segments(self) -> None:
        def damaged_markerless_response(kwargs):
            records = request_records(kwargs["messages"])
            translations = [default_translation(record) for record in records]
            translations[1] = translations[1].replace(
                records[1]["protected_placeholders"][0], ""
            )
            del translations[3]
            return "\n".join(translations)

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が話す。\n"
            "* <Subject 3>が止まる。\n"
            "* カメラが回る。\n"
            "* 空中で待つ。\n"
            "* <Subject 4>が戻る。"
        )
        llm = FakeLLM([damaged_markerless_response])
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=4096)

        self.assertEqual(output.count("* "), 6)
        self.assertEqual(len(llm.calls), 2)
        retried = request_records(llm.calls[1]["messages"])
        self.assertEqual(
            [record["id"] for record in retried],
            ["R000002", "R000004", "R000005"],
        )
        self.assertTrue(
            any(
                "Salvaged 3/6 markerless text segment" in line
                for line in captured.output
            )
        )
        retry_request = llm.calls[1]["messages"][-1]["content"]
        self.assertIn("only unresolved segments", retry_request)
        self.assertIn("natural English could omit", retry_request)

    def test_lf_crlf_and_no_final_newline_match(self) -> None:
        first = llmj2e.translate_markdown(SOURCE.rstrip("\n"), FakeLLM(), "sys", max_tokens=64)
        second = llmj2e.translate_markdown(
            SOURCE.replace("\n", "\r\n"), FakeLLM(), "sys", max_tokens=64
        )
        self.assertEqual(first, second)

    def test_scene_defaults_and_invalid_duration_fallback(self) -> None:
        cases = {
            "# シーン": "# Scene",
            "# シーン 1秒": "# Scene 1sec",
            "# シーン 60秒": "# Scene 60sec",
            "# シーン 継続": "# Scene CONTINUE",
            "# シーン 8秒 継続": "# Scene 8sec CONTINUE",
        }
        for directive, expected in cases.items():
            with self.subTest(directive=directive):
                output = llmj2e.translate_markdown(
                    f"{directive}\n* 動作。", FakeLLM(), "sys", max_tokens=64
                )
                self.assertIn(expected, output)
        for value in ("0", "61", "abc"):
            with self.subTest(value=value), self.assertLogs(
                "cl_japanese2json", level="WARNING"
            ):
                output = llmj2e.translate_markdown(
                    f"# シーン {value}秒\n* 動作。",
                    FakeLLM(),
                    "sys",
                    max_tokens=64,
                )
            self.assertIn("# Scene 5sec", output)

        for legacy in ("# シーン 8秒生成する", "# シーン 継続する"):
            with self.subTest(legacy=legacy), self.assertLogs(
                "cl_japanese2json", level="WARNING"
            ):
                output = llmj2e.translate_markdown(
                    f"{legacy}\n* 動作。", FakeLLM(), "sys", max_tokens=64
                )
            self.assertEqual(output.splitlines()[0], "# Scene")

    def test_recognized_directives_transition_without_blank_lines(self) -> None:
        source = "# 共通プロンプト\n* 共通。\n# シーン\n* 動作。\n# 共通プロンプト\n* 再共通。"
        output = llmj2e.translate_markdown(source, FakeLLM(), "sys", max_tokens=64)
        self.assertEqual(output.count("# Common"), 2)
        self.assertEqual(output.count("# Scene"), 1)
        self.assertEqual(output.count("* "), 3)

    def test_code_fence_causes_one_retry_then_success(self) -> None:
        llm = FakeLLM(["```json\n{}\n```"])
        output = llmj2e.translate_markdown(
            "# シーン\n* 動作。", llm, "sys", max_tokens=64
        )
        self.assertIn("# Scene", output)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("previous response failed", llm.calls[1]["messages"][-1]["content"].lower())
        self.assertIn("code fence", llm.calls[1]["messages"][-1]["content"].lower())

    def test_one_leading_qwen_thinking_block_is_ignored(self) -> None:
        def leading_think(kwargs):
            translated = default_stream_translation(kwargs["messages"])
            return "<think>internal reasoning</think>\n\n" + translated

        llm = FakeLLM([leading_think])
        with self.assertLogs("cl_japanese2json", level="INFO") as captured:
            output = llmj2e.translate_markdown(
                "# シーン\n* 動作。", llm, "sys", max_tokens=64
            )
        self.assertIn("# Scene", output)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(
            any("Ignored one leading Qwen thinking block" in line for line in captured.output)
        )

    def test_two_invalid_responses_raise(self) -> None:
        llm = FakeLLM(["not json", "still not json"])
        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* 動作。", llm, "sys", max_tokens=64
            )
        self.assertEqual(len(llm.calls), 2)

    def test_reordered_record_ids_are_rejected(self) -> None:
        def reversed_records(kwargs):
            records = request_records(kwargs["messages"])
            translated = default_stream_translation(kwargs["messages"])
            first, second = records
            temporary = "CLJ_SWAP_TEMP_X"
            translated = translated.replace(first["marker_token"], temporary)
            translated = translated.replace(
                second["marker_token"], first["marker_token"]
            )
            translated = translated.replace(temporary, second["marker_token"])
            return translated

        llm = FakeLLM([reversed_records, reversed_records])
        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* 一。\n* 二。", llm, "sys", max_tokens=64
            )

    def test_added_or_deleted_records_are_rejected(self) -> None:
        def wrong_count(delta):
            def responder(kwargs):
                records = request_records(kwargs["messages"])
                translated = default_stream_translation(kwargs["messages"])
                if delta < 0:
                    record = records[-1]
                    start = translated.index(record["marker_token"])
                    translated = translated[:start]
                else:
                    record = records[0]
                    marker = record["marker_token"]
                    start = translated.index(marker)
                    next_marker = translated.find("CLJT", start + len(marker))
                    snippet = (
                        translated[start:]
                        if next_marker < 0
                        else translated[start:next_marker]
                    )
                    translated += "\n" + snippet
                return translated

            return responder

        for delta in (-1, 1):
            responder = wrong_count(delta)
            with self.subTest(delta=delta), self.assertRaises(errors.TranslationError):
                llmj2e.translate_markdown(
                    "# シーン\n* 一。\n* 二。",
                    FakeLLM([responder, responder]),
                    "sys",
                    max_tokens=64,
                )

    def test_missing_placeholder_is_rejected(self) -> None:
        def missing(kwargs):
            return default_stream_translation(
                kwargs["messages"],
                transform=lambda _: "English without the protected tag.",
            )

        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* <Subject 1>が動く。",
                FakeLLM([missing, missing]),
                "sys",
                max_tokens=64,
            )

    def test_retry_recovers_only_omitted_direct_speech_placeholder(self) -> None:
        def first_response(kwargs):
            def transform(record):
                translated = default_translation(record)
                if record["id"] == "R000001":
                    return f"{record['protected_placeholders'][0]} まだ日本語。"
                dialogue = record["protected_placeholders"][-1]
                return translated.replace(dialogue, "")

            return default_stream_translation(
                kwargs["messages"], transform=transform
            )

        def retry_response(kwargs):
            def transform(record):
                translated = default_translation(record)
                if record["id"] == "R000002":
                    dialogue = record["protected_placeholders"][-1]
                    return translated.replace(dialogue, "")
                return translated

            return default_stream_translation(
                kwargs["messages"], transform=transform
            )

        source = (
            "# シーン\n"
            "* <Subject 1>が辺りを見る。\n"
            "* <Subject 1>が「こんにちは」と言う。"
        )
        llm = FakeLLM([first_response, retry_response])
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=128)

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("<d>[Japanese]こんにちは</d>", output)
        self.assertTrue(
            any(
                "Recovered 1 omitted direct-speech placeholder(s) in R000002"
                in line
                for line in captured.output
            )
        )

    def test_empty_retry_is_not_repaired_from_dialogue_alone(self) -> None:
        def omit_entire_value(kwargs):
            return default_stream_translation(
                kwargs["messages"], transform=lambda _: ""
            )

        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* 「こんにちは」と言う。",
                FakeLLM([omit_entire_value, omit_entire_value]),
                "sys",
                max_tokens=64,
            )

    def test_protected_placeholder_moved_between_segments_is_rejected(self) -> None:
        def swap_placeholders(kwargs):
            records = request_records(kwargs["messages"])
            first_token = records[0]["protected_placeholders"][0]
            second_token = records[1]["protected_placeholders"][0]
            translated = default_stream_translation(kwargs["messages"])
            temporary = "CLJ_PROTECTED_SWAP_TEMP_X"
            translated = translated.replace(first_token, temporary)
            translated = translated.replace(second_token, first_token)
            translated = translated.replace(temporary, second_token)
            return translated

        source = "# シーン\n* <Subject 1>が動く。\n* <Subject 2>が止まる。"
        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                source,
                FakeLLM([swap_placeholders, swap_placeholders]),
                "sys",
                max_tokens=64,
            )

    def test_japanese_residue_is_rejected(self) -> None:
        def japanese(kwargs):
            return default_stream_translation(
                kwargs["messages"], transform=lambda _: "まだ日本語。"
            )

        with self.assertRaises(errors.TranslationError):
            llmj2e.translate_markdown(
                "# シーン\n* 動作。",
                FakeLLM([japanese, japanese]),
                "sys",
                max_tokens=64,
            )

    def test_existing_direct_speech_japanese_is_allowed(self) -> None:
        output = llmj2e.translate_markdown(
            "# シーン\n* <d>[Japanese]そのまま</d> と動く。",
            FakeLLM(),
            "sys",
            max_tokens=64,
        )
        self.assertIn("<d>[Japanese]そのまま</d>", output)

    def test_finish_reason_length_empty_choices_and_think_are_rejected(self) -> None:
        bad_responses = [
            {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]},
            {"choices": []},
            {"choices": [{"finish_reason": "stop"}]},
            {"choices": [{"message": {"content": "  "}}]},
            {"choices": [{"message": {"content": "<think>x</think>"}}]},
            {"choices": [{"message": {"content": "<think>unclosed"}}]},
            {"choices": [{"message": {"content": "prefix <think>x</think>"}}]},
        ]
        for bad in bad_responses:
            with self.subTest(response=bad), self.assertRaises(errors.TranslationError):
                llmj2e.translate_markdown(
                    "# シーン\n* 動作。",
                    FakeLLM([bad, bad]),
                    "sys",
                    max_tokens=64,
                )

    def test_records_are_batched_without_splitting(self) -> None:
        long_body = "長い文章" * 100
        source = "# シーン\n" + "\n".join(f"* {long_body}" for _ in range(4))
        llm = FakeLLM(n_ctx=600)
        output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=32)
        self.assertEqual(output.count("* "), 4)
        self.assertGreater(len(llm.calls), 1)

    def test_whole_document_uses_one_inference_when_context_fits(self) -> None:
        source = "# シーン\n" + "\n".join("* 動作。" for _ in range(25))
        llm = FakeLLM()
        output = llmj2e.translate_markdown(source, llm, "sys", max_tokens=4096)
        self.assertEqual(output.count("* "), 25)
        self.assertEqual(len(llm.calls), 1)
        transported = request_records(llm.calls[0]["messages"])
        self.assertEqual(len(transported), 25)
        self.assertEqual(
            [record["id"] for record in transported],
            [f"R{index:06d}" for index in range(1, 26)],
        )

    def test_single_record_context_overflow_is_explicit(self) -> None:
        llm = FakeLLM(n_ctx=100)
        with self.assertRaisesRegex(errors.TranslationError, "does not fit"):
            llmj2e.translate_markdown(
                "# シーン\n* " + "長" * 500,
                llm,
                "sys",
                max_tokens=64,
            )

    def test_seed_is_deterministic_and_retry_uses_different_value(self) -> None:
        llm = FakeLLM(["invalid"])
        llmj2e.translate_markdown(
            "# シーン\n* 動作。", llm, "sys", max_tokens=64, seed=4294967295
        )
        self.assertEqual(len(llm.calls), 2)
        self.assertNotEqual(llm.calls[0]["seed"], llm.calls[1]["seed"])

    def test_retry_max_allows_multiple_retries_with_distinct_seeds(self) -> None:
        llm = FakeLLM(["invalid first", "invalid second", "invalid third"])
        output = llmj2e.translate_markdown(
            "# シーン\n* 動作。",
            llm,
            "sys",
            max_tokens=64,
            seed=17,
            retry_max=3,
        )
        self.assertIn("# Scene", output)
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(
            [call["seed"] for call in llm.calls],
            [17, 1_000_020, 2_000_023, 3_000_026],
        )

    def test_retry_max_zero_disables_retries(self) -> None:
        llm = FakeLLM(["invalid"])
        with self.assertRaisesRegex(errors.TranslationError, "retry_max=0"):
            llmj2e.translate_markdown(
                "# シーン\n* 動作。",
                llm,
                "sys",
                max_tokens=64,
                retry_max=0,
            )
        self.assertEqual(len(llm.calls), 1)

    def test_retry_max_minus_one_retries_until_success(self) -> None:
        llm = FakeLLM(["bad 1", "bad 2", "bad 3", "bad 4"])
        output = llmj2e.translate_markdown(
            "# シーン\n* 動作。",
            llm,
            "sys",
            max_tokens=64,
            retry_max=-1,
        )
        self.assertIn("# Scene", output)
        self.assertEqual(len(llm.calls), 5)
        self.assertEqual(len({call["seed"] for call in llm.calls}), 5)

    def test_unlimited_retry_does_not_swallow_backend_errors(self) -> None:
        def backend_error(_):
            raise RuntimeError("backend interrupted")

        llm = FakeLLM([backend_error])
        with self.assertRaisesRegex(RuntimeError, "backend interrupted"):
            llmj2e.translate_markdown(
                "# シーン\n* 動作。",
                llm,
                "sys",
                max_tokens=64,
                retry_max=-1,
            )
        self.assertEqual(len(llm.calls), 1)

    def test_each_retry_keeps_successes_and_sends_only_unresolved_records(self) -> None:
        def fail_both(kwargs):
            return default_stream_translation(
                kwargs["messages"],
                transform=lambda _: "English without a protected placeholder.",
            )

        def resolve_first_only(kwargs):
            def transform(record):
                if record["id"] == "R000002":
                    return "English without a protected placeholder."
                return default_translation(record)

            return default_stream_translation(kwargs["messages"], transform=transform)

        source = (
            "# シーン\n"
            "* <Subject 1>が動く。\n"
            "* <Subject 2>が止まる。"
        )
        llm = FakeLLM([fail_both, resolve_first_only])
        output = llmj2e.translate_markdown(
            source, llm, "sys", max_tokens=128, retry_max=2
        )

        self.assertEqual(output.count("* "), 2)
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(
            [record["id"] for record in request_records(llm.calls[1]["messages"])],
            ["R000001", "R000002"],
        )
        self.assertEqual(
            [record["id"] for record in request_records(llm.calls[2]["messages"])],
            ["R000002"],
        )

    def test_invalid_retry_max_is_rejected_before_inference(self) -> None:
        for invalid in (-2, True, 1.5):
            llm = FakeLLM()
            with self.subTest(invalid=invalid), self.assertRaises(
                errors.TranslationError
            ):
                llmj2e.translate_markdown(
                    "# シーン\n* 動作。",
                    llm,
                    "sys",
                    max_tokens=64,
                    retry_max=invalid,
                )
            self.assertEqual(llm.calls, [])
