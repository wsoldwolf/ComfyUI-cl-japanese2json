from __future__ import annotations

import json
import unittest

from .helpers import PKG, ROOT, module


parser = module("node_connected_combo.parser")
node_mod = module("node_connected_combo.node")
errors = module("node_connected_combo.errors")
planner_node = module("node_mv_prompt_planner.node")
enhancer_node = module("node_prompt_enhancer.node")


class ConnectedComboTests(unittest.TestCase):
    def test_registration_and_ui_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLConnectedCombo"]
        self.assertIs(cls, node_mod.CLConnectedCombo)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLConnectedCombo"],
            "CL Connected Combo",
        )
        self.assertEqual(cls.RETURN_TYPES, ("STRING",))
        self.assertEqual(cls.RETURN_NAMES, ("selected_string",))
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Prompt Tools")
        required = cls.INPUT_TYPES()["required"]
        self.assertEqual(list(required), ["enum_values_json", "selected_value"])
        self.assertEqual(required["enum_values_json"][1]["default"], "[]")
        self.assertFalse(required["enum_values_json"][1]["multiline"])
        self.assertEqual(required["selected_value"][0], "STRING")

    def test_json_transport_preserves_exact_string_values(self) -> None:
        source = json.dumps(
            ["lyric_visuals_light_8b", "path/with|pipe.gguf", "日本語"],
            ensure_ascii=False,
        )
        self.assertEqual(
            parser.parse_enum_values_json(source),
            ("lyric_visuals_light_8b", "path/with|pipe.gguf", "日本語"),
        )

    def test_invalid_enum_transport_is_rejected(self) -> None:
        for source in ("", "{}", "[]", '["a", "a"]', '["a", 2]', '[""]'):
            with self.subTest(source=source), self.assertRaises(
                (errors.ConnectedComboError, ValueError)
            ):
                parser.parse_enum_values_json(source)

    def test_selection_resolution_and_validation(self) -> None:
        source = '["first", "second"]'
        self.assertEqual(
            parser.resolve_connected_selection(source, "second"),
            ("second", 2, 2),
        )
        self.assertEqual(
            parser.resolve_connected_selection(source, ""),
            ("first", 1, 2),
        )
        self.assertIs(node_mod.CLConnectedCombo.VALIDATE_INPUTS(source, "first"), True)
        validation = node_mod.CLConnectedCombo.VALIDATE_INPUTS(source, "missing")
        self.assertIsInstance(validation, str)
        self.assertIn("not present", validation)

    def test_node_returns_exact_value_and_logs_cyan_success(self) -> None:
        with self.assertLogs("cl_connected_combo", level="INFO") as captured:
            result = node_mod.CLConnectedCombo.select_string(
                '["first", "second"]',
                "second",
            )
        self.assertEqual(result, ("second",))
        logs = "\n".join(captured.output)
        self.assertIn("\x1b[96m", logs)
        self.assertIn(
            "[cl_connected_combo] success: selected connected item 2/2",
            logs,
        )
        self.assertNotIn("second", logs)

    def test_override_inputs_declare_their_combo_sources(self) -> None:
        planner = planner_node.CLMVPromptPlannerGGUF.INPUT_TYPES()["optional"]
        self.assertEqual(
            planner["model_name_override"][1]["connected_combo_source"],
            "model_name",
        )
        self.assertEqual(
            planner["visual_enrichment_profile_override"][1][
                "connected_combo_source"
            ],
            "visual_enrichment_profile",
        )

        enhancer = enhancer_node.CLPromptEnhancerGGUF.INPUT_TYPES()["optional"]
        self.assertEqual(
            enhancer["model_name_override"][1]["connected_combo_source"],
            "model_name",
        )
        self.assertEqual(
            enhancer["style_profile_override"][1]["connected_combo_source"],
            "style_profile",
        )
        self.assertEqual(
            enhancer["background_detail_override"][1]["connected_combo_source"],
            "background_detail",
        )

    def test_frontend_discovers_direct_alias_and_subgraph_enums(self) -> None:
        source = (ROOT / "web" / "cl_connected_combo.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const NODE_NAME = "CLConnectedCombo"', source)
        self.assertIn("connected_combo_source", source)
        self.assertIn("enumAtDestination", source)
        self.assertIn("resolveSubgraphInputLinks", source)
        self.assertIn("findSubgraphHosts(app.rootGraph, graph)", source)
        self.assertIn("graph?.outputNode === targetNode", source)
        self.assertIn('typeName.includes("reroute")', source)
        self.assertIn("REFRESH_INTERVAL_MS", source)
        self.assertIn("downstream COMBO inputs expose incompatible", source)
        self.assertIn("hideSerializedWidget(widget(this, ENUM_TRANSPORT))", source)
        self.assertIn('"combo",', source)

    def test_spec_keeps_manual_and_connected_nodes_separate(self) -> None:
        source = (ROOT / "docs" / "cl_connected_combo_spec.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`CL String Combo`はユーザー", source)
        self.assertIn("変更しない", source)
        self.assertIn("サブグラフ", source)
        self.assertIn("connected_combo_source", source)
        self.assertIn("複数", source)


if __name__ == "__main__":
    unittest.main()
