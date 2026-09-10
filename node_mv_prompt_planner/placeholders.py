"""Reference placeholders for planner prompts and model responses."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .errors import PlannerResponseError


_REFERENCE_RE = re.compile(r"<(Subject|Picture|Video|Audio) ([1-9][0-9]*)>")
_TOKEN_RE = re.compile(r"CLMP[A-Z0-9]+X")
_PREFIXES = {"Subject": "SUB", "Picture": "PIC", "Video": "VID", "Audio": "AUD"}


@dataclass
class ReferenceProtector:
    token_to_reference: dict[str, str] = field(default_factory=dict)

    def protect(self, text: str) -> str:
        collision = _TOKEN_RE.search(text)
        if collision is not None:
            raise PlannerResponseError(
                f"Planner input contains reserved placeholder {collision.group(0)!r}"
            )

        def replace(match: re.Match[str]) -> str:
            kind, number = match.groups()
            token = f"CLMP{_PREFIXES[kind]}{number}X"
            self.token_to_reference[token] = match.group(0)
            return token

        return _REFERENCE_RE.sub(replace, text)

    def restore(self, text: str) -> str:
        raw_reference = _REFERENCE_RE.search(text)
        if raw_reference is not None:
            raise PlannerResponseError(
                "Planner response contains an unprotected reference tag "
                f"{raw_reference.group(0)!r}"
            )
        unknown = [token for token in _TOKEN_RE.findall(text) if token not in self.token_to_reference]
        if unknown:
            raise PlannerResponseError(
                f"Planner invented unknown reference placeholder {unknown[0]!r}"
            )
        restored = text
        for token, reference in sorted(
            self.token_to_reference.items(), key=lambda item: -len(item[0])
        ):
            restored = restored.replace(token, reference)
        if _TOKEN_RE.search(restored):
            raise PlannerResponseError("Planner response contains an unresolved reference placeholder")
        return restored

    def legend(self) -> dict[str, str]:
        return dict(sorted(self.token_to_reference.items()))
