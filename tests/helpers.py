from __future__ import annotations

import importlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
PACKAGE_NAME = ROOT.name
PKG = importlib.import_module(PACKAGE_NAME)


def module(name: str):
    return importlib.import_module(f"{PACKAGE_NAME}.{name}")


def request_stream(messages: list[dict[str, str]]) -> str:
    start_marker = "TRANSLATION_STREAM_BEGIN\n"
    end_marker = "\nTRANSLATION_STREAM_END"
    user_text = messages[-1]["content"]
    start = user_text.index(start_marker) + len(start_marker)
    end = user_text.index(end_marker, start)
    return user_text[start:end]


STREAM_START_RE = re.compile(r"(CLJT[0-9]+)(SUB|COM|SCN|SND)([0-9]+)X")
STREAM_STRUCTURAL_RE = re.compile(
    r"CLJT[0-9]+(?:D[0-9]+|(?:SUB|COM|SCN|SND)[0-9]+|END)X"
)
PROTECTED_RE = re.compile(r"CLJ[0-9]+C[0-9]+P[0-9]+X")
STREAM_SECTIONS = {
    "SUB": "Subjects",
    "COM": "Common",
    "SCN": "Scene",
    "SND": "Soundscape",
}


def request_records(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    stream = request_stream(messages)
    records: list[dict[str, Any]] = []
    for match in STREAM_START_RE.finditer(stream):
        prefix, code, number_text = match.groups()
        next_marker = STREAM_STRUCTURAL_RE.search(stream, match.end())
        if next_marker is None:
            raise AssertionError("translation stream has no following structural marker")
        body = stream[match.end():next_marker.start()].strip()
        records.append(
            {
                "id": f"R{int(number_text):06d}",
                "section": STREAM_SECTIONS[code],
                "text": body,
                "protected_placeholders": PROTECTED_RE.findall(body),
                "marker_token": match.group(0),
                "body_start": match.end(),
                "body_end": next_marker.start(),
            }
        )
    return records


def default_translation(record: dict[str, str]) -> str:
    tokens = list(record.get("protected_placeholders", []))
    protected = " ".join(tokens)
    if record["section"] == "Subjects":
        return f"a referenced character {protected}.".replace("  ", " ")
    if record["section"] == "Common":
        return f"A shared setting {protected}.".replace("  ", " ")
    if record["section"] == "Soundscape":
        return f"A defined audible sound {protected}.".replace("  ", " ")
    return f"The action occurs {protected}.".replace("  ", " ")


def default_stream_translation(
    messages: list[dict[str, str]],
    transform: Callable[[dict[str, Any]], str] = default_translation,
) -> str:
    stream = request_stream(messages)
    records = request_records(messages)
    parts: list[str] = []
    cursor = 0
    for record in records:
        body_start = record["body_start"]
        body_end = record["body_end"]
        parts.append(stream[cursor:body_start])
        parts.append(" " + transform(record) + " ")
        cursor = body_end
    parts.append(stream[cursor:])
    translated = "".join(parts)
    stop_match = re.search(r"CLJT[0-9]+ENDX\s*$", translated)
    if stop_match is not None:
        translated = translated[:stop_match.start()].rstrip()
    return translated


class FakeLLM:
    def __init__(self, responses: list[Any] | None = None, *, n_ctx: int = 65536) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.reset_count = 0
        self._n_ctx = n_ctx

    def n_ctx(self) -> int:
        return self._n_ctx

    def tokenize(self, data: bytes, add_bos: bool = True) -> list[int]:
        return list(range(max(1, len(data) // 8)))

    def reset(self) -> None:
        self.reset_count += 1

    def create_chat_completion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.responses:
            response = self.responses.pop(0)
            if callable(response):
                response = response(kwargs)
            if isinstance(response, dict) and "choices" in response:
                return response
            content = response if isinstance(response, str) else json.dumps(response)
        else:
            content = default_stream_translation(kwargs["messages"])
        return {
            "choices": [
                {"message": {"content": content}, "finish_reason": "stop"}
            ]
        }


def transport_response(transform: Callable[[dict[str, Any]], str] = default_translation):
    def responder(kwargs: dict[str, Any]) -> str:
        return default_stream_translation(kwargs["messages"], transform=transform)

    return responder


class FakeBackend(FakeLLM):
    def __init__(self, responses: list[Any] | None = None) -> None:
        super().__init__(responses)
        self.llm: Any | None = None
        self.current_model_signature: tuple[Any, ...] | None = None
        self.ensure_calls: list[dict[str, Any]] = []
        self.clear_count = 0

    def ensure_loaded(self, model_path: Path, **kwargs: Any) -> Any:
        self.ensure_calls.append({"model_path": model_path, **kwargs})
        self.llm = self
        self.current_model_signature = (str(model_path),) + tuple(kwargs.values())
        return self

    def clear_model(self) -> None:
        self.clear_count += 1
        self.llm = None
        self.current_model_signature = None

    def effective_n_ctx(self) -> int:
        return self._n_ctx

    def count_input_tokens(self, messages: list[dict[str, str]]) -> int:
        return len(messages[-1]["content"]) // 8 + 64

    def complete_chat(self, **kwargs: Any) -> Any:
        self.reset()
        return self.create_chat_completion(**kwargs)
