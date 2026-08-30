from __future__ import annotations

import json
import unittest

from .helpers import module


jsongen = module("compiler.jsongen")
structures = module("compiler.structures")
errors = module("compiler.errors")
Emd, Scene, Soundscape = structures.Emd, structures.Scene, structures.Soundscape
EXPLICIT_DIALOGUE_ONLY = structures.VOCALIZATION_EXPLICIT_DIALOGUE_ONLY


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
        self.assertEqual(
            parsed["shots"][1]["prompt"][0],
            jsongen.NO_ACTIVE_SUBJECT_BLOCK,
        )

    def test_scene_without_subject_uses_fixed_no_character_definition(self) -> None:
        emd = Emd(
            subjects=["unused character."],
            scenes=[
                Scene(
                    shots=[
                        "One fast-moving mass of blue ice and two compact blue fireballs cross the frame."
                    ]
                )
            ],
        )
        block = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"][0]
        self.assertEqual(
            block,
            "subject_definitions:\n"
            "No character subject or reference-image person is active.",
        )

    def test_silent_scene_removes_audio_reference_clauses(self) -> None:
        emd = Emd(
            subjects=[
                "a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.",
                "Character B based on <Picture 2>. Use <Audio 2> as the voice quality reference. She wears a red coat.",
                "a tall and calm character.",
                "a voice based on <Audio 3>.",
            ],
            scenes=[
                Scene(
                    shots=[
                        "<Subject 1>, <Subject 2>, <Subject 3>, and <Subject 4> wave silently."
                    ]
                )
            ],
        )
        with self.assertLogs("cl_japanese2json", level="INFO") as captured:
            block = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"][0]
        self.assertEqual(
            block,
            "subject_definitions:\n"
            "<Subject 1> is a character whose appearance is based on <Picture 1>.\n"
            "<Subject 2> is Character B based on <Picture 2>. She wears a red coat.\n"
            "<Subject 3> is a tall and calm character.\n"
            "<Subject 4> is a character.",
        )
        self.assertNotIn("<Audio", block)
        self.assertNotIn("voice", block.lower())
        self.assertTrue(
            any("removed 3 Audio reference(s)" in line for line in captured.output)
        )

    def test_explicit_direct_speech_permission_keeps_audio_references(self) -> None:
        definition = (
            "a character based on <Picture 1> whose voice is based on <Audio 1>."
        )
        emd = Emd(
            subjects=[definition],
            scenes=[
                Scene(
                    shots=[
                        "<Subject 1> says <d>[Japanese]こんにちは</d>."
                    ],
                    soundscape=Soundscape(
                        vocalization=EXPLICIT_DIALOGUE_ONLY
                    ),
                )
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        self.assertIn("<Audio 1>", parsed["shots"][0]["prompt"][0])
        self.assertIn(
            "exact shot-synchronized dialogue",
            parsed["shots"][0]["prompt"][-2],
        )

    def test_speech_requires_explicit_permission_and_protected_dialogue(self) -> None:
        missing_permission = Emd(
            scenes=[Scene(shots=["A voice says <d>[Japanese]こんにちは</d>."])]
        )
        permission_without_dialogue = Emd(
            scenes=[
                Scene(
                    shots=["A silent action."],
                    soundscape=Soundscape(
                        vocalization=EXPLICIT_DIALOGUE_ONLY
                    ),
                )
            ]
        )
        unprotected_speech = Emd(
            scenes=[
                Scene(
                    shots=["A character says hello."],
                    soundscape=Soundscape(
                        vocalization=EXPLICIT_DIALOGUE_ONLY
                    ),
                )
            ]
        )
        for emd in (missing_permission, permission_without_dialogue, unprotected_speech):
            with self.subTest(emd=emd), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(emd)

    def test_negated_speech_cues_do_not_keep_audio_references(self) -> None:
        definition = (
            "a character based on <Picture 1> whose voice is based on <Audio 1>."
        )
        cues = (
            "<Subject 1> does not speak and remains silent.",
            "<Subject 1> moves without speaking.",
            "No one says anything.",
        )
        for shot in cues:
            with self.subTest(shot=shot):
                emd = Emd(subjects=[definition], scenes=[Scene(shots=[shot])])
                block = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"][0]
                self.assertNotIn("<Audio 1>", block)

    def test_direct_speech_and_escaped_subjects_are_not_extracted(self) -> None:
        emd = Emd(
            subjects=["one.", "two."],
            scenes=[
                Scene(
                    shots=[
                        r"<d>[Japanese]<Subject 1></d> and \<Subject 2\> are literal."
                    ],
                    soundscape=Soundscape(
                        vocalization=EXPLICIT_DIALOGUE_ONLY
                    ),
                )
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        self.assertEqual(
            parsed["shots"][0]["prompt"][0],
            jsongen.NO_ACTIVE_SUBJECT_BLOCK,
        )

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
            self.assertTrue(shot["prompt"][-2].startswith("overall_soundscape:\n"))

    def test_omitted_soundscape_falls_back_to_complete_silence(self) -> None:
        emd = Emd(
            subjects=["a character with voice reference <Audio 1>."],
            scenes=[Scene(shots=["<Subject 1> moves silently."])],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertEqual(prompt[-2], "overall_soundscape:\nComplete silence.")
        self.assertNotIn("<Audio 1>", prompt[0])

    def test_soundscape_is_an_explicit_allowlist(self) -> None:
        scene = Scene(
            shots=["An effects-only action occurs."],
            soundscape=Soundscape(
                environment="Soft grassland wind.",
                sound_effects="Footsteps and clothing rustle.",
            ),
        )
        prompt = json.loads(jsongen.generate_json(Emd(scenes=[scene])))["shots"][0]["prompt"]
        self.assertEqual(
            prompt[-2],
            "overall_soundscape:\n"
            "Environment: Soft grassland wind. "
            "Sound effects: Footsteps and clothing rustle. "
            "No other sound is present.",
        )

    def test_explicit_none_disables_each_sound_category(self) -> None:
        scene = Scene(
            soundscape=Soundscape(
                environment=structures.SOUND_NONE,
                sound_effects=structures.SOUND_NONE,
                vocalization=structures.SOUND_NONE,
            )
        )
        prompt = json.loads(jsongen.generate_json(Emd(scenes=[scene])))["shots"][0]["prompt"]
        self.assertEqual(prompt[-2], jsongen.COMPLETE_SILENCE)

    def test_soundscape_text_cannot_bypass_vocalization_policy(self) -> None:
        for soundscape in (
            Soundscape(environment="Reference <Audio 1>."),
            Soundscape(sound_effects="Play <d>[Japanese]こんにちは</d>."),
        ):
            with self.subTest(soundscape=soundscape), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(
                    Emd(scenes=[Scene(soundscape=soundscape)])
                )

    def test_ids_shots_integers_and_json_escaping(self) -> None:
        emd = Emd(
            scenes=[
                Scene(
                    duration=60,
                    shots=[r"<d>[Japanese]\<x\></d>", "second"],
                    soundscape=Soundscape(
                        vocalization=EXPLICIT_DIALOGUE_ONLY
                    ),
                )
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

        misplaced = json.loads(jsongen.generate_json(Emd(scenes=[Scene()])))
        prompt = misplaced["shots"][0]["prompt"]
        prompt.insert(0, prompt.pop(-2))
        with self.assertRaises(errors.JSONValidationError):
            jsongen.validate_final_json(
                json.dumps(misplaced, ensure_ascii=False, indent=2) + "\n"
            )
