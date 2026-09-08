from __future__ import annotations

import json
import unittest

from .helpers import FakeLLM, ROOT, module


llmj2e = module("compiler.llmj2e")
mdparse = module("compiler.mdparse")
jsongen = module("compiler.jsongen")


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_bundled_workflows_use_current_node_inputs_and_scene_syntax(self) -> None:
        paths = sorted((ROOT / "workflows").glob("*.json"))
        self.assertTrue(paths)

        for path in paths:
            with self.subTest(workflow=path.name):
                workflow = json.loads(path.read_text(encoding="utf-8"))
                serialized = json.dumps(workflow, ensure_ascii=False)
                self.assertNotRegex(
                    serialized,
                    r"<Video (?:[4-9]|[1-9][0-9]+)>",
                )
                self.assertNotIn("<Video 1>～<Video 9>", serialized)
                compiler_nodes = [
                    node
                    for node in workflow["nodes"]
                    if node.get("type") == "CLJapaneseToJSONGGUF"
                ]
                self.assertEqual(len(compiler_nodes), 1)
                compiler = compiler_nodes[0]
                input_names = [item["name"] for item in compiler["inputs"]]
                self.assertIn("steps", input_names)
                self.assertEqual(compiler["widgets_values_named"]["steps"], 8)
                self.assertEqual(
                    compiler["widgets_values_named"]["retry_max"], 10
                )
                retry_index = list(compiler["widgets_values_named"]).index(
                    "retry_max"
                )
                self.assertEqual(compiler["widgets_values"][retry_index], 10)
                if "save_debug_output" in input_names:
                    self.assertFalse(
                        compiler["widgets_values_named"]["save_debug_output"]
                    )

                prompt_nodes = [
                    node
                    for node in workflow["nodes"]
                    if node.get("type") == "PrimitiveStringMultiline"
                    and node.get("widgets_values")
                    and isinstance(node["widgets_values"][0], str)
                    and "# サブジェクト" in node["widgets_values"][0]
                ]
                self.assertEqual(len(prompt_nodes), 1)
                source = prompt_nodes[0]["widgets_values"][0]
                self.assertIn("# 共通プロンプト", source)
                self.assertIn("## ショット", source)
                self.assertNotRegex(source, r"\(S[1-9][0-9]*\)")
                for line in source.splitlines():
                    if "「" in line:
                        self.assertRegex(
                            line,
                            r"<Subject [1-9][0-9]*>.*「",
                        )

                canonical = llmj2e.translate_markdown(
                    source,
                    FakeLLM(n_ctx=1_000_000),
                    "system",
                    max_tokens=16_384,
                )
                emd = mdparse.parse_markdown(canonical)
                self.assertTrue(emd.common_prompt)
                plan = jsongen.validate_final_json(jsongen.generate_json(emd))
                self.assertEqual(
                    [
                        section.split(":", 1)[0]
                        for section in plan["shots"][0]["prompt"]
                    ],
                    [
                        "subject_definitions",
                        "summary",
                        "retention_analysis",
                        "detailed_description",
                        "overall_soundscape",
                        "non_diegetic_music",
                    ],
                )

                for node in workflow["nodes"]:
                    for value in node.get("widgets_values", []):
                        if not isinstance(value, str):
                            continue
                        self.assertNotIn("(Sx)", value)
                        self.assertNotRegex(value, r"共通プロンプト.*廃止")
                        for line in value.splitlines():
                            if line.startswith("# シーン"):
                                self.assertNotIn("秒生成する", line)
                                self.assertNotIn("継続する", line)

    def test_bgm_sync_workflow_uses_aligned_source_timeline_tracks(self) -> None:
        path = (
            ROOT
            / "workflows"
            / "minimax_h3_ref2va_20260908_bgm_sync_full_locked.json"
        )
        workflow = json.loads(path.read_text(encoding="utf-8"))
        nodes = {int(node["id"]): node for node in workflow["nodes"]}
        links = {int(link[0]): link for link in workflow["links"]}

        full_mix_pad = nodes[2023]
        vocal_pad = nodes[2020]
        self.assertEqual(full_mix_pad["type"], "CLAudioPad")
        self.assertEqual(vocal_pad["type"], "CLAudioPad")
        self.assertEqual(
            full_mix_pad["widgets_values_named"],
            {
                "target_duration_seconds": 0.0,
                "extra_padding_seconds": 0.0,
                "pad_position": "end",
            },
        )
        self.assertEqual(vocal_pad["widgets_values_named"], full_mix_pad["widgets_values_named"])
        self.assertEqual(links[3760][1:5], [2022, 0, 2023, 0])
        self.assertEqual(links[3571][1:5], [2008, 0, 2020, 0])
        self.assertEqual(links[3790][1:5], [2023, 0, 2020, 2])
        self.assertEqual(links[3763][1:5], [2023, 0, 2024, 0])
        self.assertEqual(links[3762][1:5], [2020, 0, 2024, 1])
        self.assertEqual(links[3764][1:5], [2024, 0, 1701, 3])
        self.assertEqual(links[3778][1:5], [2008, 0, 2025, 0])

        source = nodes[1952]["widgets_values"][0]
        self.assertNotRegex(source, r"<Audio [0-9]+>")
        self.assertIn("リップシンク: <Subject 1> <- ソースボーカル", source)
        self.assertIn("発声: ソースボーカルのみ", source)
        self.assertIn("ソース音声: 完全維持", source)
        canonical = llmj2e.translate_markdown(
            source,
            FakeLLM(n_ctx=1_000_000),
            "system",
            max_tokens=16_384,
        )
        plan = jsongen.validate_final_json(
            jsongen.generate_json(mdparse.parse_markdown(canonical))
        )
        self.assertEqual(len(plan["shots"]), 29)
        for shot in plan["shots"]:
            prompt = "\n".join(shot["prompt"])
            self.assertNotRegex(prompt, r"<Audio [0-9]+>")
            self.assertIn("locked Source Timeline", prompt)
