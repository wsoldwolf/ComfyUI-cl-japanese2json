"""Lazy OpenAI Whisper model lifecycle without installation or downloads."""

from __future__ import annotations

import gc
import importlib
import logging
from pathlib import Path
import threading
from typing import Any

from .compiler.errors import WhisperLoadError, VocalPromptError


LOGGER = logging.getLogger("cl_vocal2promptseg")


class WhisperBackend:
    """Own one local OpenAI Whisper checkpoint and serialize inference."""

    def __init__(self) -> None:
        self.model: Any | None = None
        self.current_signature: tuple[str, str, int, int] | None = None
        self._whisper_module: Any | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _import_whisper() -> Any:
        try:
            module = importlib.import_module("whisper")
        except Exception as exc:
            raise WhisperLoadError(
                "OpenAI Whisper is not installed in the ComfyUI Python "
                "environment. Install openai-whisper manually; this custom "
                "node will not install or update it automatically."
            ) from exc
        if not callable(getattr(module, "load_model", None)):
            raise WhisperLoadError(
                "The imported 'whisper' package is not OpenAI Whisper because "
                "it has no load_model() function. Install openai-whisper manually."
            )
        return module

    def ensure_loaded(self, model_path: Path, device: str) -> Any:
        path = model_path.resolve(strict=True)
        stat = path.stat()
        signature = (str(path), device, stat.st_size, stat.st_mtime_ns)
        with self._lock:
            if self.model is not None and self.current_signature == signature:
                return self.model
            self.clear_model()
            whisper_module = self._import_whisper()
            LOGGER.info(
                "[cl_vocal2promptseg] Loading Whisper model: %s on %s",
                path.name,
                device,
            )
            try:
                model = whisper_module.load_model(str(path), device=device)
            except Exception as exc:
                raise WhisperLoadError(
                    f"Failed to load local Whisper model {path.name!r} on {device}"
                ) from exc
            if not callable(getattr(model, "transcribe", None)):
                raise WhisperLoadError(
                    f"Loaded Whisper model {path.name!r} has no transcribe() method"
                )
            self._whisper_module = whisper_module
            self.model = model
            self.current_signature = signature
            return model

    def transcribe(
        self,
        audio: Any,
        *,
        language: str | None,
        device: str,
        initial_prompt: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.model is None:
                raise WhisperLoadError("Whisper model is not loaded")
            try:
                result = self.model.transcribe(
                    audio,
                    task="transcribe",
                    temperature=0.0,
                    beam_size=5,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                    initial_prompt=initial_prompt,
                    verbose=None,
                    language=language,
                    fp16=device == "cuda",
                )
            except Exception as exc:
                raise VocalPromptError("OpenAI Whisper transcription failed") from exc
            if not isinstance(result, dict):
                raise VocalPromptError("OpenAI Whisper returned a non-object result")
            return result

    def clear_model(self) -> None:
        with self._lock:
            previous_device = (
                None if self.current_signature is None else self.current_signature[1]
            )
            self.model = None
            self.current_signature = None
            self._whisper_module = None
            gc.collect()
            if previous_device != "cuda":
                return
            try:
                torch = importlib.import_module("torch")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
