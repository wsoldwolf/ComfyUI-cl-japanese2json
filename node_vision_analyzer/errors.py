"""Errors raised by the local Vision GGUF analyzer node."""

from __future__ import annotations

from ..common.errors import CLNodeError


class VisionAnalyzerError(CLNodeError):
    """Base error for image loading, observation, and rendering failures."""


class VisionModelDiscoveryError(VisionAnalyzerError):
    """A Vision GGUF and projector pair could not be resolved safely."""


class VisionImageError(VisionAnalyzerError):
    """The selected ComfyUI input image is invalid or unsafe."""


class VisionObservationError(VisionAnalyzerError):
    """The model response does not satisfy the observation protocol."""


class VisionAnalysisError(VisionAnalyzerError):
    """Vision inference could not produce a validated observation."""


class PictureBindingError(VisionAnalyzerError):
    """The image output cannot be mapped to one H3 Picture reference."""


class VisionCacheError(VisionAnalyzerError):
    """A cache operation failed in a way the caller must handle."""
