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

    def restore(
        self,
        text: str,
        *,
        allow_known_raw_subjects: bool = False,
    ) -> str:
        raw_references = list(_REFERENCE_RE.finditer(text))
        if raw_references and not allow_known_raw_subjects:
            raise PlannerResponseError(
                "Planner response contains an unprotected reference tag "
                f"{raw_references[0].group(0)!r}"
            )
        normalized = text
        if raw_references:
            reference_to_token = {
                reference: token
                for token, reference in self.token_to_reference.items()
            }
            for match in raw_references:
                reference = match.group(0)
                token = reference_to_token.get(reference)
                if match.group(1) != "Subject" or token is None:
                    raise PlannerResponseError(
                        "Planner response contains an unprotected reference tag "
                        f"{reference!r}"
                    )
                normalized = normalized.replace(reference, token)
        unknown = [
            token
            for token in _TOKEN_RE.findall(normalized)
            if token not in self.token_to_reference
        ]
        if unknown:
            raise PlannerResponseError(
                f"Planner invented unknown reference placeholder {unknown[0]!r}"
            )
        restored = normalized
        for token, reference in sorted(
            self.token_to_reference.items(), key=lambda item: -len(item[0])
        ):
            restored = restored.replace(token, reference)
        if _TOKEN_RE.search(restored):
            raise PlannerResponseError("Planner response contains an unresolved reference placeholder")
        return restored

    def legend(self) -> dict[str, str]:
        return dict(sorted(self.token_to_reference.items()))
