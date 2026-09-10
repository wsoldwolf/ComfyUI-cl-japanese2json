"""Shared GGUF discovery and llama.cpp runtime support."""

from .discovery import discover_model_names, resolve_model_name
from .runtime import LlamaBackend

__all__ = ["LlamaBackend", "discover_model_names", "resolve_model_name"]
