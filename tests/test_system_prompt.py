from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from .helpers import module


system_prompt = module("system_prompt")
errors = module("compiler.errors")


class SystemPromptTests(unittest.TestCase):
    def _clear_cache(self) -> None:
        system_prompt._PROMPT_CACHE = None

    def test_prompt_is_loaded_as_utf8_sig_and_cached_by_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prompt.txt"
            path.write_text("\ufefffirst", encoding="utf-8")
            with patch.object(system_prompt, "SYSTEM_PROMPT_PATH", path):
                self._clear_cache()
                self.assertEqual(system_prompt.load_system_prompt(), "first")
                first_fingerprint = system_prompt.system_prompt_fingerprint()
                path.write_text("second and longer", encoding="utf-8")
                self.assertEqual(system_prompt.load_system_prompt(), "second and longer")
                self.assertNotEqual(first_fingerprint, system_prompt.system_prompt_fingerprint())

    def test_same_size_and_mtime_but_different_sha_is_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prompt.txt"
            path.write_text("first", encoding="utf-8")
            with patch.object(system_prompt, "SYSTEM_PROMPT_PATH", path):
                self._clear_cache()
                self.assertEqual(system_prompt.load_system_prompt(), "first")
                stat = path.stat()
                path.write_text("other", encoding="utf-8")
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                self.assertEqual(system_prompt.load_system_prompt(), "other")

    def test_missing_empty_and_invalid_utf8_fail_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prompt.txt"
            with patch.object(system_prompt, "SYSTEM_PROMPT_PATH", path):
                self._clear_cache()
                with self.assertRaises(errors.SystemPromptError):
                    system_prompt.load_system_prompt()
                path.write_text("   \n", encoding="utf-8")
                with self.assertRaises(errors.SystemPromptError):
                    system_prompt.load_system_prompt()
                path.write_bytes(b"\xff\xfe\xfa")
                self._clear_cache()
                with self.assertRaises(errors.SystemPromptError):
                    system_prompt.load_system_prompt()

    def test_real_prompt_contains_required_transport_rules(self) -> None:
        prompt = system_prompt.load_system_prompt()
        self.assertIn("deterministic Japanese-to-US-English translator", prompt)
        self.assertIn("protected placeholder", prompt)
        self.assertIn("raw text stream", prompt)
        self.assertIn("Do not return JSON", prompt)
        self.assertIn("CLJT...ENDX stop placeholder", prompt)
        self.assertIn("Never translate, alter, move, reorder, duplicate, or delete", prompt)
        self.assertIn("one paragraph per source segment", prompt)
        self.assertIn("output only a singular English noun phrase", prompt)
        self.assertNotIn("<Subject N> is", prompt)
        self.assertNotIn("photo prompt", prompt.lower())
