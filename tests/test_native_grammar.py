"""Opt-in native parser checks: CL_TEST_GRAMMAR_MODEL points to a local GGUF.

Load only vocabulary on CPU. No weights, inference, or sampling of NULL handles.
LlamaGrammar.from_string alone does not exercise current native parsers.
"""

import os
from types import SimpleNamespace
import unittest

from .helpers import module


@unittest.skipUnless(os.environ.get("CL_TEST_GRAMMAR_MODEL"), "set CL_TEST_GRAMMAR_MODEL for native grammar validation")
class NativeGrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import llama_cpp
        cls.native = llama_cpp
        params = llama_cpp.llama_model_default_params()
        params.vocab_only = True
        params.n_gpu_layers = 0
        cls.model = llama_cpp.llama_model_load_from_file(
            os.environ["CL_TEST_GRAMMAR_MODEL"].encode("utf-8"), params)
        if not cls.model:
            raise RuntimeError("Could not load grammar-test vocabulary")
        cls.addClassCleanup(llama_cpp.llama_model_free, cls.model)
        cls.backend = module("common.gguf.runtime").LlamaBackend(llama_module=llama_cpp)
        cls.backend.llm = SimpleNamespace(_model=SimpleNamespace(
            vocab=llama_cpp.llama_model_get_vocab(cls.model)))

    def test_all_review_grammars_initialize_in_native_runtime(self):
        review = module("common.semantic_review")
        for policy in ("translation", "environment", "translation_patch", "translation_replacement"):
            with self.subTest(policy=policy):
                self.backend.compile_grammar(review.review_grammar(("R1",), policy=policy))

    def test_rejected_native_grammar_becomes_python_error(self):
        # The parser returns NULL; our wrapper must stop before sampling.
        for grammar in ('root ::= missing-rule', 'root ::= [a-z]{1,8192}'):
            with self.subTest(grammar=grammar), self.assertRaisesRegex(
                    module("common.errors").ModelLoadError, "NULL sampler"):
                self.backend.compile_grammar(grammar)
