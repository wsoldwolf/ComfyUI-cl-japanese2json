from __future__ import annotations

import json
import unittest

from .helpers import ROOT


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

                for node in workflow["nodes"]:
                    for value in node.get("widgets_values", []):
                        if not isinstance(value, str):
                            continue
                        for line in value.splitlines():
                            if line.startswith("# シーン"):
                                self.assertNotIn("秒生成する", line)
                                self.assertNotIn("継続する", line)
