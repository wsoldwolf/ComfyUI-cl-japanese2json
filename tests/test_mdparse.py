from __future__ import annotations

import unittest

from .helpers import module


parse_markdown = module("compiler.mdparse").parse_markdown
errors = module("compiler.errors")


CANONICAL = """# Subjects
* a person from <Picture 1>.

# Retention
* <Subject 1> fully_preserved: Preserve the defined appearance.

# Common
* A clean global visual style.
* Keep <Subject 1> sharply rendered.

# Scene 8sec
* A clean anime style.

## Shot
* <Subject 1> acts.

## Shot 3.25sec
* <Subject 1> stops.

## Soundscape
* Environment: Soft wind.
* Vocalization: NONE
* Background music: Sparse piano at a slow tempo.

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
        self.assertEqual(
            emd.common_prompt,
            ["A clean global visual style.", "Keep <Subject 1> sharply rendered."],
        )
        self.assertEqual(emd.scenes[0].preamble, ["A clean anime style."])
        self.assertEqual([shot.start_ms for shot in emd.scenes[0].shots], [0, 3250])
        self.assertEqual(emd.scenes[0].shots[0].lines, ["<Subject 1> acts."])
        self.assertEqual(
            emd.scenes[0].soundscape.background_music,
            "Sparse piano at a slow tempo.",
        )

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
            "* Vocalization: NONE\n"
            "* Background music: Sparse piano."
        )
        soundscape = emd.scenes[0].soundscape
        self.assertEqual(soundscape.environment, "Soft wind.")
        self.assertEqual(soundscape.sound_effects, "Footsteps.")
        self.assertEqual(soundscape.vocalization, "NONE")
        self.assertEqual(soundscape.background_music, "Sparse piano.")

    def test_background_music_reuse_is_stored_as_structured_scene_data(self) -> None:
        emd = parse_markdown(
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 2> partially_copy "
            "00:10.000-00:15.000"
        )
        reuse = emd.scenes[0].soundscape.background_music_reuse
        self.assertIsNotNone(reuse)
        self.assertEqual(reuse.audio_number, 2)
        self.assertEqual(reuse.relationship, "partially_copy")
        self.assertEqual(reuse.source_start_ms, 10_000)
        self.assertEqual(reuse.source_end_ms, 15_000)
        self.assertIsNone(emd.scenes[0].soundscape.background_music)

    def test_invalid_or_conflicting_background_music_reuse_is_rejected(self) -> None:
        invalid_documents = (
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 4> fully_copy",
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> reference",
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> fully_copy "
            "00:00.000-00:05.000",
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> partially_copy "
            "00:05.000-00:05.000",
            "# Scene 5sec\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> partially_copy "
            "00:00.000-00:04.000",
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music: Piano.\n"
            "* Background music reuse: <Audio 1> partially_copy",
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> partially_copy\n"
            "* Background music: Piano.",
        )
        for text in invalid_documents:
            with self.subTest(text=text), self.assertRaises(
                errors.MarkdownParseError
            ):
                parse_markdown(text)

    def test_canonical_lip_sync_is_preserved_in_shot_order(self) -> None:
        emd = parse_markdown(
            "# Subjects\n* one.\n# Scene\n## Shot\n"
            "* Before.\n"
            "* Lip sync: <Subject 1> <- <Audio 1>: <d>[Japanese]こんにちは</d>\n"
            "* After.\n"
            "## Soundscape\n* Vocalization: EXPLICIT_DIALOGUE_ONLY"
        )
        self.assertEqual(
            emd.scenes[0].shots[0].lines,
            [
                "Before.",
                "Lip sync: <Subject 1> <- <Audio 1>: <d>[Japanese]こんにちは</d>",
                "After.",
            ],
        )

    def test_invalid_canonical_lip_sync_is_rejected(self) -> None:
        invalid_lines = (
            "Lip sync: <Subject 1> <- <Audio 1>: missing dialogue",
            "Lip sync: <Subject 1> <- <Audio 1>: <d>[Japanese]</d>",
            "Lip sync: <Subject 1> <- <Audio 1>: <d>one</d> <d>two</d>",
        )
        for line in invalid_lines:
            with self.subTest(line=line), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(f"# Scene\n## Shot\n* {line}")

    def test_empty_soundscape_is_rejected(self) -> None:
        with self.assertRaises(errors.MarkdownParseError):
            parse_markdown("# Scene\n## Shot\n* Action.\n## Soundscape")

    def test_implicit_shot_syntax_is_rejected(self) -> None:
        invalid = (
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
            "# Common\n* one.\n# Common\n* two.\n# Scene\n## Shot\n* x.",
            "# Common\n* global.\n# Retention\n* <Subject 1> fully_preserved: x.\n# Scene\n## Shot\n* x.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(text)

    def test_common_restrictions_and_empty_section_are_rejected(self) -> None:
        invalid = (
            "# Common\n# Scene\n## Shot\n* Action.",
            "# Common\n* Use <Audio 4>.\n# Scene\n## Shot\n* Action.",
            "# Common\n* Use <Audio 01>.\n# Scene\n## Shot\n* Action.",
            "# Common\n* Someone says <d>[Japanese]x</d>.\n# Scene\n## Shot\n* Action.",
            "# Scene\n## Shot\n* <Subject 1> (S1) says <d>[Japanese]x</d>.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(errors.MarkdownParseError):
                parse_markdown(text)

    def test_common_accepts_canonical_audio_references(self) -> None:
        emd = parse_markdown(
            "# Common\n* Keep <Audio 1> continuous.\n"
            "# Scene\n## Shot\n* Action.\n## Soundscape\n"
            "* Background music reuse: <Audio 1> partially_copy"
        )
        self.assertEqual(emd.common_prompt, ["Keep <Audio 1> continuous."])
