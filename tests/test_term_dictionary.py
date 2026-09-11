from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from .helpers import module


dictionary = module("node_japanese_to_json.compiler.term_dictionary")
TranslationError = module("node_japanese_to_json.compiler.errors").TranslationError


class PromptTermDictionaryTests(unittest.TestCase):
    def test_bundled_dictionary_normalizes_compounds_before_short_terms(self) -> None:
        entries = dictionary.load_prompt_term_entries(
            (dictionary.BUNDLED_DICTIONARY_PATH,)
        )
        normalized, replaced = dictionary.normalize_prompt_terms(
            "Ink crosses the entire画面 and exits 画面外.",
            entries=entries,
        )
        self.assertEqual(
            normalized,
            "Ink crosses the entire frame and exits outside the frame.",
        )
        self.assertEqual(replaced, ("画面外", "画面"))

    def test_later_dictionary_overrides_bundled_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "prompt_terms.csv"
            override.write_text(
                "source,target\n画面,video frame\n独自語,custom term\n",
                encoding="utf-8",
            )
            entries = dictionary.load_prompt_term_entries(
                (dictionary.BUNDLED_DICTIONARY_PATH, override)
            )
            normalized, _ = dictionary.normalize_prompt_terms(
                "画面と独自語", entries=entries
            )
        self.assertEqual(normalized, "video frameとcustom term")

    def test_utf8_bom_and_comment_rows_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt_terms.csv"
            path.write_text(
                "\ufeffsource,target\n# comment,ignored\n独自語,custom term\n",
                encoding="utf-8",
            )
            entries = dictionary.load_prompt_term_entries((path,))
        self.assertEqual(entries, (("独自語", "custom term"),))

    def test_duplicate_source_in_one_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt_terms.csv"
            path.write_text(
                "source,target\n画面,frame\n画面,screen\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TranslationError, "duplicate source"):
                dictionary.load_prompt_term_entries((path,))

    def test_japanese_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt_terms.csv"
            path.write_text(
                "source,target\n画面,まだ日本語\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TranslationError, "target must not contain"):
                dictionary.load_prompt_term_entries((path,))

    def test_invalid_header_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt_terms.csv"
            path.write_text("japanese,english\n画面,frame\n", encoding="utf-8")
            with self.assertRaisesRegex(TranslationError, "header source,target"):
                dictionary.load_prompt_term_entries((path,))

    def test_default_dictionary_reloads_after_file_change(self) -> None:
        old_entries = dictionary._CACHE_ENTRIES
        old_signature = dictionary._CACHE_SIGNATURE
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "prompt_terms.csv"
                path.write_text("source,target\n独自語,first\n", encoding="utf-8")
                with (
                    mock.patch.object(dictionary, "BUNDLED_DICTIONARY_PATH", path),
                    mock.patch.object(
                        dictionary,
                        "prompt_term_dictionary_paths",
                        return_value=(path,),
                    ),
                ):
                    dictionary._CACHE_ENTRIES = None
                    dictionary._CACHE_SIGNATURE = None
                    first, _ = dictionary.normalize_prompt_terms("独自語")
                    path.write_text(
                        "source,target\n独自語,second value\n", encoding="utf-8"
                    )
                    second, _ = dictionary.normalize_prompt_terms("独自語")
            self.assertEqual(first, "first")
            self.assertEqual(second, "second value")
        finally:
            dictionary._CACHE_ENTRIES = old_entries
            dictionary._CACHE_SIGNATURE = old_signature
