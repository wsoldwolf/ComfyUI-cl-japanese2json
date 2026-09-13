from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from .helpers import PKG, ROOT, module


seed_mod = module("node_seed32.node")
errors = module("node_seed32.errors")


class Seed32Tests(unittest.TestCase):
    def test_registration_and_schema(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLSeed32"]
        self.assertIs(cls, seed_mod.CLSeed32)
        self.assertEqual(PKG.NODE_DISPLAY_NAME_MAPPINGS["CLSeed32"], "CL 32-bit Seed")
        self.assertEqual(cls.RETURN_TYPES, ("INT",))
        self.assertEqual(cls.RETURN_NAMES, ("seed",))
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Utilities")
        required = cls.INPUT_TYPES()["required"]
        self.assertEqual(list(required), ["seed", "mode", "hold_next"])
        self.assertEqual(required["seed"][1]["default"], -1)
        self.assertEqual(required["seed"][1]["min"], -1)
        self.assertEqual(required["seed"][1]["max"], 2_147_483_647)
        self.assertIs(required["seed"][1]["control_after_generate"], False)
        self.assertEqual(required["mode"][0], ["fixed", "random"])

    def test_fixed_and_one_run_hold_keep_the_current_seed(self) -> None:
        with patch.object(seed_mod, "random_seed32") as randomize:
            self.assertEqual(seed_mod.resolve_seed32(42, "fixed", False), 42)
            self.assertEqual(seed_mod.resolve_seed32(42, "random", True), 42)
        randomize.assert_not_called()

    def test_random_mode_and_minus_one_generate_in_range(self) -> None:
        with patch.object(seed_mod.secrets, "randbelow", return_value=0) as draw:
            self.assertEqual(seed_mod.resolve_seed32(-1, "fixed", False), 1)
        draw.assert_called_once_with(seed_mod.MAX_SEED_32)
        with patch.object(seed_mod, "random_seed32", return_value=seed_mod.MAX_SEED_32):
            self.assertEqual(
                seed_mod.resolve_seed32(99, "random", False), seed_mod.MAX_SEED_32
            )

    def test_invalid_values_fail_without_drawing_entropy(self) -> None:
        invalid = [
            (0, "fixed", False),
            (-2, "fixed", False),
            (seed_mod.MAX_SEED_32 + 1, "fixed", False),
            (True, "fixed", False),
            (1.0, "fixed", False),
            (1, "increment", False),
            (1, "fixed", 1),
        ]
        with patch.object(seed_mod, "random_seed32") as randomize:
            for arguments in invalid:
                with self.subTest(arguments=arguments), self.assertRaises(errors.Seed32Error):
                    seed_mod.resolve_seed32(*arguments)
        randomize.assert_not_called()

    def test_comfy_validation_matches_runtime_contract(self) -> None:
        cls = seed_mod.CLSeed32
        self.assertIs(cls.VALIDATE_INPUTS(-1, "random", False), True)
        self.assertIs(cls.VALIDATE_INPUTS(seed_mod.MAX_SEED_32, "fixed", False), True)
        self.assertIn("must not be 0", cls.VALIDATE_INPUTS(0, "fixed", False))
        self.assertIn("fixed or random", cls.VALIDATE_INPUTS(1, "bad", False))

    def test_cache_policy_reexecutes_only_unheld_random_state(self) -> None:
        cls = seed_mod.CLSeed32
        self.assertTrue(math.isnan(cls.IS_CHANGED(-1, "fixed", False)))
        self.assertTrue(math.isnan(cls.IS_CHANGED(42, "random", False)))
        self.assertEqual(cls.IS_CHANGED(42, "random", True), (42, "random", True))
        self.assertEqual(cls.IS_CHANGED(42, "fixed", False), (42, "fixed", False))

    def test_result_updates_saved_widget_state_after_execution(self) -> None:
        with patch.object(seed_mod, "random_seed32", return_value=123456):
            with self.assertLogs("cl_seed32", level="INFO") as captured:
                result = seed_mod.CLSeed32.generate_seed(10, "random", False)
        self.assertEqual(result["result"], (123456,))
        self.assertEqual(result["ui"], {
            "seed": [123456], "mode": ["random"], "hold_next": [False]
        })
        self.assertIn("[cl_seed32] success: using seed 123456 (random)", "\n".join(captured.output))

    def test_frontend_has_persistent_state_and_three_controls(self) -> None:
        source = (ROOT / "web" / "cl_seed32.js").read_text(encoding="utf-8")
        self.assertIn('const NODE_NAME = "CLSeed32"', source)
        self.assertIn('addButton(this, "fixed"', source)
        self.assertIn('addButton(this, "reuse"', source)
        self.assertIn('addButton(this, "random"', source)
        self.assertIn('setWidget(node, "hold_next", true)', source)
        self.assertIn('setWidget(this, "hold_next", false)', source)
        self.assertIn("globalThis.crypto?.getRandomValues", source)
        self.assertIn("message?.seed", source)
        self.assertIn("serialize: false", source)


if __name__ == "__main__":
    unittest.main()
