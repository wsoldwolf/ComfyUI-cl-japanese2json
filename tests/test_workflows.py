from __future__ import annotations

import json
import math
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

    def test_bgm_sync_workflow_auto_pads_one_shared_source_track(self) -> None:
        path = ROOT / "workflows" / "minimax_h3_ref2va_20260907_bgm_sync.json"
        workflow = json.loads(path.read_text(encoding="utf-8"))
        nodes = {int(node["id"]): node for node in workflow["nodes"]}
        links = {int(link[0]): link for link in workflow["links"]}

        audio_pad = nodes[2020]
        self.assertEqual(audio_pad["type"], "CLAudioPad")
        self.assertEqual(
            audio_pad["widgets_values_named"],
            {
                "target_duration_seconds": 0.0,
                "extra_padding_seconds": 0.0,
                "pad_position": "end",
            },
        )
        self.assertNotIn(2021, nodes)
        self.assertFalse(
            any(node["type"] in {"EmptyAudio", "AudioConcat"} for node in nodes.values())
        )
        self.assertEqual(links[3571][1:5], [2008, 0, 2020, 0])
        plan_slot = next(
            index
            for index, item in enumerate(audio_pad["inputs"])
            if item["name"] == "plan"
        )
        self.assertEqual(links[3572][1:5], [1700, 0, 2020, plan_slot])
        self.assertEqual(nodes[2008]["outputs"][0]["links"], [3571])
        self.assertIn(3572, nodes[1700]["outputs"][0]["links"])

        padded_destinations = {
            (int(links[link_id][3]), int(links[link_id][4]))
            for link_id in audio_pad["outputs"][0]["links"]
        }
        self.assertEqual(
            padded_destinations,
            {(1701, 1), (1702, 1), (1706, 1)},
        )

        source = nodes[1952]["widgets_values"][0]
        canonical = llmj2e.translate_markdown(
            source,
            FakeLLM(n_ctx=1_000_000),
            "system",
            max_tokens=16_384,
        )
        plan = jsongen.validate_final_json(
            jsongen.generate_json(mdparse.parse_markdown(canonical))
        )
        durations = [shot["duration_seconds"] for shot in plan["shots"]]
        self.assertEqual(durations, [10, 10, 10, 10, 10, 7])

        def h3_frame_length(seconds: int) -> int:
            requested = max(5, int(math.ceil(seconds * 24 - 1e-9)))
            return requested + (5 - requested % 17) % 17

        raw_frames = [h3_frame_length(seconds) for seconds in durations]
        delivered_frames = raw_frames[0] + sum(
            frames - 22 for frames in raw_frames[1:]
        )
        self.assertEqual(raw_frames, [243, 243, 243, 243, 243, 175])
        self.assertEqual(delivered_frames, 1280)
