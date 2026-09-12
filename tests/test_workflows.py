from __future__ import annotations

import json
import unittest

from .helpers import FakeLLM, ROOT, module


llmj2e = module("node_japanese_to_json.compiler.llmj2e")
mdparse = module("node_japanese_to_json.compiler.mdparse")
jsongen = module("node_japanese_to_json.compiler.jsongen")


def _workflow_graphs(workflow: dict) -> list[dict]:
    """Return the root graph and every embedded ComfyUI subgraph."""

    graphs = [workflow]
    definitions = workflow.get("definitions", {})
    if isinstance(definitions, dict):
        graphs.extend(definitions.get("subgraphs", []))
    graphs.extend(workflow.get("subgraphs", []))
    return graphs


def _link_fields(link: object) -> list[object]:
    if isinstance(link, list):
        return link[:6]
    if isinstance(link, dict):
        return [
            int(link["id"]),
            int(link["origin_id"]),
            int(link["origin_slot"]),
            int(link["target_id"]),
            int(link["target_slot"]),
            link.get("type"),
        ]
    raise AssertionError(f"Unsupported workflow link record: {type(link).__name__}")


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_bundled_workflows_use_current_node_inputs_and_scene_syntax(self) -> None:
        paths = sorted((ROOT / "workflows").rglob("*.json"))
        self.assertTrue(paths)

        for path in paths:
            with self.subTest(workflow=path.name):
                workflow = json.loads(path.read_text(encoding="utf-8"))
                serialized = json.dumps(workflow, ensure_ascii=False)
                all_nodes = [
                    node
                    for graph in _workflow_graphs(workflow)
                    for node in graph.get("nodes", [])
                ]
                self.assertNotRegex(
                    serialized,
                    r"<Video (?:[4-9]|[1-9][0-9]+)>",
                )
                self.assertNotIn("<Video 1>～<Video 9>", serialized)
                compiler_nodes = [
                    node
                    for node in all_nodes
                    if node.get("type") == "CLJapaneseToJSONGGUF"
                ]
                planner_nodes = [
                    node
                    for node in all_nodes
                    if node.get("type") == "CLMVPromptPlannerGGUF"
                ]
                vocal_nodes = [
                    node
                    for node in all_nodes
                    if node.get("type") == "CLVocalToPromptSegments"
                ]
                limiter_nodes = [
                    node
                    for node in all_nodes
                    if node.get("type") == "CLSceneLimiter"
                ]
                if not compiler_nodes and not vocal_nodes:
                    # Utility-only workflows (for example the Vision analyzer
                    # smoke-test workflow) do not contain either pipeline.
                    continue
                if not compiler_nodes:
                    self.assertEqual(len(vocal_nodes), 1)
                    vocal = vocal_nodes[0]
                    input_names = [item["name"] for item in vocal["inputs"]]
                    self.assertIn("lyrics_neighbor_threshold", input_names)
                    self.assertIn("condition_on_previous_text", input_names)
                    self.assertIn("srt_time_offset", input_names)
                    self.assertIn("include_lyrics_comments", input_names)
                    self.assertTrue(
                        vocal["widgets_values_named"][
                            "condition_on_previous_text"
                        ]
                    )
                    self.assertEqual(
                        vocal["widgets_values_named"]["srt_time_offset"], 0
                    )
                    self.assertTrue(
                        vocal["widgets_values_named"]["include_lyrics_comments"]
                    )
                    self.assertFalse(
                        vocal["widgets_values_named"]["keep_whisper_loaded"]
                    )
                    self.assertEqual(
                        vocal["widgets_values_named"]["lyrics_match_threshold"],
                        0.55,
                    )
                    self.assertEqual(
                        vocal["widgets_values_named"]["lyrics_neighbor_threshold"],
                        0.45,
                    )
                    continue
                self.assertEqual(len(compiler_nodes), 1)
                compiler = compiler_nodes[0]
                input_names = [item["name"] for item in compiler["inputs"]]
                self.assertIn("steps", input_names)
                self.assertEqual(compiler["widgets_values_named"]["steps"], 8)
                self.assertEqual(
                    compiler["widgets_values_named"]["retry_max"], 10
                )
                self.assertFalse(
                    compiler["widgets_values_named"]["keep_model_loaded"]
                )
                retry_index = list(compiler["widgets_values_named"]).index(
                    "retry_max"
                )
                self.assertEqual(compiler["widgets_values"][retry_index], 10)
                if "save_debug_output" in input_names:
                    self.assertFalse(
                        compiler["widgets_values_named"]["save_debug_output"]
                    )

                if limiter_nodes:
                    self.assertEqual(len(limiter_nodes), 1)
                    limiter = limiter_nodes[0]
                    self.assertEqual(
                        [item["name"] for item in limiter["inputs"]],
                        [
                            "reduced_markdown",
                            "scene_limit_count",
                            "disable",
                            "scene_start_number",
                        ],
                    )
                    limiter_values = limiter["widgets_values_named"]
                    self.assertEqual(
                        set(limiter_values),
                        {"scene_limit_count", "disable", "scene_start_number"},
                    )
                    self.assertGreaterEqual(limiter_values["scene_limit_count"], 1)
                    self.assertGreaterEqual(limiter_values["scene_start_number"], 1)
                    self.assertIsInstance(limiter_values["disable"], bool)
                    self.assertEqual(
                        limiter["widgets_values"],
                        [
                            limiter_values["scene_limit_count"],
                            limiter_values["disable"],
                            limiter_values["scene_start_number"],
                        ],
                    )

                prompt_nodes = [
                    node
                    for node in all_nodes
                    if node.get("type") == "PrimitiveStringMultiline"
                    and node.get("widgets_values")
                    and isinstance(node["widgets_values"][0], str)
                    and "# サブジェクト" in node["widgets_values"][0]
                ]
                self.assertEqual(len(prompt_nodes), 1)
                source = prompt_nodes[0]["widgets_values"][0]
                self.assertIn("# 共通プロンプト", source)
                if planner_nodes:
                    self.assertEqual(len(planner_nodes), 1)
                    self.assertIn("# 保持分析", source)
                    self.assertNotIn("文字らしい", source)
                else:
                    self.assertIn("## ショット", source)
                self.assertNotRegex(source, r"\(S[1-9][0-9]*\)")
                for line in source.splitlines():
                    if "「" in line:
                        self.assertRegex(
                            line,
                            r"<Subject [1-9][0-9]*>.*「",
                        )

                if not planner_nodes:
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

                for node in all_nodes:
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
        paths = sorted(
            (ROOT / "workflows").rglob("*integrated_mv_generator*.json")
        )
        self.assertTrue(paths)

        for path in paths:
            with self.subTest(workflow=path.name):
                workflow = json.loads(path.read_text(encoding="utf-8"))
                graphs = _workflow_graphs(workflow)
                all_nodes = [
                    node for graph in graphs for node in graph.get("nodes", [])
                ]
                audio_pairs = [
                    node for node in all_nodes
                    if node.get("type") == "CLAudioPadPair"
                ]
                audio_tracks = [
                    node for node in all_nodes
                    if node.get("type") == "MiniMaxH3AudioTracks"
                ]
                chain_starts = [
                    node for node in all_nodes
                    if node.get("type") == "MiniMaxH3ChainLoopStart"
                ]
                self.assertEqual(len(audio_pairs), 1)
                self.assertEqual(len(audio_tracks), 1)
                self.assertEqual(len(chain_starts), 1)
                audio_pair = audio_pairs[0]
                tracks = audio_tracks[0]
                chain_start = chain_starts[0]
                owning_graph = next(
                    graph
                    for graph in graphs
                    if audio_pair in graph.get("nodes", [])
                )
                self.assertIn(tracks, owning_graph.get("nodes", []))
                self.assertIn(chain_start, owning_graph.get("nodes", []))
                links = {
                    fields[0]: fields
                    for fields in (
                        _link_fields(link)
                        for link in owning_graph.get("links", [])
                    )
                }

                self.assertEqual(
                    audio_pair["widgets_values_named"],
                    {
                        "target_duration_seconds": 0.0,
                        "extra_padding_seconds": 0.0,
                        "pad_position": "end",
                        "h3_frame_mode": "auto_safe",
                        "h3_target_frames": 0,
                    },
                )
                self.assertEqual(
                    [item["name"] for item in audio_pair["inputs"]],
                    [
                        "audio_a",
                        "audio_b",
                        "target_duration_seconds",
                        "extra_padding_seconds",
                        "pad_position",
                        "h3_frame_mode",
                        "h3_target_frames",
                    ],
                )
                pair_id = int(audio_pair["id"])
                tracks_id = int(tracks["id"])
                full_mix_link = next(
                    item["link"] for item in tracks["inputs"]
                    if item["name"] == "full_mix"
                )
                vocals_link = next(
                    item["link"] for item in tracks["inputs"]
                    if item["name"] == "vocals"
                )
                self.assertEqual(links[full_mix_link][1:5], [pair_id, 1, tracks_id, 0])
                self.assertEqual(links[vocals_link][1:5], [pair_id, 0, tracks_id, 1])
                timeline_link = next(
                    item["link"] for item in chain_start["inputs"]
                    if item["name"] == "source_timeline"
                )
                self.assertEqual(
                    links[timeline_link][1:5],
                    [tracks_id, 0, int(chain_start["id"]), 3],
                )
