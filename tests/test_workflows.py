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
                self.assertNotIn("# 共通プロンプト", source)
                self.assertIn("## ショット", source)
                for line in source.splitlines():
                    if "「" in line:
                        self.assertRegex(
                            line,
                            r"<Subject [1-9][0-9]*> \(S[1-9][0-9]*\).*「",
                        )

                canonical = llmj2e.translate_markdown(
                    source,
                    FakeLLM(n_ctx=1_000_000),
                    "system",
                    max_tokens=16_384,
                )
                plan = jsongen.validate_final_json(
                    jsongen.generate_json(mdparse.parse_markdown(canonical))
                )
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
                        for line in value.splitlines():
                            if line.startswith("# シーン"):
                                self.assertNotIn("秒生成する", line)
                                self.assertNotIn("継続する", line)
