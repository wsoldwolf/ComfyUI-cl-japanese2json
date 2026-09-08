from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from .helpers import module


discovery = module("whisper_discovery")
errors = module("compiler.errors")


class FakeFolderPaths:
    def __init__(
        self, models_dir: Path, whisper_paths: list[Path] | None = None
    ) -> None:
        self.models_dir = str(models_dir)
        self._whisper_paths = [str(path) for path in (whisper_paths or [])]
        self.folder_names_and_paths = (
            {"whisper": object()} if whisper_paths else {}
        )

    def get_folder_paths(self, name: str):
        if name != "whisper":
            raise KeyError(name)
        return self._whisper_paths


class WhisperDiscoveryTests(unittest.TestCase):
    def test_recursive_case_insensitive_checkpoint_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            root = models / "whisper"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "large-v3.PT").write_bytes(b"checkpoint")
            (root / "ignored.bin").write_bytes(b"other")

            mapping = discovery.discover_whisper_model_map(
                FakeFolderPaths(models)
            )

            self.assertEqual(list(mapping), ["nested/large-v3.PT"])
            self.assertTrue(mapping["nested/large-v3.PT"].is_absolute())

    def test_duplicate_real_path_is_listed_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            root = models / "whisper"
            root.mkdir(parents=True)
            (root / "base.pt").write_bytes(b"x")

            mapping = discovery.discover_whisper_model_map(
                FakeFolderPaths(models, [root])
            )

            self.assertEqual(list(mapping), ["base.pt"])

    def test_name_collision_uses_stable_root_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            models = base / "models"
            primary = models / "whisper"
            extra = base / "extra"
            primary.mkdir(parents=True)
            extra.mkdir()
            (primary / "same.pt").write_bytes(b"a")
            (extra / "same.pt").write_bytes(b"b")

            mapping = discovery.discover_whisper_model_map(
                FakeFolderPaths(models, [extra])
            )

            self.assertEqual(
                set(mapping), {"[models] same.pt", "[whisper1] same.pt"}
            )

    def test_placeholder_and_stale_selection_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            fake = FakeFolderPaths(models)
            self.assertEqual(
                discovery.discover_whisper_model_names(fake),
                [discovery.NO_WHISPER_MODELS_PLACEHOLDER],
            )
            with self.assertRaisesRegex(
                errors.WhisperModelDiscoveryError, "Place an OpenAI Whisper"
            ):
                discovery.resolve_whisper_model_name(
                    discovery.NO_WHISPER_MODELS_PLACEHOLDER, fake
                )

            root = models / "whisper"
            root.mkdir(parents=True)
            checkpoint = root / "base.pt"
            checkpoint.write_bytes(b"x")
            self.assertEqual(
                discovery.resolve_whisper_model_name("base.pt", fake),
                checkpoint.resolve(),
            )
            checkpoint.unlink()
            with self.assertRaises(errors.WhisperModelDiscoveryError):
                discovery.resolve_whisper_model_name("base.pt", fake)


if __name__ == "__main__":
    unittest.main()
