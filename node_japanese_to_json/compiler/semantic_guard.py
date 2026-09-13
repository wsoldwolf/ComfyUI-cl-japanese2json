"""Bounded, source-grounded review after structural translation checks."""

from ...common.semantic_review import SemanticReviewError, review_messages, run_review
from .errors import TranslationError
from .protected_text import restore_text


def source_key(record):
    # Identical constraints share one translation across Subject/Retention;
    # Subject introductions still have a different noun-phrase contract.
    return (restore_text(record.payload, record.payload.text),
            record.section == "Subjects" and record.sentence_index == 0)


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

        def invoke(messages, grammar_source, progress, abort):
            factory = getattr(llm, "compile_grammar", None)
            response = translator._call_llm(
                llm, messages, max_tokens=budget, temperature=temperature,
                top_p=top_p, repetition_penalty=repetition_penalty,
                seed=translator._normalize_seed(seed + 7_919 + sum(attempts.values())),
                stop_token="\nEND_REVIEW", grammar=factory(grammar_source) if callable(factory) else None,
                progress_callback=progress, interrupt_callback=abort)
            raw, reason = translator._response_content(response)
            raw = translator._validated_response_content(
                translator._strip_closed_thinking_blocks(raw), reason)
            return raw, {"usage": response.get("usage"), "finish_reason": reason}

        try:
            failures = run_review(
                pairs, policy="translation", context={}, invoke=invoke,
                logger=translator.LOGGER, label="[cl_japanese2json] Meaning review",
                events=debug_events, interrupt_callback=interrupt_callback,
                progress_callback=(lambda n: progress_callback(1, 1, 1, min(n, budget), budget))
                if progress_callback else None)
        except (SemanticReviewError, TranslationError) as exc:
            raise TranslationError(f"Meaning review could not validate output: {exc}") from exc
        for record in batch:
            reason = failures.get(record.record_id)
            if reason is None:
                for duplicate in groups[source_key(record)]:
                    duplicate.translated = record.translated
                pending.remove(record)
                continue
            count = attempts[record.record_id]
            if count >= limit:
                raise TranslationError(
                    f"Meaning review failed for {record.record_id} after {limit} repair(s): {reason}")
            attempts[record.record_id] += 1
            translator.LOGGER.warning(
                "[cl_japanese2json] Meaning repair %s %d/%d: %s",
                record.record_id, count + 1, limit, reason)
            repaired = translator._translate_batch(
                [record], llm, system_prompt, max_tokens=max_tokens,
                temperature=temperature, top_p=top_p, repetition_penalty=repetition_penalty,
                seed=translator._normalize_seed(seed + 104_729 * (count + 1)),
                retry_max=limit, batch_index=0, batch_count=1,
                debug_events=debug_events, progress_callback=progress_callback,
                interrupt_callback=interrupt_callback,
                initial_retry_reason=f"Meaning mismatch in {record.record_id}: {reason}. Translate the original source, not a paraphrase of the rejected output.")
            record.translated = repaired[0]
