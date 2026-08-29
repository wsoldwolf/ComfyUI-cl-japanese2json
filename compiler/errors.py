"""Exception hierarchy for cl_japanese2json."""


class CLJapaneseToJSONError(RuntimeError):
    """Base error raised by this custom node."""


class ModelDiscoveryError(CLJapaneseToJSONError):
    """A selected GGUF model could not be discovered or resolved."""


class ModelLoadError(CLJapaneseToJSONError):
    """The llama.cpp backend or GGUF model could not be loaded."""


class SystemPromptError(CLJapaneseToJSONError):
    """The external system-prompt file is unavailable or invalid."""


class TranslationError(CLJapaneseToJSONError):
    """The LLM translation response failed structural validation."""


class ProtectedTextError(CLJapaneseToJSONError):
    """Protected tags or Japanese dialogue have invalid syntax."""


class MarkdownParseError(CLJapaneseToJSONError):
    """Canonical reduced Markdown could not be parsed."""


class JSONGenerationError(CLJapaneseToJSONError):
    """An Emd value could not be converted to the plan JSON."""


class JSONValidationError(CLJapaneseToJSONError):
    """Generated plan JSON failed the required subset validation."""
