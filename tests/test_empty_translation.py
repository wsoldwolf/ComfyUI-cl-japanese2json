from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from .helpers import FakeLLM, default_stream_translation, default_translation, module, request_records


translator = module("node_japanese_to_json.compiler.llmj2e")
parser = module("node_japanese_to_json.compiler.mdparse")
SOURCE = "# シーン 5秒\n## ショット\n* 朝の街。\n* 人物が歩く。\n* 風が吹く。\n"


def empty_record(record_id, *, broken_envelope=False):
    def response(kwargs):
        value = default_stream_translation(
            kwargs["messages"],
            lambda record: " \t " if record["id"] == record_id else default_translation(record),
        )
        if broken_envelope:
            # A repeated directive forces the independent record salvage path.
            marker = re.search(r"CLJT\d+D\d+X", value).group()
            value = marker + " " + value
        return value
    return response


class EmptyTranslationTests(unittest.TestCase):
    def test_only_empty_record_is_retried_and_bullets_are_preserved(self):
        for record_id in ("R000001", "R000002", "R000003"):
            for broken in (False, True):
                with self.subTest(record_id=record_id, broken_envelope=broken):
                    llm = FakeLLM([empty_record(record_id, broken_envelope=broken)])
                    output = translator.translate_markdown(SOURCE, llm, "system", retry_max=1)
                    self.assertEqual(len(llm.calls), 2)
                    self.assertEqual([r["id"] for r in request_records(llm.calls[1]["messages"])], [record_id])
                    self.assertEqual(output.count("* "), 3)
                    self.assertFalse(any(line.strip() == "*" for line in output.splitlines()))
                    parser.parse_markdown(output)

    def test_persistent_empty_record_fails_in_translation_not_markdown_parser(self):
        llm = FakeLLM([empty_record("R000002"), empty_record("R000002")])
        with self.assertRaisesRegex(translator.TranslationError, "R000002.*empty translation"):
            translator.translate_markdown(SOURCE, llm, "system", retry_max=1)
        self.assertEqual(len(llm.calls), 2)

    def test_all_sections_reject_empty_values(self):
        record = translator.lex_japanese_markdown(SOURCE).records[0]
        for section in ("Subjects", "Retention", "Common", "Scene", "Shot", "Soundscape"):
            record.section = section
            for value in ("", " \t "):
                with self.subTest(section=section, value=value):
                    with self.assertRaisesRegex(translator.TranslationError, "empty translation"):
                        translator._validate_translation_text(record, value)

    def test_rebuild_rejects_empty_validated_record(self):
        document = translator.lex_japanese_markdown(SOURCE)
        for record in document.records:
            record.translated = "The action occurs."
        document.records[1].translated = " "
        with self.assertRaisesRegex(translator.TranslationError, "R000002.*no validated translation"):
            translator._rebuild(document)

    def test_empty_sentence_cannot_disappear_during_sentence_join(self):
        source = "# サブジェクト\n* 成人の人物。長い髪。\n\n" + SOURCE
        def empty_second_sentence(records, *args, **kwargs):
            records[1].translated = " "
        guard = module("node_japanese_to_json.compiler.semantic_guard")
        with patch.object(guard, "audit_translations", side_effect=empty_second_sentence):
            with self.assertRaisesRegex(translator.TranslationError, "incomplete sentence translations"):
                translator.translate_markdown(source, FakeLLM(), "system", semantic_guard="global")
