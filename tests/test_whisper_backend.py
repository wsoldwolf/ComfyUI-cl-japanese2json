from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import module


backend_module = module("node_vocal_to_prompt_segments.whisper_runtime")
errors = module("node_vocal_to_prompt_segments.errors")


class FakeModel:
    def __init__(self, result=None) -> None:
        self.result = result if result is not None else {"segments": []}
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": audio, **kwargs})
        return self.result


class FakeWhisperModule:
    def __init__(self) -> None:
        self.load_calls: list[tuple[str, str]] = []
        self.models: list[FakeModel] = []

    def load_model(self, path: str, *, device: str):
        self.load_calls.append((path, device))
        model = FakeModel()
        self.models.append(model)
        return model


class WhisperBackendTests(unittest.TestCase):
    def test_local_checkpoint_is_loaded_and_reused_by_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "base.pt"
            checkpoint.write_bytes(b"checkpoint")
            fake_whisper = FakeWhisperModule()
            backend = backend_module.WhisperBackend()

            with patch.object(
                backend_module.importlib,
                "import_module",
                return_value=fake_whisper,
            ):
                first = backend.ensure_loaded(checkpoint, "cpu")
                second = backend.ensure_loaded(checkpoint, "cpu")

            self.assertIs(first, second)
            self.assertEqual(
                fake_whisper.load_calls,
                [(str(checkpoint.resolve()), "cpu")],
            )

    def test_changed_checkpoint_reloads_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "base.pt"
            checkpoint.write_bytes(b"one")
            fake_whisper = FakeWhisperModule()
            backend = backend_module.WhisperBackend()

            with patch.object(
                backend_module.importlib,
                "import_module",
                return_value=fake_whisper,
            ):
                first = backend.ensure_loaded(checkpoint, "cpu")
                checkpoint.write_bytes(b"a longer checkpoint")
                second = backend.ensure_loaded(checkpoint, "cpu")

            self.assertIsNot(first, second)
            self.assertEqual(len(fake_whisper.load_calls), 2)

    def test_transcription_uses_deterministic_word_timestamp_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "base.pt"
            checkpoint.write_bytes(b"checkpoint")
            fake_whisper = FakeWhisperModule()
            backend = backend_module.WhisperBackend()
            with patch.object(
                backend_module.importlib,
                "import_module",
                return_value=fake_whisper,
            ):
                backend.ensure_loaded(checkpoint, "cpu")
            result = backend.transcribe(
                "pcm",
                language="ja",
                device="cpu",
                initial_prompt="冒頭歌詞",
                condition_on_previous_text=True,
            )

            self.assertEqual(result, {"segments": []})
            self.assertEqual(
                fake_whisper.models[0].calls,
                [
                    {
                        "audio": "pcm",
                        "task": "transcribe",
                        "temperature": 0.0,
                        "beam_size": 5,
                        "word_timestamps": True,
                        "condition_on_previous_text": True,
                        "initial_prompt": "冒頭歌詞",
                        "verbose": None,
                        "language": "ja",
                        "fp16": False,
                    }
                ],
            )

    def test_missing_or_wrong_whisper_package_has_actionable_error(self) -> None:
        backend = backend_module.WhisperBackend()
        with patch.object(
            backend_module.importlib,
            "import_module",
            side_effect=ModuleNotFoundError("whisper"),
        ), self.assertRaisesRegex(errors.WhisperLoadError, "not installed"):
            backend._import_whisper()

        with patch.object(
            backend_module.importlib,
            "import_module",
            return_value=object(),
        ), self.assertRaisesRegex(errors.WhisperLoadError, "not OpenAI Whisper"):
            backend._import_whisper()

    def test_bad_result_and_transcription_failure_are_rejected(self) -> None:
        backend = backend_module.WhisperBackend()
        with self.assertRaisesRegex(errors.WhisperLoadError, "not loaded"):
            backend.transcribe(
                "pcm",
                language=None,
                device="cpu",
                initial_prompt=None,
                condition_on_previous_text=True,
            )

        class BrokenModel:
            def transcribe(self, *_args, **_kwargs):
                raise RuntimeError("broken")

        backend.model = BrokenModel()
        with self.assertRaisesRegex(errors.VocalPromptError, "transcription failed"):
            backend.transcribe(
                "pcm",
                language=None,
                device="cpu",
                initial_prompt=None,
                condition_on_previous_text=True,
            )

        backend.model = FakeModel(result=[])
        with self.assertRaisesRegex(errors.VocalPromptError, "non-object"):
            backend.transcribe(
                "pcm",
                language=None,
                device="cpu",
                initial_prompt=None,
                condition_on_previous_text=True,
            )


if __name__ == "__main__":
    unittest.main()
