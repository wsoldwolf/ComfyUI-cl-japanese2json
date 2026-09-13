"""Errors raised by the reduced-Markdown prompt enhancer."""


class PromptEnhancerError(Exception):
    """Base error for profile, protocol, and node failures."""


class EnhancerProfileError(PromptEnhancerError):
    """An installed enhancer profile is missing or invalid."""


class EnhancerResponseError(PromptEnhancerError):
    """The GGUF model returned an invalid enhancement protocol."""


class EnhancerInferenceStallError(PromptEnhancerError):
    """The GGUF backend stopped producing output for too long."""
