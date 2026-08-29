from __future__ import annotations

import unittest

from .helpers import module


parse_markdown = module("compiler.mdparse").parse_markdown


CANONICAL = """# Subjects
* a person.

# Common
* Common setting.

# Scene 8sec
* Action one.

# Scene 5sec CONTINUE
* Action two."""


class MarkdownParserTests(unittest.TestCase):
    def test_lf_crlf_and_missing_final_newline_are_equivalent(self) -> None:
        lf = parse_markdown(CANONICAL)
        crlf = parse_markdown(CANONICAL.replace("\n", "\r\n"))
        self.assertEqual(lf, crlf)
        self.assertEqual(len(lf.scenes), 2)

    def test_blank_runs_and_directive_without_blank_transition(self) -> None:
        text = "# Subjects\n* one.\n\n\n\n# Common\n* common.\n# Scene\n* shot."
        emd = parse_markdown(text)
        self.assertEqual(emd.subjects, ["one."])
        self.assertEqual(emd.common_prompt, ["common."])
        self.assertEqual(emd.scenes[0].shots, ["shot."])

    def test_repeated_sections_append(self) -> None:
        text = (
            "# Common\n* a\n# Subjects\n* s1.\n# Scene\n* x\n"
            "# Subjects\n* s2.\n# Common\n* b\n# Scene 2sec CONTINUE\n* y"
        )
        emd = parse_markdown(text)
        self.assertEqual(emd.common_prompt, ["a", "b"])
        self.assertEqual(emd.subjects, ["s1.", "s2."])
        self.assertEqual([scene.duration for scene in emd.scenes], [5, 2])
        self.assertTrue(emd.scenes[1].is_continue)

    def test_scene_one_continue_is_reset(self) -> None:
        with self.assertLogs("cl_japanese2json", level="WARNING"):
            emd = parse_markdown("# Scene CONTINUE\n* x")
        self.assertFalse(emd.scenes[0].is_continue)

    def test_external_scene_one_continue_is_retained(self) -> None:
        emd = parse_markdown(
            "# Scene CONTINUE\n* x", external_first_context=True
        )
        self.assertTrue(emd.scenes[0].is_continue)

    def test_defensive_invalid_durations_fall_back(self) -> None:
        for value in ("0", "61", "abc"):
            with self.subTest(value=value), self.assertLogs(
                "cl_japanese2json", level="WARNING"
            ):
                emd = parse_markdown(f"# Scene {value}sec\n* x")
            self.assertEqual(emd.scenes[0].duration, 5)

    def test_payload_is_not_modified(self) -> None:
        body = r"<d>[Japanese]\<x\></d> punctuation  "
        emd = parse_markdown(f"# Scene\n* {body}")
        self.assertEqual(emd.scenes[0].shots[0], body)

    def test_fifth_subject_and_unknown_lines_warn_but_continue(self) -> None:
        text = (
            "# Subjects\n* 1.\n* 2.\n* 3.\n* 4.\n* 5.\n"
            "not a bullet\n# Unknown\nignored\n# Scene\n* shot"
        )
        with self.assertLogs("cl_japanese2json", level="WARNING"):
            emd = parse_markdown(text)
        self.assertEqual(len(emd.subjects), 5)
        self.assertEqual(len(emd.scenes), 1)
