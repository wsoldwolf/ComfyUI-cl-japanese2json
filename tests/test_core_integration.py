from __future__ import annotations

import json
import unittest

from .helpers import FakeLLM, module


llmj2e = module("compiler.llmj2e")
mdparse = module("compiler.mdparse")
jsongen = module("compiler.jsongen")


class CoreIntegrationTests(unittest.TestCase):
    def test_japanese_markdown_to_strict_plan_json(self) -> None:
        source = """# サブジェクト
* <Picture 1>と<Audio 1>を参照する人物。
* <Picture 2>を参照する人物。

# 共通プロンプト
* <Subject 1>と<Subject 2>はオフィスにいる。

# シーン 5秒
* <Subject 2>が「よろしく」と言う。
* <Subject 1>が手を上げる。

# シーン 5秒 継続
* <Subject 1>が「次です」と言う。"""
        canonical = llmj2e.translate_markdown(
            source, FakeLLM(), "system", max_tokens=64
        )
        emd = mdparse.parse_markdown(canonical)
        text = jsongen.generate_json(emd)
        parsed = jsongen.validate_final_json(text)
        self.assertEqual(len(parsed["shots"]), 2)
        self.assertEqual(parsed["shots"][0]["id"], "scene_1")
        self.assertEqual(parsed["shots"][1]["continuation_mode"], "guide")
        self.assertIn("<Subject 1>", parsed["shots"][0]["prompt"][0])
        self.assertIn("<Subject 2>", parsed["shots"][0]["prompt"][0])
        self.assertNotIn("<Subject 2>", parsed["shots"][1]["prompt"][0])
        self.assertIn("よろしく", text)
        self.assertEqual(json.loads(text), parsed)
