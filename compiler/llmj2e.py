"""LLM-assisted Japanese-to-English reduced Markdown translation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Iterable

from .errors import ProtectedTextError, TranslationError
from .protected_text import (
    ProtectedPayload,
    contains_unprotected_japanese,
    protect_text,
    restore_text,
    validate_protected_translation,
)


LOGGER = logging.getLogger("cl_japanese2json")
MAX_SEED = 4_294_967_295
CODE_FENCE_RE = re.compile(r"```", re.IGNORECASE)
THINK_RE = re.compile(r"<\s*/?\s*think\b", re.IGNORECASE)
LEADING_THINK_BLOCK_RE = re.compile(
    r"\A\s*<\s*think\s*>.*?<\s*/\s*think\s*>\s*",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class TranslationRecord:
    record_id: str
    section: str
    block_index: int
    payload: ProtectedPayload
    translated: str | None = None


@dataclass(frozen=True)
class StreamRecord:
    record: TranslationRecord
    marker_token: str


@dataclass(frozen=True)
class TranslationStream:
    text: str
    prefix: str
    records: tuple[StreamRecord, ...]
    directive_tokens: tuple[str, ...]
    protected_tokens: tuple[str, ...]
    stop_token: str

    @property
    def tokens(self) -> tuple[str, ...]:
        return self.directive_tokens + tuple(
            stream_record.marker_token for stream_record in self.records
        ) + self.protected_tokens + (self.stop_token,)


@dataclass
class LexicalBlock:
    directive: str
    section: str
    records: list[TranslationRecord] = field(default_factory=list)


@dataclass
class LexicalDocument:
    blocks: list[LexicalBlock]
    directive_count: int
    bullet_count: int

    @property
    def records(self) -> list[TranslationRecord]:
        return [record for block in self.blocks for record in block.records]


def _canonical_scene_directive(line: str, line_number: int) -> str:
    options = line[len("# シーン") :]
    if options == "":
        return "# Scene"
    if not options.startswith(" "):
        LOGGER.warning(
            "[cl_japanese2json] Invalid scene directive spacing at line %d; using 5 seconds",
            line_number,
        )
        return "# Scene"

    option_text = options[1:]
    if option_text == "継続":
        return "# Scene CONTINUE"

    match = re.fullmatch(r"([^\s]+)秒(?: (継続))?", option_text)
    if not match:
        LOGGER.warning(
            "[cl_japanese2json] Invalid scene options at line %d; using 5 seconds",
            line_number,
        )
        return "# Scene"

    raw_duration = match.group(1)
    try:
        duration = int(raw_duration, 10)
    except ValueError:
        duration = 5
    if raw_duration != str(duration) or not 1 <= duration <= 60:
        LOGGER.warning(
            "[cl_japanese2json] Scene duration %r at line %d is outside 1-60; using 5 seconds",
            raw_duration,
            line_number,
        )
        duration = 5
    suffix = " CONTINUE" if match.group(2) else ""
    return f"# Scene {duration}sec{suffix}"


def lex_japanese_markdown(plain_text: str) -> LexicalDocument:
    """Recognize directives and protect only valid bullet payloads."""

    if not isinstance(plain_text, str):
        raise TypeError("plain_text must be a string")

    blocks: list[LexicalBlock] = []
    current: LexicalBlock | None = None
    record_number = 0
    directive_count = 0
    bullet_count = 0

    for line_number, line in enumerate(plain_text.lstrip("\ufeff").splitlines(), start=1):
        if line.strip() == "":
            current = None
            continue

        canonical: str | None = None
        section: str | None = None
        if line == "# サブジェクト":
            canonical, section = "# Subjects", "Subjects"
        elif line == "# 共通プロンプト":
            canonical, section = "# Common", "Common"
        elif line == "# シーン" or line.startswith("# シーン "):
            canonical, section = _canonical_scene_directive(line, line_number), "Scene"

        if canonical is not None and section is not None:
            current = LexicalBlock(canonical, section)
            blocks.append(current)
            directive_count += 1
            continue

        if line.startswith("#"):
            LOGGER.warning(
                "[cl_japanese2json] Unknown directive at line %d ignored: %s",
                line_number,
                line,
            )
            current = None
            continue

        if line.startswith("* "):
            if current is None:
                LOGGER.warning(
                    "[cl_japanese2json] Bullet outside a recognized section at line %d ignored",
                    line_number,
                )
                continue
            record_number += 1
            bullet_count += 1
            record_id = f"R{record_number:06d}"
            record = TranslationRecord(
                record_id=record_id,
                section=current.section,
                block_index=len(blocks) - 1,
                payload=protect_text(line[2:], namespace=record_id),
            )
            current.records.append(record)
            continue

        if current is None:
            LOGGER.warning(
                "[cl_japanese2json] Unknown line %d outside a section ignored",
                line_number,
            )
        else:
            LOGGER.warning(
                "[cl_japanese2json] Non-bullet line %d inside %s ignored",
                line_number,
                current.section,
            )

    LOGGER.info(
        "[cl_japanese2json] Read %d directive(s) and %d translatable text segment(s)",
        directive_count,
        bullet_count,
    )
    return LexicalDocument(blocks, directive_count, bullet_count)


SECTION_STREAM_CODES = {"Subjects": "SUB", "Common": "COM", "Scene": "SCN"}


def _build_translation_stream(records: Iterable[TranslationRecord]) -> TranslationStream:
    selected = list(records)
    combined = "\n".join(record.payload.text for record in selected)
    prefix_number = 0
    while f"CLJT{prefix_number}" in combined:
        prefix_number += 1
    prefix = f"CLJT{prefix_number}"

    parts: list[str] = []
    stream_records: list[StreamRecord] = []
    directive_tokens: list[str] = []
    last_block_index: int | None = None
    protected_tokens: list[str] = []
    for record in selected:
        if record.block_index != last_block_index:
            directive_token = f"{prefix}D{record.block_index}X"
            parts.append(directive_token)
            directive_tokens.append(directive_token)
            last_block_index = record.block_index

        code = SECTION_STREAM_CODES[record.section]
        number = int(record.record_id[1:])
        marker_token = f"{prefix}{code}{number}X"
        parts.append(f"{marker_token} {record.payload.text}")
        stream_records.append(StreamRecord(record, marker_token))
        protected_tokens.extend(record.payload.tokens)

    stop_token = f"{prefix}ENDX"
    parts.append(stop_token)
    return TranslationStream(
        text="\n".join(parts),
        prefix=prefix,
        records=tuple(stream_records),
        directive_tokens=tuple(directive_tokens),
        protected_tokens=tuple(protected_tokens),
        stop_token=stop_token,
    )


def _user_payload(
    stream: TranslationStream, *, retry_reason: str | None = None
) -> str:
    requirement = (
        "Translate the Japanese prose inside the single protected stream below. Return only the "
        "translated raw stream, without JSON or quotes. "
        "Do not translate, alter, move, duplicate, or delete any placeholder token. "
        "A SUB marker starts a singular noun phrase ending in an ASCII period; COM and SCN markers "
        "start concise natural US English. Keep one segment after each marker and preserve the "
        "marker order. Copy the final stop placeholder after translating the last segment. "
        "Emergency recovery only: if every CLJT structural marker is omitted, return exactly "
        "one blank-line-separated paragraph per source segment in the original order and keep "
        "every CLJ protected placeholder unchanged."
    )
    if retry_reason is not None:
        safe_reason = retry_reason.replace("\r", " ").replace("\n", " ")[:400]
        requirement = (
            f"The previous response failed strict validation: {safe_reason}. "
            "Correct that exact problem and apply every constraint. "
            + requirement
        )
    return (
        f"{requirement}\n"
        f"TRANSLATION_STREAM_BEGIN\n{stream.text}\nTRANSLATION_STREAM_END\n"
        "/no_think"
    )


def _messages(
    system_prompt: str,
    stream: TranslationStream,
    *,
    retry_reason: str | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": _user_payload(stream, retry_reason=retry_reason),
        },
    ]


def _effective_n_ctx(llm: Any) -> int | None:
    value: Any = None
    if hasattr(llm, "effective_n_ctx"):
        value = llm.effective_n_ctx()
    elif hasattr(llm, "n_ctx"):
        value = llm.n_ctx() if callable(llm.n_ctx) else llm.n_ctx
    return value if isinstance(value, int) and value > 0 else None


def _count_input_tokens(llm: Any, messages: list[dict[str, str]]) -> int:
    if hasattr(llm, "count_input_tokens"):
        value = llm.count_input_tokens(messages)
        if isinstance(value, int) and value >= 0:
            return value

    combined = "\n".join(message["content"] for message in messages)
    tokenizer = getattr(llm, "tokenize", None)
    if callable(tokenizer):
        try:
            return len(tokenizer(combined.encode("utf-8"), add_bos=True)) + 64
        except (TypeError, ValueError, RuntimeError):
            pass
    return max(1, (len(combined.encode("utf-8")) + 2) // 3) + 64


def _fits_context(
    llm: Any,
    system_prompt: str,
    records: list[TranslationRecord],
    max_tokens: int,
) -> bool:
    n_ctx = _effective_n_ctx(llm)
    if n_ctx is None:
        return True
    stream = _build_translation_stream(records)
    return _count_input_tokens(llm, _messages(system_prompt, stream)) + max_tokens <= n_ctx


def _make_batches(
    records: list[TranslationRecord],
    llm: Any,
    system_prompt: str,
    max_tokens: int,
) -> list[list[TranslationRecord]]:
    if not records:
        return []
    if _fits_context(llm, system_prompt, records, max_tokens):
        return [records]

    batches: list[list[TranslationRecord]] = []
    current: list[TranslationRecord] = []
    for record in records:
        candidate = [*current, record]
        if _fits_context(llm, system_prompt, candidate, max_tokens):
            current = candidate
            continue
        if not current:
            raise TranslationError(
                f"Record {record.record_id} does not fit the effective context length with max_tokens={max_tokens}"
            )
        batches.append(current)
        current = [record]
        if not _fits_context(llm, system_prompt, current, max_tokens):
            raise TranslationError(
                f"Record {record.record_id} does not fit the effective context length with max_tokens={max_tokens}"
            )
    if current:
        batches.append(current)
    return batches


def _normalize_seed(value: int) -> int:
    return ((int(value) - 1) % MAX_SEED) + 1


def _call_llm(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    seed: int,
    stop_token: str,
) -> Any:
    kwargs = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "repeat_penalty": repetition_penalty,
        "seed": seed,
        "stop": [stop_token],
    }
    if hasattr(llm, "complete_chat"):
        return llm.complete_chat(**kwargs)

    reset = getattr(llm, "reset", None)
    if callable(reset):
        reset()
    completion = getattr(llm, "create_chat_completion", None)
    if not callable(completion):
        raise TranslationError("LLM backend does not provide create_chat_completion()")
    return completion(**kwargs)


def _content_from_response(response: Any) -> str:
    if not isinstance(response, dict):
        raise TranslationError("LLM response must be an object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TranslationError("LLM response has no choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise TranslationError("LLM response choice is invalid")
    if choice.get("finish_reason") == "length":
        raise TranslationError("LLM response was truncated because max_tokens was reached")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise TranslationError("LLM response has no message object")
    content = message.get("content")
    if not isinstance(content, str) or content.strip() == "":
        raise TranslationError("LLM response content is empty")
    leading_think = LEADING_THINK_BLOCK_RE.match(content)
    if leading_think is not None:
        content = content[leading_think.end():]
        LOGGER.info("[cl_japanese2json] Ignored one leading Qwen thinking block")
    if content.strip() == "":
        raise TranslationError(
            "LLM response contains thinking markup but no translation"
        )
    if CODE_FENCE_RE.search(content):
        raise TranslationError("LLM response contains a Markdown code fence")
    if THINK_RE.search(content):
        raise TranslationError("LLM response contains Qwen thinking markup")
    usage = response.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", "?")
        completion_tokens = usage.get("completion_tokens", "?")
        total_tokens = usage.get("total_tokens", "?")
        LOGGER.info(
            "[cl_japanese2json] LLM tokens: prompt=%s completion=%s total=%s",
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
    return content


def _validate_translation_text(record: TranslationRecord, translated: str) -> str:
    if "\n" in translated or "\r" in translated:
        raise TranslationError(f"Record {record.record_id} was split across multiple lines")
    validate_protected_translation(record.payload, translated)
    restored = restore_text(record.payload, translated)
    if record.section == "Subjects" and restored and not restored.endswith("."):
        raise TranslationError(
            f"Subject record {record.record_id} must end with an ASCII period"
        )
    try:
        japanese_remains = contains_unprotected_japanese(restored)
    except ProtectedTextError as exc:
        raise TranslationError(
            f"Record {record.record_id} has invalid restored direct-speech tags"
        ) from exc
    if japanese_remains:
        raise TranslationError(
            f"Record {record.record_id} still contains Japanese outside protected direct speech"
        )
    return restored


def _translation_stream_from_content(
    content: str, stream: TranslationStream
) -> str:
    translated = content.strip()
    stop_count = translated.count(stream.stop_token)
    if stop_count > 1:
        raise TranslationError(
            f"Stop placeholder {stream.stop_token!r} occurred {stop_count} times"
        )
    if stop_count == 1:
        before, after = translated.split(stream.stop_token, 1)
        if after.strip():
            raise TranslationError("LLM added text after the stop placeholder")
        translated = before.rstrip()
    if not translated:
        raise TranslationError("LLM translation stream is empty")
    return translated


def _structural_tokens(stream: TranslationStream) -> list[str]:
    return sorted(
        [
            *stream.directive_tokens,
            *(stream_record.marker_token for stream_record in stream.records),
        ],
        key=lambda token: stream.text.index(token),
    )


def _structural_pattern(stream: TranslationStream) -> re.Pattern[str]:
    return re.compile(
        re.escape(stream.prefix) + r"(?:D[0-9]+|(?:SUB|COM|SCN)[0-9]+)X"
    )


def _normalize_segment_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_markerless_values(
    translated_stream: str, stream: TranslationStream
) -> list[str]:
    if len(stream.records) < 2 or not stream.protected_tokens:
        raise TranslationError("LLM omitted every structural placeholder")
    if stream.prefix in translated_stream:
        raise TranslationError("LLM altered every structural placeholder")

    normalized_newlines = translated_stream.replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    paragraphs = [
        _normalize_segment_text(paragraph)
        for paragraph in re.split(r"\n[ \t]*\n+", normalized_newlines.strip())
        if paragraph.strip()
    ]
    lines = [
        _normalize_segment_text(line)
        for line in normalized_newlines.splitlines()
        if line.strip()
    ]

    protected_counts = [
        translated_stream.count(token) for token in stream.protected_tokens
    ]
    protected_exact = sum(count == 1 for count in protected_counts)
    protected_occurrences = sum(protected_counts)
    LOGGER.info(
        "[cl_japanese2json] Markerless response shape: paragraphs=%d "
        "non_empty_lines=%d protected_exact=%d/%d protected_occurrences=%d",
        len(paragraphs),
        len(lines),
        protected_exact,
        len(stream.protected_tokens),
        protected_occurrences,
    )

    expected = len(stream.records)
    if len(paragraphs) == expected:
        LOGGER.warning(
            "[cl_japanese2json] LLM omitted all structural placeholders; "
            "trying strict paragraph-aligned validation for %d text segment(s)",
            len(paragraphs),
        )
        return paragraphs
    if len(lines) != expected:
        raise TranslationError(
            "LLM omitted every structural placeholder and returned "
            f"{len(paragraphs)} paragraph(s) and {len(lines)} non-empty "
            f"line(s); expected {expected} text segment(s)"
        )
    LOGGER.warning(
        "[cl_japanese2json] LLM omitted all structural placeholders; "
        "trying strict line-aligned validation for %d text segment(s)",
        len(lines),
    )
    return lines


def _parse_stream_text_values(
    content: str, stream: TranslationStream
) -> list[str]:
    translated_stream = _translation_stream_from_content(content, stream)
    structural_tokens = _structural_tokens(stream)
    found_structural = _structural_pattern(stream).findall(translated_stream)
    if not found_structural:
        return _parse_markerless_values(translated_stream, stream)

    for token in structural_tokens:
        count = translated_stream.count(token)
        if count != 1:
            raise TranslationError(
                f"Structural placeholder {token!r} occurred {count} time(s); expected exactly once"
            )

    if found_structural != structural_tokens:
        raise TranslationError(
            "LLM changed, added, or reordered structural placeholders"
        )

    positions = [translated_stream.index(token) for token in structural_tokens]
    if translated_stream[: positions[0]].strip():
        raise TranslationError("LLM added text before the first structural placeholder")

    by_marker = {
        stream_record.marker_token: stream_record for stream_record in stream.records
    }
    translated_by_id: dict[str, str] = {}
    for index, token in enumerate(structural_tokens):
        value_start = positions[index] + len(token)
        value_end = (
            positions[index + 1]
            if index + 1 < len(structural_tokens)
            else len(translated_stream)
        )
        value = translated_stream[value_start:value_end]
        stream_record = by_marker.get(token)
        if stream_record is None:
            if value.strip():
                raise TranslationError(
                    "LLM added text after a directive placeholder"
                )
            continue
        translated_by_id[stream_record.record.record_id] = _normalize_segment_text(
            value
        )

    return [
        translated_by_id[stream_record.record.record_id]
        for stream_record in stream.records
    ]


def _salvage_stream_response(
    content: str, stream: TranslationStream
) -> dict[int, str]:
    """Return individually valid segments from a structurally incomplete response."""

    try:
        translated_stream = _translation_stream_from_content(content, stream)
    except TranslationError:
        return {}

    structural_tokens = _structural_tokens(stream)
    expected_set = set(structural_tokens)
    found = _structural_pattern(stream).findall(translated_stream)
    if any(token not in expected_set for token in found) or len(found) != len(set(found)):
        return {}
    expected_index = {token: index for index, token in enumerate(structural_tokens)}
    found_indices = [expected_index[token] for token in found]
    if found_indices != sorted(found_indices):
        return {}

    positions = {token: translated_stream.index(token) for token in found}
    if found and translated_stream[: positions[found[0]]].strip():
        return {}
    for directive_token in stream.directive_tokens:
        if directive_token not in positions:
            continue
        token_index = expected_index[directive_token]
        if token_index + 1 >= len(structural_tokens):
            continue
        next_token = structural_tokens[token_index + 1]
        if next_token not in positions:
            continue
        value_start = positions[directive_token] + len(directive_token)
        if translated_stream[value_start:positions[next_token]].strip():
            return {}

    missing_directive_blocks = {
        stream_record.record.block_index
        for stream_record in stream.records
        if f"{stream.prefix}D{stream_record.record.block_index}X" not in positions
    }
    first_record_by_block: dict[int, int] = {}
    for record_index, stream_record in enumerate(stream.records):
        first_record_by_block.setdefault(
            stream_record.record.block_index, record_index
        )
    salvaged: dict[int, str] = {}
    for record_index, stream_record in enumerate(stream.records):
        if (
            stream_record.record.block_index in missing_directive_blocks
            and first_record_by_block[stream_record.record.block_index]
            == record_index
        ):
            continue
        token = stream_record.marker_token
        if token not in positions:
            continue
        token_index = expected_index[token]
        if token_index + 1 < len(structural_tokens):
            next_token = structural_tokens[token_index + 1]
            if next_token not in positions:
                continue
            value_end = positions[next_token]
        else:
            value_end = len(translated_stream)
        value_start = positions[token] + len(token)
        translated = _normalize_segment_text(
            translated_stream[value_start:value_end]
        )
        try:
            salvaged[record_index] = _validate_stream_record_text(
                stream_record, translated, stream.protected_tokens
            )
        except TranslationError:
            continue
    return salvaged


def _validate_stream_record_text(
    stream_record: StreamRecord,
    translated: str,
    all_protected_tokens: tuple[str, ...],
) -> str:
    own_tokens = set(stream_record.record.payload.tokens)
    foreign = [
        token
        for token in all_protected_tokens
        if token not in own_tokens and token in translated
    ]
    if foreign:
        raise TranslationError(
            f"Record {stream_record.record.record_id} contains a placeholder from another record"
        )
    return _validate_translation_text(stream_record.record, translated)


def _parse_stream_response(content: str, stream: TranslationStream) -> list[str]:
    translated_values = _parse_stream_text_values(content, stream)
    return [
        _validate_stream_record_text(
            stream_record, translated, stream.protected_tokens
        )
        for stream_record, translated in zip(stream.records, translated_values)
    ]


def _translate_batch(
    records: list[TranslationRecord],
    llm: Any,
    system_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    seed: int,
    batch_index: int,
) -> list[str]:
    first_seed = _normalize_seed(seed + batch_index)
    stream = _build_translation_stream(records)
    validated: list[str | None] = [None] * len(records)
    retry_indices: list[int]
    first_error: TranslationError
    content: str | None = None

    try:
        response = _call_llm(
            llm,
            _messages(system_prompt, stream),
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            seed=first_seed,
            stop_token=stream.stop_token,
        )
        content = _content_from_response(response)
        translated_values = _parse_stream_text_values(content, stream)
        failures: list[tuple[int, TranslationError]] = []
        for index, (stream_record, translated) in enumerate(
            zip(stream.records, translated_values)
        ):
            try:
                validated[index] = _validate_stream_record_text(
                    stream_record, translated, stream.protected_tokens
                )
            except TranslationError as exc:
                failures.append((index, exc))
        if not failures:
            return [value for value in validated if value is not None]
        retry_indices = [index for index, _ in failures]
        first_error = failures[0][1]
        if len(failures) > 1:
            first_error = TranslationError(
                f"{len(failures)} text segment(s) failed; first error: {first_error}"
            )
    except TranslationError as exc:
        first_error = exc
        salvaged = (
            _salvage_stream_response(content, stream)
            if content is not None
            else {}
        )
        for index, value in salvaged.items():
            validated[index] = value
        retry_indices = [
            index for index, value in enumerate(validated) if value is None
        ]

    if not retry_indices:
        return [value for value in validated if value is not None]

    retry_records = [records[index] for index in retry_indices]
    retry_stream = _build_translation_stream(retry_records)
    LOGGER.warning(
        "[cl_japanese2json] LLM validation failed for batch %d; retrying %d unresolved text segment(s) once: %s",
        batch_index + 1,
        len(retry_records),
        first_error,
    )
    retry_seed = _normalize_seed(seed + batch_index + 1_000_003)
    try:
        response = _call_llm(
            llm,
            _messages(
                system_prompt,
                retry_stream,
                retry_reason=str(first_error),
            ),
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            seed=retry_seed,
            stop_token=retry_stream.stop_token,
        )
        retry_values = _parse_stream_response(
            _content_from_response(response), retry_stream
        )
    except TranslationError as exc:
        raise TranslationError(
            f"Batch {batch_index + 1} failed validation after one retry: {exc}"
        ) from first_error

    for index, value in zip(retry_indices, retry_values):
        validated[index] = value
    if any(value is None for value in validated):
        raise TranslationError(
            f"Batch {batch_index + 1} retry did not resolve every failed record"
        )
    return [value for value in validated if value is not None]


def _rebuild(document: LexicalDocument) -> str:
    sections: list[str] = []
    for block in document.blocks:
        lines = [block.directive]
        for record in block.records:
            if record.translated is None:
                raise TranslationError(f"Record {record.record_id} has no validated translation")
            lines.append(f"* {record.translated}")
        sections.append("\n".join(lines))
    canonical = "\n\n".join(sections)
    if canonical:
        canonical += "\n"

    directive_count = sum(
        1
        for line in canonical.splitlines()
        if line in {"# Subjects", "# Common"} or line.startswith("# Scene")
    )
    bullet_count = sum(1 for line in canonical.splitlines() if line.startswith("* "))
    if directive_count != document.directive_count or bullet_count != document.bullet_count:
        raise TranslationError("Canonical Markdown reconstruction changed directive or bullet counts")
    return canonical


def translate_markdown(
    plain_text: str,
    llm: Any,
    system_prompt: str,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    top_p: float = 0.9,
    repetition_penalty: float = 1.05,
    seed: int = 1,
) -> str:
    """Translate Japanese bullet payloads and rebuild canonical Markdown."""

    document = lex_japanese_markdown(plain_text)
    records = document.records
    if records:
        batches = _make_batches(records, llm, system_prompt, max_tokens)
        LOGGER.info(
            "[cl_japanese2json] Prepared one protected translation stream for %d text segment(s); using %d inference request(s)",
            len(records),
            len(batches),
        )
        for batch_index, batch in enumerate(batches):
            LOGGER.info(
                "[cl_japanese2json] Translating batch %d/%d with %d text segment(s)",
                batch_index + 1,
                len(batches),
                len(batch),
            )
            translations = _translate_batch(
                batch,
                llm,
                system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                seed=seed,
                batch_index=batch_index,
            )
            for record, translated in zip(batch, translations):
                record.translated = translated
    return _rebuild(document)
