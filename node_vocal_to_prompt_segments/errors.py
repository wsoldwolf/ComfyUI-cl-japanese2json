"""Errors raised by vocal analysis and lyric alignment."""

from ..common.errors import CLNodeError


class WhisperModelDiscoveryError(CLNodeError):
    """A selected local OpenAI Whisper checkpoint could not be resolved."""


class WhisperLoadError(CLNodeError):
    """OpenAI Whisper or its selected local checkpoint could not be loaded."""


class VocalPromptError(CLNodeError):
    """Vocal analysis, lyrics alignment, or prompt generation failed."""
