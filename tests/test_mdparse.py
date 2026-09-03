from __future__ import annotations

import unittest

from .helpers import module


parse_markdown = module("compiler.mdparse").parse_markdown
errors = module("compiler.errors")


CANONICAL = """# Subjects
* a person from <Picture 1>.

# Retention
* <Subject 1> fully_preserved: Preserve the defined appearance.

# Scene 8sec
* A clean anime style.

## Shot
* <Subject 1> acts.

## Shot 3.25sec
* <Subject 1> stops.

## Soundscape
* Environment: Soft wind.
* Vocalization: NONE

# Scene 5sec CONTINUE
## Shot
* An effect fades."""


class MarkdownParserTests(unittest.TestCase):
    def test_lf_crlf_and_missing_final_newline_are_equivalent(self) -> None:
        lf = parse_markdown(CANONICAL)
        crlf = parse_markdown(CANONICAL.replace("\n", "\r\n"))
        self.assertEqual(lf, crlf)
        self.assertEqual(len(lf.scenes), 2)

    def test_new_structure_is_stored_without_modifying_payloads(self) -> None:
        emd = parse_markdown(CANONICAL)
        self.assertEqual(emd.subjects, ["a person from <Picture 1>."])
        self.assertEqual(len(emd.retention_rules), 1)
        rule = emd.retention_rules[0]
        self.assertEqual(rule.subject_number, 1)
        self.assertEqual(rule.relationship, "fully_preserved")
        self.assertEqual(emd.scenes[0].preamble, ["A clean anime style."])
        self.assertEqual([shot.start_ms for shot in emd.scenes[0].shots], [0, 3250])
        self.assertEqual(emd.scenes[0].shots[0].lines, ["<Subject 1> acts."])

    def test_attribute_transfer_requires_a_different_target(self) -> None:
        valid = parse_markdown(
            "# Subjects\n* one.\n* two.\n# Retention\n"
            "* <Subject 1> attribute_transfer -> <Subject 2>: Transfer style.\n"
            "# Scene\n## Shot\n* <Subject 1> affects <Subject 2>."
        )
        self.assertEqual(valid.retention_rules[0].target_subject_number, 2)
        for line in (
            "* <Subject 1> attribute_transfer: Missing target.",
            "* <Subject 1> attribute_transfer -> <Subject 1>: Same target.",
            "* <Subject 1> fully_preserved -> <Subject 2>: Invalid target.",
        ):
            with self.subTest(line=line), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(
                    f"# Subjects\n* one.\n* two.\n# Retention\n{line}\n"
                    "# Scene\n## Shot\n* action."
                )

    def test_scene_one_continue_is_reset_unless_external_context_exists(self) -> None:
        source = "# Scene CONTINUE\n## Shot\n* action."
        with self.assertLogs("cl_japanese2json", level="WARNING"):
            local = parse_markdown(source)
        external = parse_markdown(source, external_first_context=True)
        self.assertFalse(local.scenes[0].is_continue)
        self.assertTrue(external.scenes[0].is_continue)

    def test_defensive_invalid_durations_fall_back(self) -> None:
        for value in ("0", "61", "abc"):
            with self.subTest(value=value), self.assertLogs(
                "cl_japanese2json", level="WARNING"
            ):
                emd = parse_markdown(f"# Scene {value}sec\n## Shot\n* action.")
            self.assertEqual(emd.scenes[0].duration, 5)

    def test_shot_times_are_strict_and_within_scene(self) -> None:
        invalid = (
            "# Scene 5sec\n## Shot 1sec\n* action.",
            "# Scene 5sec\n## Shot\n* a.\n## Shot\n* b.",
            "# Scene 5sec\n## Shot\n* a.\n## Shot 5sec\n* b.",
            "# Scene 5sec\n## Shot\n* a.\n## Shot 3sec\n* b.\n## Shot 2sec\n* c.",
            "# Scene 5sec\n## Shot\n## Soundscape\n* Environment: Wind.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(text)

    def test_soundscape_is_scene_local_and_follows_shots(self) -> None:
        emd = parse_markdown(
            "# Scene 5sec\n## Shot\n* Action.\n\n## Soundscape\n"
            "* Environment: Soft wind.\n"
            "* Sound effects: Footsteps.\n"
            "* Vocalization: NONE"
        )
        soundscape = emd.scenes[0].soundscape
        self.assertEqual(soundscape.environment, "Soft wind.")
        self.assertEqual(soundscape.sound_effects, "Footsteps.")
        self.assertEqual(soundscape.vocalization, "NONE")

    def test_empty_soundscape_is_rejected(self) -> None:
        with self.assertRaises(errors.MarkdownParseError):
            parse_markdown("# Scene\n## Shot\n* Action.\n## Soundscape")

    def test_removed_common_and_implicit_shot_syntax_are_rejected(self) -> None:
        invalid = (
            "# Common\n* setting.\n# Scene\n## Shot\n* action.",
            "# Scene\n* former implicit shot.",
            "# Scene\n## Soundscape\n* Environment: Wind.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(text)

    def test_duplicate_or_misordered_top_level_sections_are_rejected(self) -> None:
        invalid = (
            "# Subjects\n* one.\n# Subjects\n* two.\n# Scene\n## Shot\n* x.",
            "# Scene\n## Shot\n* x.\n# Retention\n* <Subject 1> fully_preserved: x.",
            "# Retention\n* <Subject 1> fully_preserved: x.\n# Retention\n* <Subject 2> fully_preserved: y.\n# Scene\n## Shot\n* x.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(text)
