"""Protected, delta-only GGUF enhancement for global reduced Markdown."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import threading
import time
from typing import Any, Callable

from ..node_prompt_merger.merger import merge_reduced_markdown
from .errors import (
    EnhancerInferenceStallError,
    EnhancerResponseError,
    PromptEnhancerError,
)
from .conflicts import (
    authoritative_time_of_day,
    conflicts_with_time_of_day,
    source_line_can_be_removed,
)
from .markdown import (
    PromptInspection,
    inspect_prompt,
    protected_user_fragments,
    rewrite_common,
)
from .profiles import BackgroundProfile, StyleProfile
from .prompt_loader import load_enhancer_system_prompt
from .protocol import EnhancementResponse, parse_enhancement_response


LOGGER = logging.getLogger("cl_prompt_enhancer")
INFERENCE_HEARTBEAT_SECONDS = 10.0
INFERENCE_FIRST_CHUNK_TIMEOUT_SECONDS = 90.0
INFERENCE_STALL_SECONDS = 60.0
_REFERENCE_RE = re.compile(r"<(Subject|Picture)\s+([1-9][0-9]*)>")


@dataclass(frozen=True)
class PromptEnhancementResult:
    markdown: str
    report: dict[str, Any]
    request_count: int
    retry_count: int
    events: tuple[dict[str, Any], ...]


def _protect_references(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = "SUB" if match.group(1) == "Subject" else "PIC"
        return f"CLPE{prefix}{match.group(2)}X"

    return _REFERENCE_RE.sub(replace, value)


def _response_content(response: Any) -> str:
    if not isinstance(response, dict):
        raise EnhancerResponseError("llama.cpp returned a non-object response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise EnhancerResponseError("llama.cpp response has no choice")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise EnhancerResponseError("Enhancer response was truncated because max_tokens was reached")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise EnhancerResponseError("llama.cpp response has no text content")
    content = message["content"].strip()
    if not content:
        raise EnhancerResponseError("llama.cpp response content is empty")
    return content


def _seed_for_attempt(seed: int, attempt_index: int) -> int:
    return ((seed - 1 + attempt_index) % 4_294_967_295) + 1


def _request_payload(
    source: PromptInspection,
    user: PromptInspection,
    style: StyleProfile,
    background: BackgroundProfile,
    additional_instruction: str,
    previous_error: str | None,
) -> dict[str, Any]:
    time_authority = authoritative_time_of_day(
        item.text for item in user.common
    )
    payload = {
        "source_common": [
            {"id": item.source_id, "text": _protect_references(item.text)}
            for item in source.common
        ],
        "immutable_user_prompt": {
            key: [_protect_references(value) for value in values]
            for key, values in user.payload().items()
        },
        "selected_style": {
            "profile_id": style.profile_id,
            "replacement_enabled": not style.passthrough,
            "description": style.description,
        },
        "selected_background": {
            "profile_id": background.profile_id,
            "replacement_enabled": not background.passthrough,
            "description": background.description,
            "minimum_lines": background.minimum_lines,
            "maximum_lines": background.maximum_lines,
        },
        "additional_instruction": _protect_references(additional_instruction.strip()),
    }
    if time_authority is not None:
        payload["authoritative_environment"] = {
            "time_of_day": time_authority,
        }
    if previous_error:
        payload["previous_validation_error"] = previous_error
    return payload


def _call_backend(
    backend: Any,
    *,
    system_prompt: str,
    payload: dict[str, Any],
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    seed: int,
    label: str,
    progress_callback: Callable[[str, int], None] | None,
    interrupt_callback: Callable[[], Any] | None,
) -> tuple[str, dict[str, Any]]:
    started = time.monotonic()
    heartbeat_stop = threading.Event()
    progress_lock = threading.Lock()
    streamed_chunks = 0
    last_chunk_at: float | None = None

    def token_progress(current: int) -> None:
        nonlocal streamed_chunks, last_chunk_at
        with progress_lock:
            streamed_chunks = current
            last_chunk_at = time.monotonic()
        if progress_callback is not None:
            progress_callback(label, current)

    def heartbeat() -> None:
        while not heartbeat_stop.wait(INFERENCE_HEARTBEAT_SECONDS):
            with progress_lock:
                current = streamed_chunks
                chunk_at = last_chunk_at
            age = "none" if chunk_at is None else f"{time.monotonic() - chunk_at:.1f}s"
            LOGGER.info(
                "[cl_prompt_enhancer] LLM inference active: %s streamed_chunks=%d last_chunk_age=%s",
                label,
                current,
                age,
            )

    def check_abort() -> bool:
        if interrupt_callback is not None and interrupt_callback():
            return True
        with progress_lock:
            current = streamed_chunks
            chunk_at = last_chunk_at
        now = time.monotonic()
        idle_start = chunk_at if chunk_at is not None else started
        idle = now - idle_start
        timeout = INFERENCE_STALL_SECONDS if chunk_at is not None else INFERENCE_FIRST_CHUNK_TIMEOUT_SECONDS
        if idle >= timeout:
            phase = "after output began" if chunk_at is not None else "before first output"
            raise EnhancerInferenceStallError(
                f"Prompt Enhancer inference stalled {phase}: no new streamed chunk for "
                f"{idle:.1f}s (received {current} chunk(s), timeout {timeout:.0f}s)"
            )
        return False

    thread = threading.Thread(
        target=heartbeat,
        name="cl_prompt_enhancer-llm-heartbeat",
        daemon=True,
    )
    thread.start()
    LOGGER.info("[cl_prompt_enhancer] LLM inference started: %s", label)
    try:
        response = backend.complete_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repeat_penalty=repetition_penalty,
            seed=seed,
            progress_callback=token_progress,
            interrupt_callback=check_abort,
        )
        return _response_content(response), {
            "usage": response.get("usage"),
            "finish_reason": response.get("choices", [{}])[0].get("finish_reason"),
            "elapsed_seconds": time.monotonic() - started,
            "streamed_chunks": streamed_chunks,
        }
    finally:
        heartbeat_stop.set()
        thread.join(timeout=0.25)
        LOGGER.info(
            "[cl_prompt_enhancer] LLM inference completed: %s elapsed=%.1fs streamed_chunks=%d",
            label,
            time.monotonic() - started,
            streamed_chunks,
        )


def _verify_user_prompt(final_markdown: str, user: PromptInspection) -> None:
    missing = [value for value in protected_user_fragments(user) if value not in final_markdown]
    if missing:
        raise PromptEnhancerError(
            "Enhanced output lost immutable user instruction(s): " + "; ".join(missing[:3])
        )


def enhance_reduced_markdown(
    source_markdown: str,
    user_prompt: str,
    *,
    style: StyleProfile,
    background: BackgroundProfile,
    backend: Any | None,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    seed: int,
    retry_max: int,
    additional_instruction: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
    interrupt_callback: Callable[[], Any] | None = None,
    debug_events: list[dict[str, Any]] | None = None,
) -> PromptEnhancementResult:
    source = inspect_prompt(source_markdown, "source_markdown")
    user = inspect_prompt(user_prompt, "user_prompt")
    time_authority = authoritative_time_of_day(
        item.text for item in user.common
    )
    explicit_conflicting_source_ids = {
        item.source_id
        for item in source.common
        if conflicts_with_time_of_day(item.text, time_authority)
    }
    conflicting_source_ids = {
        item.source_id
        for item in source.common
        if item.source_id in explicit_conflicting_source_ids
        and source_line_can_be_removed(item.text)
    }
    protected_conflicting_source_ids = (
        explicit_conflicting_source_ids - conflicting_source_ids
    )
    if conflicting_source_ids:
        LOGGER.warning(
            "[cl_prompt_enhancer] Removed %d automatic source background "
            "line(s) that conflict with immutable user time_of_day=%s: %s",
            len(conflicting_source_ids),
            time_authority,
            ", ".join(sorted(conflicting_source_ids)),
        )
    if protected_conflicting_source_ids:
        LOGGER.warning(
            "[cl_prompt_enhancer] Retained %d mixed-responsibility source "
            "line(s) despite a time-of-day conflict to avoid deleting "
            "character, camera, or audio instructions: %s",
            len(protected_conflicting_source_ids),
            ", ".join(sorted(protected_conflicting_source_ids)),
        )
    events: list[dict[str, Any]] = debug_events if debug_events is not None else []
    request_count = 0
    retries = 0
    response = EnhancementResponse(
        classifications={item.source_id: "keep" for item in source.common},
        background_lines=(),
        warnings=(),
    )
    needs_inference = not background.passthrough or (
        not style.passthrough and bool(source.common)
    )
    system_prompt = load_enhancer_system_prompt(style, background)
    if needs_inference:
        if backend is None:
            raise PromptEnhancerError("An LLM backend is required for the selected enhancer profiles")
        previous_error: Exception | None = None
        expected_ids = tuple(item.source_id for item in source.common)
        for attempt_index in range(retry_max + 1):
            request_count += 1
            payload = _request_payload(
                source,
                user,
                style,
                background,
                additional_instruction,
                str(previous_error) if previous_error is not None else None,
            )
            event: dict[str, Any] = {
                "label": f"enhancement attempt {attempt_index + 1}/{retry_max + 1}",
                "attempt": attempt_index + 1,
                "seed": _seed_for_attempt(seed, attempt_index),
                "system_prompt": system_prompt,
                "request_payload": payload,
            }
            events.append(event)
            try:
                raw, metadata = _call_backend(
                    backend,
                    system_prompt=system_prompt,
                    payload=payload,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                    seed=_seed_for_attempt(seed, attempt_index),
                    label=event["label"],
                    progress_callback=progress_callback,
                    interrupt_callback=interrupt_callback,
                )
                event.update(metadata)
                event["response_content"] = raw
                response = parse_enhancement_response(
                    raw,
                    expected_source_ids=expected_ids,
                    minimum_background_lines=background.minimum_lines,
                    maximum_background_lines=background.maximum_lines,
                )
                event["parsed_response"] = {
                    "classifications": response.classifications,
                    "background_lines": list(response.background_lines),
                    "warnings": list(response.warnings),
                }
                event["validation"] = "passed"
                break
            except Exception as exc:
                if type(exc).__name__ == "InterruptProcessingException":
                    raise
                event["validation"] = "failed"
                event["error"] = f"{type(exc).__name__}: {exc}"
                previous_error = exc
                retryable = isinstance(exc, EnhancerResponseError)
                if not retryable or attempt_index >= retry_max:
                    raise PromptEnhancerError(
                        f"Prompt enhancement failed after {attempt_index + 1} attempt(s): {exc}"
                    ) from exc
                retries += 1
                LOGGER.warning(
                    "[cl_prompt_enhancer] Response validation failed; retry %d/%d: %s",
                    attempt_index + 1,
                    retry_max,
                    exc,
                )
    conflicting_background_lines = tuple(
        value
        for value in response.background_lines
        if conflicts_with_time_of_day(value, time_authority)
    )
    if conflicting_background_lines:
        LOGGER.warning(
            "[cl_prompt_enhancer] Removed %d generated background line(s) "
            "that conflict with immutable user time_of_day=%s",
            len(conflicting_background_lines),
            time_authority,
        )
    accepted_background_lines = tuple(
        value
        for value in response.background_lines
        if value not in conflicting_background_lines
    )
    removed: set[str] = set(conflicting_source_ids)
    if not style.passthrough:
        removed.update(
            source_id
            for source_id, kind in response.classifications.items()
            if kind == "style"
        )
    if not background.passthrough:
        removed.update(
            source_id
            for source_id, kind in response.classifications.items()
            if kind == "background"
        )
    additions = (*style.directives, *accepted_background_lines)
    enhanced_base = rewrite_common(
        source_markdown,
        source,
        removed_source_ids=removed,
        prepended_bullets=tuple(additions),
    )
    try:
        final_markdown = merge_reduced_markdown(enhanced_base, user_prompt)
    except Exception as exc:
        raise PromptEnhancerError(f"Could not merge immutable user_prompt: {exc}") from exc
    _verify_user_prompt(final_markdown, user)
    inspect_prompt(final_markdown, "enhanced_markdown")
    report = {
        "schema_version": 1,
        "style_profile": style.profile_id,
        "background_detail": background.profile_id,
        "mode": "passthrough" if not needs_inference and not additions else "enhanced",
        "user_lines_preserved": len(user.bullets),
        "user_lines_modified": 0,
        "source_common_lines": len(source.common),
        "source_classifications": dict(response.classifications),
        "removed_source_common_ids": sorted(removed),
        "removed_conflicting_source_ids": sorted(conflicting_source_ids),
        "retained_mixed_conflicting_source_ids": sorted(
            protected_conflicting_source_ids
        ),
        "removed_conflicting_background_lines": len(
            conflicting_background_lines
        ),
        "authoritative_time_of_day": time_authority,
        "style_lines_added": len(style.directives),
        "background_lines_added": len(accepted_background_lines),
        "llm_requests": request_count,
        "retries": retries,
        "warnings": [
            *response.warnings,
            *(
                (
                    f"removed conflicting automatic source background ids: "
                    f"{', '.join(sorted(conflicting_source_ids))}",
                )
                if conflicting_source_ids
                else ()
            ),
            *(
                (
                    "retained mixed-responsibility source ids despite a "
                    f"time-of-day conflict: {', '.join(sorted(protected_conflicting_source_ids))}",
                )
                if protected_conflicting_source_ids
                else ()
            ),
            *(
                (
                    f"removed {len(conflicting_background_lines)} generated "
                    "background line(s) conflicting with immutable user "
                    f"time_of_day={time_authority}",
                )
                if conflicting_background_lines
                else ()
            ),
        ],
    }
    return PromptEnhancementResult(
        markdown=final_markdown,
        report=report,
        request_count=request_count,
        retry_count=retries,
        events=tuple(events),
    )
