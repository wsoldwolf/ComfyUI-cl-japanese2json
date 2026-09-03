from __future__ import annotations

import json
import unittest

from .helpers import module


jsongen = module("compiler.jsongen")
structures = module("compiler.structures")
errors = module("compiler.errors")
Emd = structures.Emd
RetentionRule = structures.RetentionRule
Scene = structures.Scene
Shot = structures.Shot
Soundscape = structures.Soundscape
EXPLICIT_DIALOGUE_ONLY = structures.VOCALIZATION_EXPLICIT_DIALOGUE_ONLY


def make_shot(*lines: str, start_ms: int = 0) -> Shot:
    return Shot(start_ms=start_ms, lines=list(lines))


class JSONGenerationTests(unittest.TestCase):
    def test_prompt_has_exact_official_six_section_order(self) -> None:
        emd = Emd(
            subjects=["a character based on <Picture 1>."],
            scenes=[
                Scene(
                    preamble=["A clean anime visual style."],
                    shots=[make_shot("<Subject 1> walks forward.")],
                )
            ],
        )
        parsed = jsongen.validate_final_json(jsongen.generate_json(emd))
        self.assertEqual(parsed["prompt_prefix"], "")
        prompt = parsed["shots"][0]["prompt"]
        self.assertEqual(len(prompt), 6)
        prefixes = (
            "subject_definitions:\n",
            "summary:\n",
            "retention_analysis:\n",
            "detailed_description:\n",
            "overall_soundscape:\n",
            "non_diegetic_music:\n",
        )
        self.assertEqual(tuple(item[: len(prefix)] for item, prefix in zip(prompt, prefixes)), prefixes)
        self.assertEqual(prompt[-1], "non_diegetic_music:\nN/A")

    def test_subject_definitions_are_filtered_and_subjectless_scene_is_explicit(self) -> None:
        emd = Emd(
            subjects=["one.", "two.", "three."],
            scenes=[
                Scene(shots=[make_shot("<Subject 2> and <Subject 1> move.")]),
                Scene(shots=[make_shot("One blue ice mass crosses the frame.")]),
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        self.assertEqual(
            parsed["shots"][0]["prompt"][0],
            "subject_definitions:\n<Subject 1> is one.\n<Subject 2> is two.",
        )
        self.assertEqual(parsed["shots"][1]["prompt"][0], jsongen.NO_ACTIVE_SUBJECT_BLOCK)
        self.assertEqual(parsed["shots"][1]["prompt"][2], jsongen.NO_ACTIVE_RETENTION)

    def test_retention_rules_are_scene_filtered_and_defaults_are_deterministic(self) -> None:
        emd = Emd(
            subjects=["one.", "two.", "a style from <Picture 2>."],
            retention_rules=[
                RetentionRule(1, "partially_preserved", "Only the costume is retained."),
                RetentionRule(3, "attribute_transfer", "Transfer photoreal lighting.", 1),
            ],
            scenes=[
                Scene(shots=[make_shot("Apply <Subject 3> to <Subject 1>.")]),
                Scene(shots=[make_shot("<Subject 2> stands alone.")]),
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        first = parsed["shots"][0]["prompt"][2]
        self.assertIn("<Subject 1> (used in [Shot 1]): partially_preserved", first)
        self.assertIn(
            "<Subject 3> (applied to <Subject 1> in [Shot 1]): attribute_transfer",
            first,
        )
        second = parsed["shots"][1]["prompt"][2]
        self.assertIn("<Subject 2> (used in [Shot 1]): fully_preserved", second)
        self.assertNotIn("<Subject 1>", second)
        self.assertNotIn("<Subject 3>", second)

    def test_active_attribute_transfer_requires_active_target(self) -> None:
        emd = Emd(
            subjects=["one.", "a style."],
            retention_rules=[
                RetentionRule(2, "attribute_transfer", "Transfer style.", 1)
            ],
            scenes=[Scene(shots=[make_shot("Use <Subject 2> as the style.")])],
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "target Subject"):
            jsongen.generate_json(emd)

    def test_silent_scene_removes_audio_and_does_not_activate_audio_reference(self) -> None:
        emd = Emd(
            subjects=[
                "a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>."
            ],
            scenes=[Scene(shots=[make_shot("<Subject 1> walks silently.")])],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertNotIn("<Audio 1>", prompt[0])
        self.assertNotIn("<Audio 1>", prompt[1])
        self.assertNotIn("<Audio 1>", prompt[2])
        self.assertNotIn("<Audio 1>", prompt[3])
        self.assertEqual(prompt[4], jsongen.COMPLETE_SILENCE)

    def test_dialogue_generates_audio_definition_summary_retention_and_shot_reference(self) -> None:
        emd = Emd(
            subjects=[
                "a character based on <Picture 1> whose voice is based on <Audio 1>."
            ],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "<Subject 1> (S1) says <d>[Japanese]こんにちは</d>."
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertIn(
            "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).",
            prompt[0],
        )
        self.assertIn("[reference generation + audio reference]", prompt[1])
        self.assertIn("<Audio 1>: reference", prompt[2])
        self.assertNotIn("(S1)", prompt[2])
        self.assertIn("use <Audio 1> only as a voice-timbre", prompt[3])
        self.assertIn("exact shot-synchronized dialogue", prompt[4])

    def test_subject_speaker_pair_accepts_no_intervening_space(self) -> None:
        emd = Emd(
            subjects=["a character whose voice is based on <Audio 1>."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "<Subject 1>(S1) says <d>[Japanese]こんにちは</d>."
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        jsongen.generate_json(emd)

    def test_speech_requires_permission_direct_speech_and_speaker_pair(self) -> None:
        invalid = (
            Emd(
                subjects=["one."],
                scenes=[
                    Scene(shots=[make_shot("<Subject 1> (S1) says <d>[Japanese]x</d>.")])
                ],
            ),
            Emd(
                scenes=[
                    Scene(
                        shots=[make_shot("A silent action.")],
                        soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                    )
                ],
            ),
            Emd(
                subjects=["one."],
                scenes=[
                    Scene(
                        shots=[make_shot("<Subject 1> says <d>[Japanese]x</d>.")],
                        soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                    )
                ],
            ),
            Emd(
                subjects=["one."],
                scenes=[
                    Scene(
                        shots=[make_shot("<Subject 1> (S1) says hello.")],
                        soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                    )
                ],
            ),
        )
        for emd in invalid:
            with self.subTest(emd=emd), self.assertRaises(errors.JSONGenerationError):
                jsongen.generate_json(emd)

    def test_positive_speech_cue_without_same_line_dialogue_is_rejected(self) -> None:
        emd = Emd(
            subjects=["one."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "<Subject 1> speaks softly.",
                            "<Subject 1> (S1) says <d>[Japanese]はい</d>.",
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        with self.assertRaises(errors.JSONGenerationError):
            jsongen.generate_json(emd)

    def test_speaker_ids_are_global_unique_and_follow_vocal_event_order(self) -> None:
        valid = Emd(
            subjects=["one.", "two."],
            scenes=[
                Scene(
                    shots=[make_shot("<Subject 2> (S1) says <d>[Japanese]a</d>.")],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                ),
                Scene(
                    shots=[make_shot("<Subject 1> (S2) says <d>[Japanese]b</d>.")],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                ),
            ],
        )
        jsongen.generate_json(valid)

        invalid = (
            Emd(
                subjects=["one."],
                scenes=[
                    Scene(
                        shots=[make_shot("<Subject 1> (S2) says <d>[Japanese]a</d>.")],
                        soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                    )
                ],
            ),
            Emd(
                subjects=["one.", "two."],
                scenes=[
                    Scene(
                        shots=[
                            make_shot(
                                "<Subject 1> (S1) says <d>[Japanese]a</d>.",
                                "<Subject 2> (S1) says <d>[Japanese]b</d>.",
                            )
                        ],
                        soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                    )
                ],
            ),
        )
        for emd in invalid:
            with self.subTest(emd=emd), self.assertRaises(errors.JSONGenerationError):
                jsongen.generate_json(emd)

    def test_negated_speech_cues_remain_silent(self) -> None:
        definition = "a character whose voice is based on <Audio 1>."
        for line in (
            "<Subject 1> does not speak and remains silent.",
            "<Subject 1> moves without speaking.",
            "No one says anything while <Subject 1> waits.",
        ):
            with self.subTest(line=line):
                emd = Emd(subjects=[definition], scenes=[Scene(shots=[make_shot(line)])])
                prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
                self.assertNotIn("<Audio 1>", "\n".join(prompt))

    def test_shot_boundaries_and_timestamps_are_rendered_inside_one_section(self) -> None:
        emd = Emd(
            scenes=[
                Scene(
                    duration=8,
                    preamble=["A cinematic visual style."],
                    shots=[
                        make_shot("The opening action occurs."),
                        make_shot("The second action occurs.", start_ms=1350),
                    ],
                )
            ]
        )
        detailed = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"][3]
        self.assertIn("A cinematic visual style.\n[Shot 1]", detailed)
        self.assertIn("[Shot 2] At 00:01.350,", detailed)
        self.assertEqual(detailed.count("[Shot "), 2)

    def test_soundscape_is_an_explicit_allowlist(self) -> None:
        scene = Scene(
            shots=[make_shot("An effects-only action occurs.")],
            soundscape=Soundscape(
                environment="Soft grassland wind.",
                sound_effects="Footsteps and clothing rustle.",
            ),
        )
        prompt = json.loads(jsongen.generate_json(Emd(scenes=[scene])))["shots"][0]["prompt"]
        self.assertEqual(
            prompt[4],
            "overall_soundscape:\n"
            "Environment: Soft grassland wind. "
            "Sound effects: Footsteps and clothing rustle. "
            "No other sound is present.",
        )

    def test_soundscape_text_cannot_bypass_vocalization_policy(self) -> None:
        for soundscape in (
            Soundscape(environment="Reference <Audio 1>."),
            Soundscape(sound_effects="Play <d>[Japanese]x</d>."),
        ):
            with self.subTest(soundscape=soundscape), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(
                    Emd(scenes=[Scene(shots=[make_shot("Action.")], soundscape=soundscape)])
                )

    def test_continuation_reset_steps_and_json_escaping(self) -> None:
        emd = Emd(
            scenes=[
                Scene(shots=[make_shot(r"A literal \\<x\\> appears.")]),
                Scene(shots=[make_shot("Next action.")], is_continue=True),
            ]
        )
        parsed = jsongen.validate_final_json(jsongen.generate_json(emd, steps=12))
        first, second = parsed["shots"]
        self.assertEqual(parsed["defaults"]["steps"], 12)
        self.assertEqual(first["context_length"], 0)
        self.assertEqual(first["audio_context_length"], 0)
        self.assertEqual(second["continuation_mode"], "guide")
        self.assertNotIn("context_length", second)

    def test_invalid_scene_retention_steps_and_counts_are_rejected(self) -> None:
        invalid_emds = (
            Emd(scenes=[]),
            Emd(scenes=[Scene()]),
            Emd(scenes=[Scene(shots=[Shot(start_ms=100, lines=["x."])])]),
            Emd(
                subjects=["one."],
                retention_rules=[RetentionRule(2, "fully_preserved", "x.")],
                scenes=[Scene(shots=[make_shot("<Subject 1> acts.")])],
            ),
        )
        for emd in invalid_emds:
            with self.subTest(emd=emd), self.assertRaises(errors.JSONGenerationError):
                jsongen.generate_json(emd)
        for steps in (0, 10001, True, 8.0):
            with self.subTest(steps=steps), self.assertRaises(errors.JSONGenerationError):
                jsongen.generate_json(
                    Emd(scenes=[Scene(shots=[make_shot("Action.")])]),
                    steps=steps,
                )

    def test_final_validator_rejects_wrong_prefix_and_section_order(self) -> None:
        text = jsongen.generate_json(
            Emd(scenes=[Scene(shots=[make_shot("Action.")])])
        )
        parsed = json.loads(text)
        parsed["prompt_prefix"] = "not empty"
        with self.assertRaises(errors.JSONValidationError):
            jsongen.validate_final_json(json.dumps(parsed, ensure_ascii=False) + "\n")

        parsed = json.loads(text)
        prompt = parsed["shots"][0]["prompt"]
        prompt[1], prompt[2] = prompt[2], prompt[1]
        with self.assertRaises(errors.JSONValidationError):
            jsongen.validate_final_json(json.dumps(parsed, ensure_ascii=False) + "\n")
