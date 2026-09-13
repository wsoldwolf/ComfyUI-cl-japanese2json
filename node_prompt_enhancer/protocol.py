"""Low-entropy line protocol used by the Prompt Enhancer GGUF call."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import EnhancerResponseError


_SOURCE_ID_RE = re.compile(r"C[0-9]{3}\Z")
_CLASSES = frozenset({"keep", "style", "background"})
_REFERENCE_RE = re.compile(r"<(?:Subject|Picture)\s+[1-9][0-9]*>")
_PLACEHOLDER_RE = re.compile(r"CLPE(?:SUB|PIC)[0-9]+X")


@dataclass(frozen=True)
class EnhancementResponse:
    classifications: dict[str, str]
    background_lines: tuple[str, ...]
    warnings: tuple[str, ...]


def _natural_text(parts: list[str], context: str, warnings: list[str]) -> str:
    values = [part.strip() for part in parts if part.strip()]
    if not values:
        raise EnhancerResponseError(f"{context} must contain text")
    if len(parts) != 1:
        warnings.append(f"{context} contained extra TAB fields; joined them as one sentence")
    value = "、".join(values)
    if len(value) > 800:
        raise EnhancerResponseError(f"{context} is longer than 800 characters")
    if any(token in value for token in ("\x00", "\n", "\r", "```", "「", "」", "<d>", "</d>")):
        raise EnhancerResponseError(f"{context} contains forbidden syntax")
    if value.startswith(("#", "*")):
        raise EnhancerResponseError(f"{context} must be an unprefixed Common sentence")
    if _REFERENCE_RE.search(value) or _PLACEHOLDER_RE.search(value):
        raise EnhancerResponseError(f"{context} cannot introduce a Subject or Picture reference")
    return value


def parse_enhancement_response(
    raw: object,
    *,
    expected_source_ids: tuple[str, ...],
    minimum_background_lines: int,
    maximum_background_lines: int,
) -> EnhancementResponse:
    if not isinstance(raw, str) or not raw.strip():
        raise EnhancerResponseError("Enhancer response is empty")
    if "```" in raw:
        raise EnhancerResponseError("Enhancer response contains a Markdown code fence")
    lines = [line.strip(" \r\n") for line in raw.splitlines() if line.strip()]
    if not lines or lines[0] != "ENHANCEMENT_V1":
        raise EnhancerResponseError("Enhancer response must start with ENHANCEMENT_V1")
    if lines[-1] != "END_ENHANCEMENT":
        raise EnhancerResponseError("Enhancer response must end with END_ENHANCEMENT")
    classifications: dict[str, str] = {}
    background: list[str] = []
    warnings: list[str] = []
    for line_number, line in enumerate(lines[1:-1], start=2):
        if "<TAB>" in line or "\\t" in line:
            line = line.replace("<TAB>", "\t").replace("\\t", "\t")
            warnings.append(
                f"line {line_number} used a literal TAB marker; normalized it"
            )
        parts = line.split("\t")
        record = parts[0]
        if record == "SOURCE":
            if len(parts) != 3:
                raise EnhancerResponseError(
                    f"SOURCE at line {line_number} requires exactly three TAB fields"
                )
            source_id = parts[1].strip()
            classification = parts[2].strip()
            if not _SOURCE_ID_RE.fullmatch(source_id):
                raise EnhancerResponseError(f"SOURCE at line {line_number} has invalid id {source_id!r}")
            if classification not in _CLASSES:
                raise EnhancerResponseError(
                    f"SOURCE {source_id} uses unknown class {classification!r}"
                )
            if source_id in classifications:
                raise EnhancerResponseError(f"SOURCE {source_id} is repeated")
            classifications[source_id] = classification
        elif record == "BACKGROUND":
            background.append(
                _natural_text(parts[1:], f"BACKGROUND at line {line_number}", warnings)
            )
        else:
            raise EnhancerResponseError(
                f"Enhancer response uses unknown record {record!r} at line {line_number}"
            )
    expected = set(expected_source_ids)
    actual = set(classifications)
    if actual != expected:
        raise EnhancerResponseError(
            "Enhancer SOURCE ids differ from the request; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    if not minimum_background_lines <= len(background) <= maximum_background_lines:
        raise EnhancerResponseError(
            f"Enhancer returned {len(background)} BACKGROUND line(s); expected "
            f"{minimum_background_lines}-{maximum_background_lines}"
        )
    return EnhancementResponse(
        classifications=classifications,
        background_lines=tuple(background),
        warnings=tuple(warnings),
    )
