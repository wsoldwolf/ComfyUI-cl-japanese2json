from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from .helpers import module


debug_output = module("debug_output")


class DebugOutputTests(unittest.TestCase):
    def test_bundle_is_written_below_supplied_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_directory = Path(temp)
            target = debug_output.save_debug_bundle(
                plain_text="# シーン\n## ショット\n* 動作。",
                system_prompt="system",
                model_name="model.gguf",
                settings={"seed": 1},
                events=[
                    {
                        "batch": 1,
                        "attempt": 1,
                        "protected_stream": "CLJT0D0X\nCLJT0SCN1X text",
                        "user_request": "request",
                        "response_content": "raw response",
                        "finish_reason": "stop",
                        "usage": {"total_tokens": 10},
                        "validation_result": "validated",
                    }
                ],
                canonical_markdown="# Scene\n* Action.",
                json_text='{"shots": []}\n',
                output_directory=output_directory,
            )
            self.assertEqual(target.parent, output_directory / "cl_japanese2json_debug")
            self.assertEqual(
                (target / "source.md").read_text(encoding="utf-8"),
                "# シーン\n## ショット\n* 動作。",
            )
            self.assertEqual(
                (target / "event_01_batch_01_attempt_01_response.txt").read_text(
                    encoding="utf-8"
                ),
                "raw response",
            )
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["event_count"], 1)

    def test_error_bundle_contains_error_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = debug_output.save_debug_bundle(
                plain_text="source",
                system_prompt="system",
                model_name="model.gguf",
                settings={},
                events=[],
                error=ValueError("broken"),
                output_directory=Path(temp),
            )
            self.assertEqual(
                (target / "error.txt").read_text(encoding="utf-8"),
                "ValueError: broken\n",
            )
