from __future__ import annotations

import json
import re
import unittest

from .helpers import FakeLLM, default_stream_translation, module, request_records

compiler = module("node_japanese_to_json.compiler.llmj2e")
grammar_module = module("node_japanese_to_json.compiler.stream_grammar")
errors = module("node_japanese_to_json.compiler.errors")


class GrammarLLM(FakeLLM):
    def __init__(self, responses=None):
        super().__init__(responses)
        self.grammars = []

    def compile_grammar(self, source):
        self.grammars.append(source)
        return source


class StreamGrammarTests(unittest.TestCase):
    def test_middle_reference_failure_constrains_only_unresolved_record(self):
        source = (
            '# 保持分析\n* <Subject 1> 完全に保持: 青い服と<Picture 2>由来の外観を維持する。'
            '\n# シーン\n## ショット\n* カメラが移動する。'
        )
        def response(kwargs):
            def transform(record):
                if record['section'] == 'Retention':
                    token = record['protected_placeholders'][0]
                    return (f'Keep the blue clothing and appearance from {token}.'
                            if 'grammar' in kwargs else 'Keep the blue clothing and appearance.')
                return 'The camera moves.'
            return default_stream_translation(kwargs['messages'], transform)

        llm = GrammarLLM([response, response])
        events = []
        output = compiler.translate_markdown(
            source, llm, 'system', retry_max=1, debug_events=events,
        )
        self.assertEqual(len(llm.calls), 2)
        self.assertNotIn('grammar', llm.calls[0])
        self.assertIn('grammar', llm.calls[1])
        self.assertEqual(len(request_records(llm.calls[1]['messages'])), 1)
        self.assertIn('appearance from <Picture 2>', output)
        self.assertEqual(output.count('The camera moves.'), 1)
        self.assertEqual([event['transport'] for event in events], ['validated_stream', 'gbnf'])
        self.assertEqual(events[1]['grammar'], llm.grammars[0])

    def test_retry_budget_zero_does_not_start_grammar_call(self):
        def omit(kwargs):
            return default_stream_translation(kwargs['messages'], lambda _: 'She walks.')
        llm = GrammarLLM([omit])
        with self.assertRaisesRegex(errors.TranslationError, 'retry_max=0'):
            compiler.translate_markdown(
                '# シーン\n## ショット\n* <Subject 1>が歩く。', llm, 'system', retry_max=0,
            )
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.grammars, [])

    def test_missing_envelope_does_not_hide_missing_reference(self):
        llm = GrammarLLM(['She walks.'])
        output = compiler.translate_markdown(
            '# シーン\n## ショット\n* <Subject 1>が歩く。', llm, 'system', retry_max=1,
        )
        self.assertIn('<Subject 1>', output)
        self.assertIn('grammar', llm.calls[1])

    def test_invalid_constrained_output_is_not_patched_or_accepted(self):
        # A fake backend ignores the supplied grammar. The independent validator
        # must still reject it; sentence-head reinsertion would mask that failure.
        def omit(kwargs):
            return default_stream_translation(kwargs['messages'], lambda _: 'She walks.')
        llm = GrammarLLM([omit, omit])
        with self.assertRaisesRegex(errors.TranslationError, 'occurred 0 time'):
            compiler.translate_markdown(
                '# シーン\n## ショット\n* <Subject 1>が歩く。', llm, 'system', retry_max=1,
            )
        self.assertIn('grammar', llm.calls[1])

    def test_grammar_keeps_repeated_references_and_dialogue_in_source_order(self):
        source = (
            '# シーン\n## ショット\n'
            '* <Subject 1>が「前へ！」と言い、<Subject 1>が<Picture 2>を見る。'
        )
        record = compiler.lex_japanese_markdown(source).records[0]
        stream = compiler._build_translation_stream([record])
        grammar = grammar_module.translation_stream_grammar(stream)
        root = grammar.splitlines()[0]
        literals = [json.loads(value) for value in re.findall(r'"(?:\\.|[^"\\])*"', root)]
        tokens = sorted(record.payload.tokens, key=record.payload.text.index)
        self.assertEqual(literals, [
            'CLJT0D1X\n', stream.records[0].marker_token + ' ', *tokens, '\n', stream.stop_token,
        ])
        self.assertEqual(len(tokens), 4)
        self.assertNotIn('前へ', grammar)
        self.assertNotIn('<Subject 1>', grammar)

    def test_duplicate_reference_triggers_constrained_retry(self):
        def response(kwargs):
            def transform(record):
                token = record['protected_placeholders'][0]
                return f'{token} walks.' if 'grammar' in kwargs else f'{token} walks beside {token}.'
            return default_stream_translation(kwargs['messages'], transform)
        llm = GrammarLLM([response, response])
        output = compiler.translate_markdown(
            '# シーン\n## ショット\n* <Subject 1>が歩く。', llm, 'system', retry_max=1,
        )
        self.assertEqual(output.count('<Subject 1>'), 1)
        self.assertEqual(len(llm.grammars), 1)


if __name__ == '__main__':
    unittest.main()
