from __future__ import annotations

import json
import logging
import unittest
from unittest.mock import patch

from .helpers import FakeLLM, FakeBackend, module, default_stream_translation, request_records

review = module("common.semantic_review")
anchors = module("common.scene_anchors")
compiler = module("node_japanese_to_json.compiler.llmj2e")
compile_errors = module("node_japanese_to_json.compiler.errors")
engine = module("node_prompt_enhancer.engine")
profiles = module("node_prompt_enhancer.profiles")
enhance_errors = module("node_prompt_enhancer.errors")


def verdict(kwargs, rejected=None):
    request = json.loads(kwargs["messages"][-1]["content"])
    if request["review_policy"] == "translation_replacement":
        return json.dumps({"after": request["pairs"][0]["candidate"]})
    if request["review_policy"] == "translation_patch":
        if rejected:
            raise AssertionError("Use a grounded patch instead of an arbitrary failure reason")
        item = request["pairs"][0]
        return json.dumps({"source_excerpt": item["source"], "before": item["candidate"], "after": item["candidate"]})
    rows = []
    for item in request["pairs"]:
        if request["review_policy"] == "environment":
            rows.append(f"EVIDENCE\t{item['id']}\tFixture source fact: " + ("MISSING" if rejected else "matched"))
        rows.append(f"CHECK\t{item['id']}\tFAIL\t{rejected}" if rejected else f"CHECK\t{item['id']}\tPASS")
    return "\n".join(rows)


class SemanticProtocolTests(unittest.TestCase):
    def test_new_node_defaults_preserve_widget_order_and_safe_review_modes(self):
        compiler_node = module("node_japanese_to_json.node").CLJapaneseToJSONGGUF
        enhancer_node = module("node_prompt_enhancer.node").CLPromptEnhancerGGUF
        for node, default in ((compiler_node, "global"), (enhancer_node, False)):
            options = node.INPUT_TYPES()["optional"]
            self.assertEqual(list(options)[-1], "semantic_guard")
            self.assertEqual(options["semantic_guard"][1]["default"], default)

    def test_complete_ordered_verdicts(self):
        self.assertEqual(review.parse_review("CHECK\tR1\tPASS\nCHECK\tR2\tFAIL\tChanged the owner", ("R1", "R2")),
                         {"R2": "Changed the owner"})

    def test_missing_duplicate_unknown_and_truncated_verdicts_fail_closed(self):
        for raw in ("", "CHECK\tR1\tPASS", "CHECK\tR1\tPASS\nCHECK\tR1\tPASS",
                    "CHECK\tR3\tPASS", "CHECK\tR1\tFAIL", "CHECK\tR1\tPASS\textra",
                    "CHECK R1 PASS", "CHECK\tR2\tPASS\nCHECK\tR1\tPASS"):
            with self.subTest(raw=raw), self.assertRaises(review.SemanticReviewError):
                review.parse_review(raw, ("R1", "R2"))

    def test_review_protocol_grammar_parses_without_a_model(self):
        try:
            from llama_cpp import LlamaGrammar
        except ImportError:
            self.skipTest("optional llama.cpp runtime is unavailable")
        grammar = LlamaGrammar.from_string(review.review_grammar(("R1", "R2")), verbose=False)
        self.assertIsNotNone(grammar)
        self.assertIsNotNone(LlamaGrammar.from_string(
            review.review_grammar(("C1", "C2"), policy="environment"), verbose=False))
        self.assertIsNotNone(LlamaGrammar.from_string(
            review.review_grammar(("R1",), policy="translation_patch"), verbose=False))
        self.assertIsNotNone(LlamaGrammar.from_string(
            review.review_grammar(("R1",), policy="translation_replacement"), verbose=False))

    def test_full_unit_replacement_binds_only_to_the_supplied_pair(self):
        pairs = [{"id": "R13", "source": "白い服", "candidate": "A black outfit."}]
        self.assertEqual(review.parse_translation_replacement('{"after":"A white outfit."}', pairs),
                         {"R13": {"source": "白い服", "before": "A black outfit.", "after": "A white outfit."}})
        for raw in ('{}', '{"after":""}', '{"after":42}',
                    '{"after":"a","after":"b"}', '{"before":"other","after":"a"}',
                    '{"after":"truncated', json.dumps({"after": "x" * 8193})):
            with self.subTest(raw=raw[:60]), self.assertRaises(review.SemanticReviewError):
                review.parse_translation_replacement(raw, pairs)


    def test_patch_protocol_requires_complete_literals_for_one_pair(self):
        raw = json.dumps({"source_excerpt": "短い", "before": "thick ovals", "after": "short thick ovals"})
        self.assertEqual(review.parse_translation_patches(raw, ("R1",)), {
            "R1": {"source": "短い", "before": "thick ovals", "after": "short thick ovals"}})
        self.assertEqual(review.parse_translation_patches(
            '{"source_excerpt":"短い","before":"short","after":"short"}', ("R1",), source="短い。"),
            {"R1": {"source": "短い", "before": "short", "after": "short"}})
        with self.assertRaisesRegex(review.SemanticReviewError, "duplicate JSON key"):
            review.parse_translation_patches(
                '{"source_excerpt":"短い","before":"short","after":"short","after":"long"}', ("R1",))
        with self.assertRaises(review.SemanticReviewError):
            review.parse_translation_patches(raw, ("R1",), source="別の原文。")
        for invalid in ("CHECK\tR1\tPASS", "", "[]", '{"evidence":"MISSING","verdict":"PASS"}',
                        '{"evidence":"ok","verdict":"PASS","verdict":"PASS"}',
                        '{"evidence":"ok","verdict":"FAIL"}',
                        '{"evidence":"ok","verdict":[]}', '{"verdict":"PASS"}'):
            with self.assertRaises(review.SemanticReviewError):
                review.parse_translation_patches(invalid, ("R1",))

    def test_environment_review_requires_evidence_before_each_verdict(self):
        raw = "EVIDENCE\tC1\tSource object: MISSING\nCHECK\tC1\tFAIL\tMissing object"
        self.assertEqual(review.parse_review(raw, ("C1",), policy="environment"), {"C1": "Missing object"})
        for invalid in ("CHECK\tC1\tPASS", "EVIDENCE\tC2\tx\nCHECK\tC1\tPASS",
                        "EVIDENCE\tC1\tobject: MISSING\nCHECK\tC1\tPASS"):
            with self.assertRaises(review.SemanticReviewError):
                review.parse_review(invalid, ("C1",), policy="environment")

    def test_patch_fields_cannot_be_empty_missing_or_nontext(self):
        for field in ("source_excerpt", "before", "after"):
            for value in ([], {}, None, "", " ", 42):
                data = {"source_excerpt": "短い", "before": "short", "after": "short", field: value}
                with self.subTest(field=field, value=value), self.assertRaises(review.SemanticReviewError):
                    review.parse_translation_patches(json.dumps(data), ("R1",), source="短い。")

    def test_boolean_interrupt_stops_before_inference(self):
        with self.assertRaises(review.SemanticReviewCancelled):
            review.run_review([{"id": "R1"}], policy="translation", context={},
                              invoke=lambda *args: self.fail("must not infer"),
                              logger=logging.getLogger("test"), label="cancel review",
                              interrupt_callback=lambda: True)

    def test_context_batching_preserves_all_pairs_and_never_truncates_authority(self):
        class Backend:
            def effective_n_ctx(self):
                return 100
            def count_input_tokens(self, messages):
                data = json.loads(messages[-1]["content"])
                self.last_context = data["context"]
                return 25 * len(data["pairs"])
        backend = Backend()
        pairs = [{"id": str(i), "source": "Original"} for i in range(5)]
        result = list(review.review_batches(pairs, policy="translation", context={"authority": "Unchanged"},
                                          backend=backend, output_budget=50))
        self.assertEqual([len(batch) for batch in result], [2, 2, 1])
        self.assertEqual([item for batch in result for item in batch], pairs)
        self.assertEqual(backend.last_context, {"authority": "Unchanged"})
        with self.assertRaises(review.SemanticReviewCapacityError):
            list(review.review_batches(pairs, policy="translation", context={}, backend=backend, output_budget=90))

    def test_interrupt_and_heartbeat_cleanup(self):
        def interrupted():
            raise RuntimeError("user interrupted")
        events = []
        with self.assertRaisesRegex(RuntimeError, "user interrupted"):
            review.run_review([{"id": "R1"}], policy="translation", context={},
                              invoke=lambda *args: self.fail("must not infer"),
                              logger=logging.getLogger("test"), label="test review",
                              events=events, interrupt_callback=interrupted)
        self.assertEqual(events[0]["validation"], "failed")


class TranslationMeaningTests(unittest.TestCase):
    SOURCE = "# 共通プロンプト\n* 目尻は目頭より少し高い。\n* 衣装は白い。\n# シーン\n## ショット\n* 動作。"

    def test_only_confirmed_semantic_failure_is_patched(self):
        def translate(kwargs):
            return default_stream_translation(kwargs["messages"], lambda row:
                "The lower eyelid is higher than the upper eyelid." if "目尻" in row["text"]
                else "The outfit is white.")
        def assess(kwargs):
            pairs = json.loads(kwargs["messages"][-1]["content"])["pairs"]
            return "\n".join(f"CHECK\t{item['id']}\t" +
                             ("FAIL\tCompare outer and inner corners, not the two eyelids."
                              if "lower eyelid" in item["candidate"] else "PASS") for item in pairs)
        def repair(kwargs):
            request = json.loads(kwargs["messages"][-1]["content"])
            self.assertEqual(request["review_policy"], "translation_patch")
            self.assertEqual(len(request["pairs"]), 1)
            item = request["pairs"][0]
            return self.patch_response(kwargs, "目尻は目頭より少し高い", item["candidate"],
                                       "The outer eye corner is slightly higher than the inner corner.")
        llm = FakeLLM([translate, assess, repair, assess])
        result = compiler.translate_markdown(self.SOURCE, llm, "system", semantic_guard="global", max_tokens=1024)
        self.assertEqual(len(llm.calls), 4)
        self.assertIn("outer eye corner", result)
        self.assertNotIn("lower eyelid", result)
        self.assertIn("outfit is white", result)

    def test_salvaged_structure_still_undergoes_meaning_review(self):
        source = "# 共通プロンプト\n* 短く太い楕円形の眉。\n# シーン\n## ショット\n* 動作。"
        def translated(kwargs):
            raw = default_stream_translation(kwargs["messages"], lambda _: "Thin and short thick elliptical eyebrows.")
            return {"choices": [{"message": {"content": raw}, "finish_reason": "length"}]}
        with self.assertRaisesRegex(compile_errors.TranslationError, "Meaning review failed"):
            compiler.translate_markdown(source, FakeLLM([
                translated, lambda k: verdict(k, "Added an opposing thickness"),
                lambda k: self.patch_response(k, "太い", "Thin and short thick", "Short thick")]),
                                        "system", semantic_guard="global", retry_max=0)

    def test_unlimited_transport_does_not_make_meaning_repair_unlimited(self):
        def translated(kwargs):
            return default_stream_translation(kwargs["messages"], lambda _: "The black outfit has two square fasteners.")
        responses = [translated, lambda k: verdict(k, "Wrong color"),
                     lambda k: self.patch_response(k, "白い", "black", "white"),
                     lambda k: verdict(k, "Wrong count"),
                     lambda k: self.patch_response(k, "三つ", "two", "three"),
                     lambda k: verdict(k, "Wrong shape"),
                     lambda k: self.patch_response(k, "丸い", "square", "round")]
        llm = FakeLLM(responses)
        with self.assertRaisesRegex(compile_errors.TranslationError, "after 2 repair"):
            compiler.translate_markdown("# 共通プロンプト\n* 白い衣装には三つの丸い留め具がある。\n# シーン\n## ショット\n* 動作。",
                                        llm, "system", semantic_guard="global", retry_max=-1)
        self.assertEqual(len(llm.calls), 7)

    @staticmethod
    def patch_response(kwargs, source, before, after):
        return json.dumps({"source_excerpt": source, "before": before, "after": after}, ensure_ascii=False)

    def test_review_focus_contains_only_verbatim_source_not_stale_candidate(self):
        guard = module("node_japanese_to_json.compiler.semantic_guard")
        records, _ = compiler._sentence_records(compiler.lex_japanese_markdown(self.SOURCE).records)
        record = next(item for item in records if "目尻" in item.payload.text)
        self.assertEqual(guard._source_focus(record,
            '"目尻は目頭より少し高い" is translated as "lower eyelid" but should be "outer corner". '
            '"原文に無い文" was omitted.'), ["目尻は目頭より少し高い"])

    def test_false_same_expression_failure_is_confirmed_without_retranslation(self):
        llm = FakeLLM([
            lambda k: default_stream_translation(k["messages"], lambda _: "One on each side."),
            lambda k: verdict(k, '"one on each side" should be "one on each side" (same)'),
            verdict])
        result = compiler.translate_markdown(
            "# 共通プロンプト\n* 左右に一つずつ配置する。\n# シーン\n## ショット\n* 動作。",
            llm, "system", semantic_guard="global", retry_max=0)
        self.assertIn("One on each side.", result)
        self.assertEqual(len(llm.calls), 3)

    def test_short_omission_is_patched_without_rewriting_other_attributes(self):
        source = "# 共通プロンプト\n* 淡い金色で塗られた小さく短く太い楕円形の眉毛を左右に一つずつ配置する。\n# シーン\n## ショット\n* 動作。"
        before = "Place a small thick elliptical eyebrow painted in light gold on each side."
        events = []
        llm = FakeLLM([
            lambda k: default_stream_translation(k["messages"], lambda _: before),
            lambda k: verdict(k, '"one on each side" should be "one on each side" (same); short missing'),
            lambda k: self.patch_response(k, "小さく短く太い楕円形の眉毛", "a small thick elliptical eyebrow",
                                         "a small, short, thick elliptical eyebrow"), verdict])
        result = compiler.translate_markdown(source, llm, "system", semantic_guard="global", debug_events=events)
        self.assertIn(before.replace("small thick", "small, short, thick"), result)
        self.assertEqual(sum("TRANSLATION_STREAM_BEGIN" in k["messages"][-1]["content"] for k in llm.calls), 1)
        self.assertEqual(sum(e["stage"] == "semantic_patch" for e in events if "stage" in e), 1)
        json.dumps(events)  # Debug bundle must remain serializable.

    def test_invalid_patch_is_rechecked_not_applied(self):
        for source, before, after in (("存在しない原文", "each side", "both sides"),
                                      ("左右", "not in the candidate", "both sides")):
            with self.subTest(before=before, after=after):
                llm = FakeLLM([
                    lambda k: default_stream_translation(k["messages"], lambda _: "One on each side."),
                    lambda k: verdict(k, "Same expression"),
                    lambda k: self.patch_response(k, source, before, after), verdict])
                result = compiler.translate_markdown(
                    "# 共通プロンプト\n* 左右に一つずつ。\n# シーン\n## ショット\n* 動作。",
                    llm, "system", semantic_guard="global")
                self.assertIn("One on each side.", result)
                self.assertIn("validation_feedback", llm.calls[-1]["messages"][-1]["content"])
                self.assertEqual(len(llm.calls), 4)

    def test_noop_confirmation_keeps_original_without_consuming_repair_budget(self):
        for after in ("each side", "EACH   side"):
            llm = FakeLLM([
                lambda k: default_stream_translation(k["messages"], lambda _: "One on each side."),
                lambda k: verdict(k, "Same expression"),
                lambda k: self.patch_response(k, "左右", "each side", after)])
            result = compiler.translate_markdown(
                "# 共通プロンプト\n* 左右に一つずつ。\n# シーン\n## ショット\n* 動作。",
                llm, "system", semantic_guard="global", retry_max=0)
            self.assertIn("One on each side.", result)
            self.assertEqual(len(llm.calls), 3)

    def test_missing_or_ambiguous_patch_locator_uses_full_unit_and_reaudits(self):
        source = "# 共通プロンプト\n* 左の衣装は白く、右の衣装は黒い。\n# シーン\n## ショット\n* 動作。"
        original = "The left outfit is black and the right outfit is black."
        corrected = "The left outfit is white and the right outfit is black."
        for locator in ("black", "The left outfit is white"):
            def replace(kwargs):
                request = json.loads(kwargs["messages"][-1]["content"])
                self.assertEqual(request["review_policy"], "translation_replacement")
                self.assertEqual(request["pairs"][0]["candidate"], original)
                self.assertIn("validation_feedback", request["context"])
                return json.dumps({"after": corrected})
            def recheck(kwargs):
                request = json.loads(kwargs["messages"][-1]["content"])
                self.assertEqual(request["review_policy"], "translation")
                self.assertEqual(request["pairs"][0]["candidate"], corrected)
                return verdict(kwargs)
            with self.subTest(locator=locator):
                events = []
                llm = FakeLLM([
                    lambda k: default_stream_translation(k["messages"], lambda _: original),
                    lambda k: verdict(k, "Wrong color of the left outfit"),
                    lambda k: self.patch_response(k, "左の衣装は白く", locator, "white"),
                    replace, recheck])
                result = compiler.translate_markdown(source, llm, "system", semantic_guard="global", debug_events=events)
                self.assertIn(corrected, result)
                self.assertEqual(len(llm.calls), 5)
                self.assertEqual(sum(e.get("stage") == "semantic_patch" for e in events), 1)

    def test_full_unit_repair_cannot_remove_reference_tag(self):
        source = "# シーン\n## ショット\n* <Subject 1>は白い衣装を着る。"
        llm = FakeLLM([
            lambda k: default_stream_translation(k["messages"]),
            lambda k: verdict(k, "Wrong meaning"),
            lambda k: self.patch_response(k, "白い衣装", "absent", "white outfit"),
            json.dumps({"after": "A white outfit."})])
        with self.assertRaisesRegex(compile_errors.TranslationError, "preserve every reference tag"):
            compiler.translate_markdown(source, llm, "system", semantic_guard="all")
        self.assertEqual(len(llm.calls), 4)

    def test_noop_detection_preserves_word_boundaries_and_numeric_punctuation(self):
        guard = module("node_japanese_to_json.compiler.semantic_guard")
        for before, after in (("3.0", "30"), ("-5", "5"), ("each side", "eachside")):
            self.assertNotEqual(guard._meaningful_text(before), guard._meaningful_text(after))

    def test_repeated_invalid_confirmation_stops_with_a_separate_bounded_error(self):
        llm = FakeLLM([lambda k: default_stream_translation(k["messages"]),
                       lambda k: verdict(k, "Wrong meaning"), "CHECK\tR000001\tFAIL\tx", "CHECK\tR000001\tFAIL\tx"])
        with self.assertRaisesRegex(compile_errors.TranslationError, "Meaning confirmation.*after 2 checks"):
            compiler.translate_markdown(self.SOURCE, llm, "system", semantic_guard="global", retry_max=-1)
        self.assertEqual(len(llm.calls), 4)

    def test_patch_preserves_tags_and_dialogue_and_rejects_new_japanese(self):
        guard = module("node_japanese_to_json.compiler.semantic_guard")
        records, _ = compiler._sentence_records(compiler.lex_japanese_markdown(
            "# シーン\n## ショット\n* <Subject 1>は短い棒を持ち、「こんにちは」と言う。").records)
        record = records[0]
        record.translated = '<Subject 1> holds a rod and says <d>[Japanese]こんにちは</d>.'
        changed = guard._validated_patch(record, {"source": "短い棒", "before": "a rod", "after": "a short rod"})
        self.assertEqual(changed, record.translated.replace("a rod", "a short rod"))
        for before, after in (("<Subject 1>", "<Subject 2>"),
                              ("こんにちは", "さようなら"), ("a rod", "a 短い rod")):
            with self.subTest(after=after), self.assertRaises((review.SemanticReviewError, compile_errors.TranslationError)):
                guard._validated_patch(record, {"source": "短い棒", "before": before, "after": after})

    def test_duplicate_constraints_share_one_translation_and_review(self):
        source = "# サブジェクト\n* 人物。眉毛は短く太い楕円形。\n# 保持分析\n* <Subject 1> 完全に保持: 外観。眉毛は短く太い楕円形。\n# シーン\n## ショット\n* 動作。"
        def dispatch(kwargs):
            if '"review_policy"' in kwargs["messages"][-1]["content"]:
                return verdict(kwargs)
            return default_stream_translation(kwargs["messages"], lambda row:
                "The eyebrows are short, thick oval shapes." if "眉毛" in row["text"] else "A person.")
        llm = FakeLLM([dispatch] * 10)
        result = compiler.translate_markdown(source, llm, "system", semantic_guard="global")
        translation_calls = [call for call in llm.calls if "TRANSLATION_STREAM_BEGIN" in call["messages"][-1]["content"]]
        translated_brows = [row for call in translation_calls for row in request_records(call["messages"]) if "眉毛" in row["text"]]
        self.assertEqual(len(translated_brows), 1)
        self.assertEqual(result.count("The eyebrows are short, thick oval shapes."), 2)
        self.assertEqual(result.count("* "), 3)

    def test_malformed_review_is_not_an_approved_translation(self):
        llm = FakeLLM([lambda k: default_stream_translation(k["messages"]), "PASS"])
        with self.assertRaisesRegex(compile_errors.TranslationError, "could not validate"):
            compiler.translate_markdown(self.SOURCE, llm, "system", semantic_guard="global")
        self.assertEqual(len(llm.calls), 2)

    def test_invalid_scope(self):
        with self.assertRaisesRegex(compile_errors.TranslationError, "semantic_guard"):
            compiler.translate_markdown(self.SOURCE, FakeLLM(), "system", semantic_guard="maybe")


class EnvironmentMeaningTests(unittest.TestCase):
    def test_verbatim_anchor_needs_no_model_verdict(self):
        raw = "ENHANCEMENT_V1\nSOURCE\tC001\tanchor\nBACKGROUND\t霧が漂う。\nEND_ENHANCEMENT"
        backend = FakeBackend([raw])
        result = self.run_enhancer(backend)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result.report["semantic_review_requests"], 0)
        self.assertTrue(result.report["scene_anchors"])
    def test_environment_salience_word_is_not_a_standing_action(self):
        protocol = module("node_prompt_enhancer.protocol")
        result = protocol.parse_enhancement_response(
            "ENHANCEMENT_V1\nBACKGROUND\t月光で建物の屋根の勾配が際立つ。\nEND_ENHANCEMENT",
            expected_source_ids=(), minimum_background_lines=1, maximum_background_lines=1)
        self.assertEqual(len(result.background_lines), 1)
        with self.assertRaises(enhance_errors.EnhancerResponseError):
            protocol.parse_enhancement_response(
                "ENHANCEMENT_V1\nBACKGROUND\t地面に立つ。\nEND_ENHANCEMENT",
                expected_source_ids=(), minimum_background_lines=1, maximum_background_lines=1)
    SOURCE = "# サブジェクト\n* 人物。\n# 共通プロンプト\n* 中央に赤い鳥居があり、奥に本殿がある。"

    def run_enhancer(self, backend, *, source=None, user="", retry_max=2):
        return engine.enhance_reduced_markdown(
            source or self.SOURCE, user,
            style=profiles.load_style_profile("passthrough"),
            background=profiles.load_background_profile("reduce"), backend=backend,
            max_tokens=1024, temperature=0.1, top_p=0.9, repetition_penalty=1.05,
            seed=1, retry_max=retry_max, semantic_guard=True)

    def test_vanished_object_is_repaired_then_carried_as_anchor(self):
        bad = "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\nBACKGROUND\t霧が漂う。\nEND_ENHANCEMENT"
        good = "ENHANCEMENT_V1\nSOURCE\tC001\tanchor\nBACKGROUND\t霧が漂う。\nEND_ENHANCEMENT"
        backend = FakeBackend([bad, lambda k: verdict(k, "Missing the red gate and hall spatial relationship"),
                               good, verdict])
        result = self.run_enhancer(backend)
        self.assertIn("情景の固定要素: 中央に赤い鳥居", result.markdown)
        self.assertEqual(result.report["semantic_review_requests"], 1)
        self.assertEqual(result.request_count, 3)
        self.assertIn("Missing the red gate", backend.calls[2]["messages"][-1]["content"])
        self.assertEqual(result.markdown.count("# サブジェクト"), 1)

    def test_mixed_time_change_preserves_physical_fact(self):
        source = self.SOURCE.replace("中央に赤い鳥居", "昼間の中央に赤い鳥居")
        good = "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\nBACKGROUND\t夜の境内の中央に赤い鳥居があり、奥に本殿がある。\nEND_ENHANCEMENT"
        def assess(kwargs):
            context = json.loads(kwargs["messages"][-1]["content"])["context"]
            self.assertIn("時間帯は深夜とする。", context["immutable_user_prompt"]["common"])
            self.assertNotIn("昼間", json.loads(kwargs["messages"][-1]["content"])["pairs"][0]["candidate"])
            self.assertIn("赤い鳥居", json.loads(kwargs["messages"][-1]["content"])["pairs"][0]["candidate"])
            return verdict(kwargs)
        result = self.run_enhancer(FakeBackend([good, assess]), source=source,
                                   user="# 共通プロンプト\n* 時間帯は深夜とする。")
        self.assertIn("赤い鳥居", result.markdown)
        self.assertNotIn("昼間", result.markdown)

    def test_explicit_user_setting_override_is_available_to_reviewer(self):
        user = "# 共通プロンプト\n* 舞台を砂漠へ変更し、建物は配置しない。"
        raw = "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\nBACKGROUND\t砂漠には起伏のある砂丘が広がる。\nEND_ENHANCEMENT"
        def assess(kwargs):
            self.assertIn("舞台を砂漠へ変更", kwargs["messages"][-1]["content"])
            return verdict(kwargs)
        result = self.run_enhancer(FakeBackend([raw, assess]), user=user)
        self.assertNotIn("鳥居", result.markdown)
        self.assertIn("砂漠", result.markdown)

    def test_persistent_loss_stops_after_two_repairs_even_with_large_budget(self):
        bad = "ENHANCEMENT_V1\nSOURCE\tC001\tbackground\nBACKGROUND\t霧が漂う。\nEND_ENHANCEMENT"
        backend = FakeBackend([bad, lambda k: verdict(k, "Location missing")] * 3)
        with self.assertRaisesRegex(enhance_errors.PromptEnhancerError, "Location missing"):
            self.run_enhancer(backend, retry_max=10)
        self.assertEqual(len(backend.calls), 6)


class PlannerAnchorTests(unittest.TestCase):
    def test_anchors_survive_limited_range_and_do_not_change_timing_or_lipsync(self):
        from .test_mv_prompt_planner import BRIEF, TIMELINE
        planning = module("node_mv_prompt_planner.planning")
        brief_parser = module("node_mv_prompt_planner.brief_parser")
        limiter = module("node_scene_limiter.node")
        parser = module("node_mv_prompt_planner.timeline_parser")
        renderer = module("node_mv_prompt_planner.renderer")
        types = module("node_mv_prompt_planner.structures")
        anchor = "赤い鳥居の奥に本殿がある。"
        brief = brief_parser.parse_planning_brief(BRIEF + "* " + anchors.scene_anchor(anchor) + "\n")
        timeline = parser.parse_prompt_timeline(limiter.limit_reduced_markdown_scenes(TIMELINE, 1, scene_start_number=2))
        protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        payload = planning._planning_brief_payload(brief, protector)
        self.assertEqual(payload["scene_anchors"], [anchor])
        shot = types.PlannedShot(0, "<Subject 1>が前景に立つ。", ("<Subject 1>が踏み出す。",),
                                 "月光が地面を照らす。", types.CameraPlan("arc", "large", "moderate", "人物の側面へ回る。"))
        plan = types.MVPlan(types.SongBible("夜の情景。", ()), (types.PlannedScene(2, "進む。", (shot,)),))
        output = renderer.render_planned_markdown(brief, timeline, plan)
        self.assertIn("* " + anchor, output.split("## ショット", 1)[1])
        self.assertEqual(parser.parse_prompt_timeline(output), timeline)
        self.assertIn("リップシンク: <Subject 1> <- ソースボーカル", output)


if __name__ == "__main__":
    unittest.main()
