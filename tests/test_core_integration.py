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

# 保持分析
* <Subject 1> 完全に保持: 外観を維持する。
* <Subject 2> 完全に保持: 外観を維持する。

# 共通プロンプト
* 明るいアニメ調にする。
* <Subject 1>と<Subject 2>を混同しない。

# シーン 5秒
* 二人はオフィスにいる。
## ショット
* <Subject 2>が「よろしく」と言う。
* <Subject 1>が手を上げる。
## 音響
* 環境音: 静かな室内音。
* 発声: 指定台詞のみ

# シーン 5秒 継続
## ショット
* <Subject 1>が「次です」と言う。
## 音響
* 発声: 指定台詞のみ"""
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
        self.assertIn("<Subject 2> (S2)", parsed["shots"][0]["prompt"][3])
        self.assertIn("<Subject 1> (S1)", parsed["shots"][1]["prompt"][3])
        self.assertIn(
            "The action occurs <Subject 1> <Subject 2>.",
            parsed["shots"][0]["prompt"][3],
        )
        self.assertNotIn("<Subject 2>", parsed["shots"][1]["prompt"][3])
        self.assertEqual(len(parsed["shots"][0]["prompt"]), 6)
        self.assertTrue(
            parsed["shots"][0]["prompt"][3].startswith("detailed_description:\n")
        )
        self.assertTrue(
            parsed["shots"][0]["prompt"][4].startswith("overall_soundscape:\n")
        )
        self.assertEqual(json.loads(text), parsed)

    def test_lip_sync_and_background_music_end_to_end(self) -> None:
        source = """# サブジェクト
* <Picture 1>を外観参照として使用する人物。

# シーン 6秒
## ショット
* <Subject 1>がカメラを見る。
* リップシンク: <Subject 1> <- <Audio 1> 「こんにちは！」
## 音響
* 発声: 指定台詞のみ
* BGM: ゆっくりしたピアノと低い弦楽器。"""
        canonical = llmj2e.translate_markdown(
            source, FakeLLM(), "system", max_tokens=128
        )
        parsed = jsongen.validate_final_json(
            jsongen.generate_json(mdparse.parse_markdown(canonical))
        )
        prompt = parsed["shots"][0]["prompt"]
        self.assertIn("[reference generation + audio reuse]", prompt[1])
        self.assertIn("<Audio 1>: partially_copy", prompt[2])
        self.assertIn("<d>[Japanese]こんにちは！</d>", prompt[3])
        self.assertIn("non_diegetic_music:\nA defined audible sound", prompt[5])

    def test_reused_background_music_vocal_lip_sync_end_to_end(self) -> None:
        source = """# サブジェクト
* <Picture 1>を外観参照として使用する歌手。

# 共通プロンプト
* <Audio 1>の音楽時間軸をシーン間で維持する。

# シーン 8秒
## ショット
* <Subject 1>がカメラを見ながら音楽に合わせて身体を動かす。
* リップシンク: <Subject 1> <- <Audio 1> 「夜空を越えて、君のもとへ。」
## 音響
* 発声: 指定台詞のみ
* BGM再利用: <Audio 1> 完全コピー"""
        canonical = llmj2e.translate_markdown(
            source, FakeLLM(), "system", max_tokens=128
        )
        self.assertIn(
            "* Background music reuse: <Audio 1> fully_copy", canonical
        )
        parsed = jsongen.validate_final_json(
            jsongen.generate_json(mdparse.parse_markdown(canonical))
        )
        prompt = parsed["shots"][0]["prompt"]
        self.assertIn("<Audio 1>: fully_copy", prompt[2])
        self.assertIn("<Audio 1>", prompt[3].split("[Shot 1]", 1)[0])
        self.assertIn("visually performs and lip-syncs exactly", prompt[3])
        self.assertIn("<Audio 1> is directly reused 1:1", prompt[5])
