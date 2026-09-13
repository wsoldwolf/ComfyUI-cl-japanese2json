"""Vocabulary-independent, fail-closed review of model-produced prose.

The model judges meaning; Python checks coverage and bounds the transport.
No appearance or scenery vocabulary is translated or substituted here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time


PROMPT_PATH = Path(__file__).with_name("prompts") / "semantic_review.txt"
ENVIRONMENT_PROMPT_PATH = PROMPT_PATH.with_name("environment_review.txt")
TRANSLATION_PATCH_PROMPT_PATH = PROMPT_PATH.with_name("translation_patch.txt")
TRANSLATION_REPLACEMENT_PROMPT_PATH = PROMPT_PATH.with_name("translation_replacement.txt")


class SemanticReviewError(ValueError):
    pass


class SemanticReviewCancelled(RuntimeError):
    pass


class SemanticReviewCapacityError(RuntimeError):
    pass


def review_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8-sig").strip()


def review_fingerprint() -> str:
    return hashlib.sha256(b"\0".join(path.read_bytes() for path in (
        PROMPT_PATH, ENVIRONMENT_PROMPT_PATH, TRANSLATION_PATCH_PROMPT_PATH,
        TRANSLATION_REPLACEMENT_PROMPT_PATH))).hexdigest()


def review_messages(policy: str, pairs: list[dict], context: dict) -> list[dict]:
    path = {"environment": ENVIRONMENT_PROMPT_PATH,
            "translation_replacement": TRANSLATION_REPLACEMENT_PROMPT_PATH,
            "translation_patch": TRANSLATION_PATCH_PROMPT_PATH}.get(policy, PROMPT_PATH)
    prompt = path.read_text(encoding="utf-8-sig").strip()
    if policy == "environment":
        # Keep the source/candidate adjacent like a translation pair; small
        # models otherwise confuse source text with distant context evidence.
        pairs = [{**item, "candidate": "\n".join(context.get("candidate_common", []))} for item in pairs]
        context = {key: value for key, value in context.items() if key != "candidate_common"}
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(
            {"review_policy": policy, "pairs": pairs, "context": context},
            ensure_ascii=False, separators=(",", ":"))},
    ]


def review_batches(pairs, *, policy, context, backend, output_budget):
    """Fit complete, unsummarized source/authority data; never truncate facts."""
    accessor = getattr(backend, "effective_n_ctx", None)
    n_ctx = accessor() if callable(accessor) else accessor
    counter = getattr(backend, "count_input_tokens", None)
    offset = 0
    while offset < len(pairs):
        # A candidate contains the whole enhanced Common section. Review one
        # source at a time so small models cannot conflate several fact lists.
        size = min(1 if policy == "environment" else 4, len(pairs) - offset)
        while size:
            batch = pairs[offset:offset + size]
            messages = review_messages(policy, batch, context)
            if not (isinstance(n_ctx, int) and n_ctx > 0 and callable(counter)):
                break
            if counter(messages) + output_budget <= n_ctx:
                break
            size -= 1
        if not size:
            raise SemanticReviewCapacityError(
                "Meaning review source and authority exceed context; increase n_ctx or shorten the input")
        yield batch
        offset += size


def review_grammar(ids: tuple[str, ...], *, policy: str = "translation") -> str:
    if policy == "translation_replacement":
        if len(ids) != 1:
            raise SemanticReviewError("Meaning confirmation reviews exactly one current translation")
        return r'''root ::= "{" ws "\"after\"" ws ":" ws string ws "}" ws
string ::= "\"" char{1,8192} "\""
char ::= [^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})
ws ::= [ \t\n\r]*
'''
    if policy == "translation_patch":
        if len(ids) != 1:
            raise SemanticReviewError("Meaning confirmation reviews exactly one current translation")
        return r'''root ::= "{" ws "\"source_excerpt\"" ws ":" ws string ws "," ws "\"before\"" ws ":" ws string ws "," ws "\"after\"" ws ":" ws string ws "}" ws
string ::= "\"" char{1,512} "\""
char ::= [^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})
ws ::= [ \t\n\r]*
'''
    rows = []
    for key in ids:
        if policy == "environment":
            rows.append(json.dumps(f"EVIDENCE\t{key}\t") + ' evidence "\\n" ' +
                        json.dumps(f"CHECK\t{key}\t") + " verdict")
        else:
            rows.append(json.dumps(f"CHECK\t{key}\t") + " verdict")
    verdict = 'verdict ::= "PASS" | "FAIL\\t" reason\n'
    return ('root ::= ' + ' "\\n" '.join(rows) + ' "\\n"?\n' + verdict +
            'reason ::= [^\\t\\n\\r]{1,256}\n'
            'evidence ::= [^\\t\\n\\r]{1,512}\n')


def run_review(pairs, *, policy, context, invoke, logger, label,
               events=None, interrupt_callback=None, progress_callback=None):
    """One review inference with heartbeat; invoke returns (text, metadata).

    Retry budgets belong to callers; this helper never hides failed reviews.
    """
    if not pairs:
        return {}
    ids = tuple(item["id"] for item in pairs)
    if len(set(ids)) != len(ids):
        raise SemanticReviewError("Meaning review input ids must be unique")
    messages = review_messages(policy, pairs, context)
    event = {"label": label, "stage": "semantic_review", "policy": policy,
             "system_prompt": messages[0]["content"],
             "user_request": messages[1]["content"], "validation": "pending"}
    if events is not None:
        events.append(event)
    started = time.monotonic()
    state = {"last": started, "chunks": 0}
    stop = threading.Event()

    def progress(count):
        state.update(last=time.monotonic(), chunks=count)
        if progress_callback is not None:
            progress_callback(count)

    def abort():
        if interrupt_callback is not None and interrupt_callback():
            raise SemanticReviewCancelled(f"{label} interrupted")
        timeout = 60.0 if state["chunks"] else 120.0
        if time.monotonic() - state["last"] >= timeout:
            raise SemanticReviewError(f"{label} stalled with no output for {timeout:.0f}s")
        return False

    def heartbeat():
        while not stop.wait(10.0):
            logger.info("%s active: elapsed=%.1fs chunks=%d", label,
                        time.monotonic() - started, state["chunks"])

    thread = threading.Thread(target=heartbeat, daemon=True, name="semantic-review-heartbeat")
    logger.info("%s started: %d item(s)", label, len(ids))
    thread.start()
    try:
        abort()
        raw, metadata = invoke(messages, review_grammar(ids, policy=policy), progress, abort)
        event.update(metadata)
        event["response_content"] = raw
        if policy == "translation_replacement":
            failures = parse_translation_replacement(raw, pairs)
        elif policy == "translation_patch":
            failures = parse_translation_patches(raw, ids, source=pairs[0]["source"])
        else:
            failures = parse_review(raw, ids, policy=policy)
        if policy in {"translation_patch", "translation_replacement"}:
            event["patches"] = failures
            event["validation"] = "passed"  # Schema validated; caller checks/applies the literal proposal.
        else:
            event["failures"] = failures
            event["validation"] = "failed" if failures else "passed"
        return failures
    except Exception as exc:
        event.update(validation="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        stop.set()
        thread.join(timeout=0.25)
        event["elapsed_seconds"] = time.monotonic() - started
        logger.info("%s completed: %s elapsed=%.1fs", label,
                    event["validation"], event["elapsed_seconds"])


def parse_review(raw: str, expected_ids: tuple[str, ...], *, policy: str = "translation") -> dict[str, str]:
    """Return failing IDs and reasons; absent/duplicate verdicts never mean PASS."""
    verdicts: dict[str, str] = {}
    lines = raw.strip().splitlines()
    if policy == "environment":
        if len(lines) != 2 * len(expected_ids):
            raise SemanticReviewError("Environment review requires evidence and verdict for every id")
        for offset, record_id in enumerate(expected_ids):
            parts = lines[offset * 2].split("\t")
            if len(parts) != 3 or parts[:2] != ["EVIDENCE", record_id] or not parts[2].strip():
                raise SemanticReviewError(f"Environment review evidence missing for {record_id}")
            if "MISSING" in parts[2] and lines[offset * 2 + 1].endswith("\tPASS"):
                raise SemanticReviewError(f"{record_id}: evidence lists a missing source fact but verdict is PASS: {parts[2]}")
        lines = lines[1::2]
    for line in lines:
        parts = line.strip().split("\t")
        if len(parts) < 3 or parts[0] != "CHECK":
            raise SemanticReviewError("Meaning review requires CHECK, id, PASS or FAIL TAB records")
        _, record_id, verdict, *reason = parts
        if record_id not in expected_ids or record_id in verdicts:
            raise SemanticReviewError(f"Meaning review has unknown or repeated id {record_id!r}")
        if verdict == "PASS" and not reason:
            verdicts[record_id] = ""
        elif verdict == "FAIL" and len(reason) == 1 and reason[0].strip():
            verdicts[record_id] = reason[0].strip()[:600]
        else:
            raise SemanticReviewError(f"Meaning review has invalid verdict for {record_id}")
    if tuple(verdicts) != expected_ids:
        raise SemanticReviewError("Meaning review did not cover every requested id in order")
    return {key: value for key, value in verdicts.items() if value}


def parse_translation_replacement(raw: str, pairs: list[dict]) -> dict[str, dict[str, str]]:
    """Bind a full-unit repair to the current pair without model-copied locators."""
    if len(pairs) != 1:
        raise SemanticReviewError("Meaning confirmation reviews exactly one current translation")

    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SemanticReviewError(f"Meaning confirmation has duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as exc:
        raise SemanticReviewError(f"Meaning confirmation requires one complete JSON object: {exc}") from exc
    if (not isinstance(data, dict) or set(data) != {"after"}
            or not isinstance(data["after"], str) or not data["after"].strip()
            or len(data["after"]) > 8192):
        raise SemanticReviewError("Meaning confirmation requires one nonempty after string (maximum 8192 characters)")
    pair = pairs[0]
    return {pair["id"]: {"source": pair["source"], "before": pair["candidate"], "after": data["after"]}}


def parse_translation_patches(raw: str, expected_ids: tuple[str, ...], *, source=None) -> dict[str, dict[str, str]]:
    """Parse literal evidence and a replacement, not an unconstrained critique."""
    if len(expected_ids) != 1:
        raise SemanticReviewError("Meaning confirmation reviews exactly one current translation")

    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SemanticReviewError(f"Meaning confirmation has duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as exc:
        raise SemanticReviewError(f"Meaning confirmation requires one complete JSON object: {exc}") from exc
    expected = {"source_excerpt", "before", "after"}
    if not isinstance(data, dict) or set(data) != expected or any(
            not isinstance(value, str) or not value.strip() for value in data.values()):
        raise SemanticReviewError("Meaning confirmation requires source_excerpt, before and after strings")
    if source is not None and data["source_excerpt"] not in source:
        raise SemanticReviewError("source_excerpt must be copied exactly from the current source")
    return {expected_ids[0]: {"source": data["source_excerpt"], "before": data["before"], "after": data["after"]}}
