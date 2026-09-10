"""Exception hierarchy for the Japanese-to-JSON compiler."""

from ...common.errors import CLNodeError


class CLJapaneseToJSONError(CLNodeError):
    """Base error raised by this custom node."""


class SystemPromptError(CLJapaneseToJSONError):
    """The external system-prompt file is unavailable or invalid."""


class CommentSyntaxError(CLJapaneseToJSONError):
    """C-style comments have invalid or unsupported syntax."""


class TranslationError(CLJapaneseToJSONError):
    """The LLM translation response failed structural validation."""


class InferenceStallError(TranslationError):
    """The local LLM stopped producing output before completing a response."""


class ProtectedTextError(CLJapaneseToJSONError):
    """Protected tags or Japanese dialogue have invalid syntax."""


class MarkdownParseError(CLJapaneseToJSONError):
    """Canonical reduced Markdown could not be parsed."""


class JSONGenerationError(CLJapaneseToJSONError):
    """An Emd value could not be converted to the plan JSON."""


class JSONValidationError(CLJapaneseToJSONError):
    """Generated plan JSON failed the required subset validation."""
