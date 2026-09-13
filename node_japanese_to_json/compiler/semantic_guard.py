"""Bounded, source-grounded review after structural translation checks."""

from collections import Counter, defaultdict, deque
import re
import unicodedata

from ...common.semantic_review import SemanticReviewError, review_messages, run_review
from .errors import TranslationError
from .protected_text import protect_text, restore_text


def source_key(record):
    # Identical constraints share one translation across Subject/Retention;
    # Subject introductions still have a different noun-phrase contract.
    return (restore_text(record.payload, record.payload.text),
            record.section == "Subjects" and record.sentence_index == 0)


def _meaningful_text(text):
    # A confirmation may return the current wording. Formatting-only changes
    # do not justify rewriting it or consuming a semantic repair attempt.
    # Preserve word boundaries, punctuation and numeric signs/decimal points.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _source_focus(record, reason):
    # Free-form reviews can quote an obsolete candidate or invent a replacement.
    # Only excerpts that literally exist in the original source may guide focus.
    source = source_key(record)[0]
    quoted = re.findall(r'"([^"\r\n]+)"|“([^”\r\n]+)”|「([^」\r\n]+)」', reason)
    return sorted(set(value for group in quoted for value in group
                      if value and value in source), key=lambda value: (-len(value), value))[:8]


def _validated_patch(record, patch):
    from . import llmj2e as translator

    source, before, after = (patch[key] for key in ("source", "before", "after"))
    if source not in source_key(record)[0]:
        raise SemanticReviewError("source_excerpt must be copied exactly from the current source")
    if record.translated.count(before) != 1:
        raise SemanticReviewError("before must be copied exactly from the current candidate and occur once; include more surrounding text")
    if _meaningful_text(before) == _meaningful_text(after):
        return record.translated
    candidate = record.translated.replace(before, after, 1)
    payload = protect_text(candidate, namespace="MeaningPatch")
    if Counter(payload.replacements.values()) != Counter(record.payload.replacements.values()):
        raise SemanticReviewError("A meaning patch must preserve every reference tag and protected dialogue exactly")
    # The stored translation is already restored. Rebind its protected content
    # to the original record before running the normal compiler validation.
    tokens = defaultdict(deque)
    for token, value in record.payload.replacements.items():
        tokens[value].append(token)
    protected = payload.text
    for token, value in payload.replacements.items():
        protected = protected.replace(token, tokens[value].popleft(), 1)
    return translator._validate_translation_text(record, protected)


def audit_translations(records, llm, system_prompt, *, scope, max_tokens,
                       temperature, top_p, repetition_penalty, seed, retry_max,
                       debug_events, progress_callback, interrupt_callback):
    from . import llmj2e as translator

    selected = [record for record in records if scope == "all" or
                record.section in {"Subjects", "Retention", "Common"}]
    groups = {}
    for record in selected:
        groups.setdefault(source_key(record), []).append(record)
    pending = [items[0] for items in groups.values()]
    if not pending:
        return
    # Always finite, even with legacy unlimited transport retries.
    limit = 2 if retry_max == -1 else min(2, retry_max)
    budget = min(max_tokens, 1024)
    attempts = {record.record_id: 0 for record in pending}
    request_index = 0

    def assess(pairs, *, policy="translation", context=None):
        nonlocal request_index
        context = context or {}
        output_budget = min(max_tokens, 2048) if policy in {"translation_patch", "translation_replacement"} else budget
        messages = review_messages(policy, pairs, context)
        n_ctx = translator._effective_n_ctx(llm)
        if n_ctx and translator._count_input_tokens(llm, messages) + output_budget > n_ctx:
            raise TranslationError(f"Meaning review {pairs[0]['id']} exceeds context; increase n_ctx or shorten the source bullet")
        request_index += 1

        def invoke(messages, grammar_source, progress, abort):
            factory = getattr(llm, "compile_grammar", None)
            response = translator._call_llm(
                llm, messages, max_tokens=output_budget, temperature=temperature,
                top_p=top_p, repetition_penalty=repetition_penalty,
                seed=translator._normalize_seed(seed + 7_919 + request_index),
                stop_token="\nEND_REVIEW", grammar=factory(grammar_source) if callable(factory) else None,
                progress_callback=progress, interrupt_callback=abort)
            raw, finish_reason = translator._response_content(response)
            raw = translator._validated_response_content(
                translator._strip_closed_thinking_blocks(raw), finish_reason)
            return raw, {"usage": response.get("usage"), "finish_reason": finish_reason}

        return run_review(
            pairs, policy=policy, context=context, invoke=invoke,
            logger=translator.LOGGER,
            label="[cl_japanese2json] Meaning confirmation" if policy in {"translation_patch", "translation_replacement"}
            else "[cl_japanese2json] Meaning review",
            events=debug_events, interrupt_callback=interrupt_callback,
            progress_callback=(lambda n: progress_callback(1, 1, 1, min(n, output_budget), output_budget))
            if progress_callback else None)

    def confirm(record, reason):
        pairs = [{"id": record.record_id, "source": source_key(record)[0],
                  "candidate": record.translated}]
        focus = _source_focus(record, reason)
        feedback = {"source_focus": focus} if focus else {}
        # Malformed edits are reviewer errors, not translation repairs.
        # Recheck once using a whole-unit replacement. Python owns the source
        # and target literals so a model cannot repeat the same copy error.
        for confirmation_attempt in range(2):
            try:
                policy = "translation_replacement" if confirmation_attempt else "translation_patch"
                patches = assess(pairs, policy=policy, context=feedback)
                patch = patches.get(record.record_id)
                if patch is None:
                    raise SemanticReviewError("Meaning confirmation omitted the requested translation")
                repaired = _validated_patch(record, patch)
                return (None, None) if repaired == record.translated else (repaired, patch)
            except (SemanticReviewError, TranslationError) as exc:
                if confirmation_attempt:
                    raise TranslationError(
                        f"Meaning confirmation could not validate {record.record_id} after 2 checks: {exc}") from exc
                feedback = {"source_focus": focus, "validation_feedback": str(exc)}
                translator.LOGGER.warning(
                    "[cl_japanese2json] Rechecking invalid meaning confirmation %s with a source-bound full-unit repair: %s",
                    record.record_id, exc)

    while pending:
        batch = []
        for record in pending[:4]:
            proposed = [*batch, record]
            pairs = [{"id": item.record_id, "source": source_key(item)[0],
                      "candidate": item.translated} for item in proposed]
            messages = review_messages("translation", pairs, {})
            n_ctx = translator._effective_n_ctx(llm)
            if n_ctx and translator._count_input_tokens(llm, messages) + budget > n_ctx:
                if not batch:
                    raise TranslationError(f"Meaning review {record.record_id} exceeds context; increase n_ctx or shorten the source bullet")
                break
            batch = proposed
        pairs = [{"id": item.record_id, "source": source_key(item)[0],
                  "candidate": item.translated} for item in batch]

        try:
            failures = assess(pairs)
        except (SemanticReviewError, TranslationError) as exc:
            raise TranslationError(f"Meaning review could not validate output: {exc}") from exc
        for record in batch:
            reason = failures.get(record.record_id)
            repaired, patch = (None, None)
            if reason is not None:
                repaired, patch = confirm(record, reason)
                if repaired is None:
                    translator.LOGGER.info(
                        "[cl_japanese2json] Meaning confirmation %s passed; retained current translation",
                        record.record_id)
                    reason = None
            if reason is None:
                for duplicate in groups[source_key(record)]:
                    duplicate.translated = record.translated
                pending.remove(record)
                continue
            count = attempts[record.record_id]
            correction = f"source={patch['source']!r}; {patch['before']!r} -> {patch['after']!r}"
            if count >= limit:
                raise TranslationError(
                    f"Meaning review failed for {record.record_id} after {limit} repair(s): {correction}")
            attempts[record.record_id] += 1
            translator.LOGGER.warning(
                "[cl_japanese2json] Meaning repair %s %d/%d: %s",
                record.record_id, count + 1, limit, correction)
            if debug_events is not None:
                debug_events.append({"stage": "semantic_patch", "record_id": record.record_id,
                                     "repair": count + 1, "patch": patch,
                                     "before": record.translated, "after": repaired})
            record.translated = repaired
