from __future__ import annotations

import json
import unittest

from .helpers import module


jsongen = module("compiler.jsongen")
structures = module("compiler.structures")
errors = module("compiler.errors")
Emd, Scene = structures.Emd, structures.Scene


class JSONGenerationTests(unittest.TestCase):
    def test_subject_definitions_are_used_only_per_scene_and_sorted(self) -> None:
        emd = Emd(
            common_prompt=["<Subject 3> appears only in Common."],
            subjects=["one.", "two.", "three."],
            scenes=[
                Scene(shots=["<Subject 2> and <Subject 1> then <Subject 2>."]),
                Scene(shots=["No subject here."], is_continue=True),
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        block = parsed["shots"][0]["prompt"][0]
        self.assertEqual(
            block,
            "subject_definitions:\n<Subject 1> is one.\n<Subject 2> is two.",
        )
        self.assertEqual(parsed["shots"][1]["prompt"][0], "subject_definitions:")

    def test_direct_speech_and_escaped_subjects_are_not_extracted(self) -> None:
        emd = Emd(
            subjects=["one.", "two."],
            scenes=[
                Scene(
                    shots=[
                        r"<d>[Japanese]<Subject 1></d> and \<Subject 2\> are literal."
                    ]
                )
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        self.assertEqual(parsed["shots"][0]["prompt"][0], "subject_definitions:")

    def test_undefined_subject_warns_and_remains_in_shot(self) -> None:
        emd = Emd(scenes=[Scene(shots=["<Subject 4> acts."])])
        with self.assertLogs("cl_japanese2json", level="WARNING"):
            parsed = json.loads(jsongen.generate_json(emd))
        self.assertIn("<Subject 4>", parsed["shots"][0]["prompt"][1])
        self.assertEqual(parsed["shots"][0]["prompt"][0], "subject_definitions:")

    def test_continuation_and_reset_keys(self) -> None:
        emd = Emd(scenes=[Scene(), Scene(is_continue=True)])
        parsed = jsongen.validate_final_json(jsongen.generate_json(emd))
        first, second = parsed["shots"]
        self.assertEqual(first["context_length"], 0)
        self.assertEqual(first["audio_context_length"], 0)
        self.assertNotIn("continuation_mode", first)
        self.assertEqual(second["continuation_mode"], "guide")
        self.assertNotIn("context_length", second)

    def test_every_prompt_ends_with_music_disabled(self) -> None:
        emd = Emd(scenes=[Scene(), Scene(shots=["Action."], is_continue=True)])
        parsed = jsongen.validate_final_json(jsongen.generate_json(emd))
        for shot in parsed["shots"]:
            self.assertEqual(shot["prompt"][-1], "non_diegetic_music:\nN/A")

    def test_ids_shots_integers_and_json_escaping(self) -> None:
        emd = Emd(
            scenes=[
                Scene(duration=60, shots=[r"<d>[Japanese]\<x\></d>", "second"])
            ]
        )
        text = jsongen.generate_json(emd)
        parsed = json.loads(text)
        shot = parsed["shots"][0]
        self.assertEqual(shot["id"], "scene_1")
        self.assertEqual(shot["prompt"][1][:8], "[Shot 1]")
        self.assertEqual(shot["prompt"][2][:8], "[Shot 2]")
        self.assertIs(type(shot["duration_seconds"]), int)
        self.assertIs(type(parsed["defaults"]["steps"]), int)
        self.assertIn("Japanese", text)
        self.assertTrue(text.endswith("\n"))

    def test_steps_is_configurable_and_defaults_to_eight(self) -> None:
        emd = Emd(scenes=[Scene()])
        self.assertEqual(json.loads(jsongen.generate_json(emd))["defaults"]["steps"], 8)
        self.assertEqual(
            json.loads(jsongen.generate_json(emd, steps=12))["defaults"]["steps"],
            12,
        )
        for invalid in (0, 10001, True, 8.0):
            with self.subTest(invalid=invalid), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(emd, steps=invalid)

    def test_scene_count_limits(self) -> None:
        for scenes in ([], [Scene() for _ in range(129)]):
            with self.subTest(count=len(scenes)), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(Emd(scenes=scenes))

    def test_final_validator_rejects_bad_subset(self) -> None:
        bad = '{"prompt_prefix":"","defaults":{"duration_seconds":5,"steps":8},"shots":[]}\n'
        with self.assertRaises(errors.JSONValidationError):
            jsongen.validate_final_json(bad)

        missing_music = (
            '{"prompt_prefix":"","defaults":{"duration_seconds":5,"steps":8},'
            '"shots":[{"id":"scene_1","prompt":["subject_definitions:"],'
            '"duration_seconds":5,"context_length":0,"audio_context_length":0}]}\n'
        )
        with self.assertRaises(errors.JSONValidationError):
            jsongen.validate_final_json(missing_music)
