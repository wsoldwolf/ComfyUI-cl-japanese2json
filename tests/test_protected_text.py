from __future__ import annotations

import unittest

from .helpers import module


protected = module("compiler.protected_text")
ProtectedTextError = module("compiler.errors").ProtectedTextError


class ProtectedTextTests(unittest.TestCase):
    def test_all_valid_references_restore_exactly(self) -> None:
        source = (
            "<Picture 1> <Picture 9> <Video 1> <Video 9> "
            "<Audio 1> <Audio 3> <Subject 1> <Subject 4>"
        )
        payload = protected.protect_text(source)
        self.assertNotIn("<Picture", payload.text)
        self.assertEqual(protected.restore_text(payload, payload.text), source)

    def test_speaker_ids_are_protected_and_restored_exactly(self) -> None:
        source = "<Subject 1> (S1)が「こんにちは」と言う。"
        payload = protected.protect_text(source)
        self.assertNotIn("(S1)", payload.text)
        self.assertIn("<Subject 1> (S1)", payload.replacements.values())
        self.assertEqual(
            protected.restore_text(payload, payload.text),
            "<Subject 1> (S1)が<d>[Japanese]こんにちは</d>と言う。",
        )

    def test_subject_speaker_pair_is_one_placeholder_with_or_without_space(self) -> None:
        for pair in ("<Subject 1> (S1)", "<Subject 1>(S1)"):
            with self.subTest(pair=pair):
                payload = protected.protect_text(f"{pair}が話す。")
                self.assertEqual(tuple(payload.replacements.values()), (pair,))
                self.assertEqual(protected.restore_text(payload, payload.text), f"{pair}が話す。")

    def test_out_of_range_reference_is_still_protected(self) -> None:
        with self.assertLogs("cl_japanese2json", level="WARNING"):
            payload = protected.protect_text("<Subject 5>")
        self.assertEqual(protected.restore_text(payload, payload.text), "<Subject 5>")

    def test_compact_reference_warns_and_is_preserved(self) -> None:
        source = "<Picture1> <Audio2> <Subject3>"
        with self.assertLogs("cl_japanese2json", level="WARNING") as captured:
            payload = protected.protect_text(source)
        self.assertEqual(protected.restore_text(payload, payload.text), source)
        self.assertEqual(len(captured.records), 3)
        self.assertIn("missing the required ASCII space", captured.output[0])

    def test_source_text_that_looks_like_an_internal_token_does_not_collide(self) -> None:
        literal = "CLJ0C0P999X"
        payload = protected.protect_text(f"{literal} <Subject 1>")
        self.assertIn(literal, payload.text)
        self.assertEqual(
            protected.restore_text(payload, payload.text),
            f"{literal} <Subject 1>",
        )

    def test_existing_direct_speech_is_byte_preserved(self) -> None:
        source = "before <d>[Japanese]「そのまま」 [x]</d> after"
        payload = protected.protect_text(source)
        self.assertEqual(protected.restore_text(payload, payload.text), source)

    def test_multiple_dialogues_are_converted(self) -> None:
        payload = protected.protect_text("「一つ」から「二つ」")
        restored = protected.restore_text(payload, payload.text)
        self.assertEqual(
            restored,
            "<d>[Japanese]一つ</d>から<d>[Japanese]二つ</d>",
        )

    def test_dialogue_metacharacters_are_escaped_once(self) -> None:
        payload = protected.protect_text(r"「< > [ ] \< \> \[ \]」")
        restored = protected.restore_text(payload, payload.text)
        self.assertEqual(
            restored,
            r"<d>[Japanese]\< \> \[ \] \< \> \[ \]</d>",
        )

    def test_malformed_corner_brackets_fail(self) -> None:
        for value in ("「unclosed", "extra」", "「nested「x」"):
            with self.subTest(value=value), self.assertRaises(ProtectedTextError):
                protected.protect_text(value)

    def test_malformed_direct_speech_fails(self) -> None:
        for value in ("<d>open", "close</d>", "<d>one<d>two</d></d>"):
            with self.subTest(value=value), self.assertRaises(ProtectedTextError):
                protected.protect_text(value)

    def test_japanese_scan_ignores_only_direct_speech(self) -> None:
        self.assertFalse(
            protected.contains_unprotected_japanese("say <d>[Japanese]こんにちは</d>")
        )
        self.assertTrue(protected.contains_unprotected_japanese("outside 日本語"))
