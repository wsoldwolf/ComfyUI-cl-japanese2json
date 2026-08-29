from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from .helpers import module


discovery = module("model_discovery")
errors = module("compiler.errors")


class FakeFolderPaths:
    def __init__(self, models_dir: Path, llm_paths: list[Path] | None = None) -> None:
        self.models_dir = str(models_dir)
        self._llm_paths = [str(path) for path in (llm_paths or [])]
        self.folder_names_and_paths = {"LLM": object()} if llm_paths else {}

    def get_folder_paths(self, name: str):
        if name != "LLM":
            raise KeyError(name)
        return self._llm_paths


class ModelDiscoveryTests(unittest.TestCase):
    def test_recursive_case_insensitive_and_mmproj_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            root = models / "LLM" / "GGUF"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "model.GGUF").write_bytes(b"gguf")
            (root / "model-mmproj.gguf").write_bytes(b"projection")
            mapping = discovery.discover_model_map(FakeFolderPaths(models))
            self.assertEqual(list(mapping), ["nested/model.GGUF"])
            self.assertTrue(mapping["nested/model.GGUF"].is_absolute())

    def test_duplicate_actual_path_is_shown_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            root = models / "LLM" / "GGUF"
            root.mkdir(parents=True)
            (root / "same.gguf").write_bytes(b"x")
            fake = FakeFolderPaths(models, [models / "LLM"])
            self.assertEqual(len(discovery.discover_model_map(fake)), 1)

    def test_same_relative_name_across_roots_gets_stable_root_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            models = base / "models"
            primary = models / "LLM" / "GGUF"
            extra = base / "extra"
            primary.mkdir(parents=True)
            extra.mkdir()
            (primary / "same.gguf").write_bytes(b"a")
            (extra / "same.gguf").write_bytes(b"b")
            mapping = discovery.discover_model_map(FakeFolderPaths(models, [extra]))
            self.assertEqual(
                set(mapping), {"[models] same.gguf", "[LLM1] same.gguf"}
            )

    def test_no_models_placeholder_and_resolve_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeFolderPaths(Path(temp) / "models")
            self.assertEqual(
                discovery.discover_model_names(fake),
                [discovery.NO_MODELS_PLACEHOLDER],
            )
            with self.assertRaisesRegex(errors.ModelDiscoveryError, "Place a text GGUF"):
                discovery.resolve_model_name(discovery.NO_MODELS_PLACEHOLDER, fake)

    def test_selected_id_resolves_against_fresh_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp) / "models"
            root = models / "LLM" / "GGUF"
            root.mkdir(parents=True)
            file_path = root / "model.gguf"
            file_path.write_bytes(b"x")
            fake = FakeFolderPaths(models)
            self.assertEqual(
                discovery.resolve_model_name("model.gguf", fake), file_path.resolve()
            )
            file_path.unlink()
            with self.assertRaises(errors.ModelDiscoveryError):
                discovery.resolve_model_name("model.gguf", fake)
