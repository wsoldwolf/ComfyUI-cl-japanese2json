from __future__ import annotations

import unittest

from .helpers import PKG, ROOT, module


parser = module("node_string_combo.parser")
node_mod = module("node_string_combo.node")
errors = module("node_string_combo.errors")


class StringComboTests(unittest.TestCase):
    def test_registration_and_ui_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLStringCombo"]
        self.assertIs(cls, node_mod.CLStringCombo)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLStringCombo"],
            "CL String Combo",
        )
        self.assertEqual(cls.RETURN_TYPES, ("STRING",))
        self.assertEqual(cls.RETURN_NAMES, ("selected_string",))
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Prompt Tools")
        required = cls.INPUT_TYPES()["required"]
        self.assertEqual(list(required), ["string_list", "selected_value"])
        self.assertEqual(required["string_list"][0], "STRING")
        self.assertFalse(required["string_list"][1]["multiline"])
        self.assertEqual(required["selected_value"][0], "STRING")

    def test_plain_and_escaped_lists(self) -> None:
        cases = {
            "foo|bar|baz": ("foo", "bar", "baz"),
            "foo|bar||baz|qux": ("foo", "bar|baz", "qux"),
            "a|||b": ("a|", "b"),
            "a||||b": ("a||b",),
            "||": ("|",),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(parser.parse_string_list(source), expected)

    def test_outer_whitespace_is_trimmed_and_case_is_preserved(self) -> None:
        self.assertEqual(
            parser.parse_string_list("  Alpha  | beta value |GAMMA "),
            ("Alpha", "beta value", "GAMMA"),
        )

    def test_invalid_lists_are_rejected(self) -> None:
        for source in ("", "|foo", "foo|", "foo|   |bar", "foo\nbar"):
            with self.subTest(source=source), self.assertRaises(
                errors.StringComboError
            ):
                parser.parse_string_list(source)
        with self.assertRaisesRegex(errors.StringComboError, "duplicate"):
            parser.parse_string_list("foo| foo ")

    def test_resolve_selection_and_empty_initial_value(self) -> None:
        self.assertEqual(
            parser.resolve_selected_string("foo|bar||baz", "bar|baz"),
            ("bar|baz", 2, 2),
        )
        self.assertEqual(
            parser.resolve_selected_string("first|second", ""),
            ("first", 1, 2),
        )
        with self.assertRaisesRegex(errors.StringComboError, "not present"):
            parser.resolve_selected_string("first|second", "stale")

    def test_validate_inputs_reports_user_friendly_error(self) -> None:
        self.assertIs(node_mod.CLStringCombo.VALIDATE_INPUTS("a|b", "b"), True)
        validation = node_mod.CLStringCombo.VALIDATE_INPUTS("a|b", "missing")
        self.assertIsInstance(validation, str)
        self.assertIn("not present", validation)

    def test_node_returns_exact_value_and_logs_cyan_success(self) -> None:
        with self.assertLogs("cl_string_combo", level="INFO") as captured:
            result = node_mod.CLStringCombo.select_string(
                " first | second||part ",
                "second|part",
            )
        self.assertEqual(result, ("second|part",))
        logs = "\n".join(captured.output)
        self.assertIn("\x1b[96m", logs)
        self.assertIn("[cl_string_combo] success: selected item 2/2", logs)
        self.assertNotIn("second|part", logs)

    def test_frontend_converts_serialized_string_widget_to_dynamic_combo(self) -> None:
        source = (ROOT / "web" / "cl_string_combo.js").read_text(encoding="utf-8")
        self.assertIn('const NODE_NAME = "CLStringCombo"', source)
        self.assertIn('const LIST_PROPERTY = "string_list"', source)
        self.assertIn(
            'node.addProperty(LIST_PROPERTY, current, "string")',
            source,
        )
        self.assertIn("hideSerializedWidget(widget(this, LIST_PROPERTY))", source)
        self.assertIn("syncPropertyToTransport", source)
        self.assertIn('node.addWidget(', source)
        self.assertIn('"combo",', source)
        self.assertIn("replaceSelectedWidgetWithCombo", source)
        self.assertIn("node.widgets.splice(existingIndex, 1)", source)
        self.assertIn("widgets_values_named?.selected_value", source)
        self.assertIn("selectedWidget.options.values = values", source)
        self.assertIn('value[index + 1] === "|"', source)

    def test_spec_requires_right_click_property_and_hidden_transport(self) -> None:
        source = (ROOT / "docs" / "cl_string_combo_spec.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("右クリック→プロパティーパネル", source)
        self.assertIn("ノード表面には`selected_value`コンボだけ", source)
        self.assertIn("非表示transport", source)


if __name__ == "__main__":
    unittest.main()
