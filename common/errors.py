"""Cross-node errors for shared infrastructure."""


class CLNodeError(RuntimeError):
    """Base error raised by this custom-node package."""


class ModelDiscoveryError(CLNodeError):
    """A selected GGUF model could not be discovered or resolved."""


class ModelLoadError(CLNodeError):
    """The llama.cpp backend or GGUF model could not be loaded."""
