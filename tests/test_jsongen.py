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
BackgroundMusicReuse = structures.BackgroundMusicReuse
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
                            "<Subject 1> says <d>[Japanese]こんにちは</d>."
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

    def test_lip_sync_reuses_audio_signal_with_transcript_and_speaker(self) -> None:
        emd = Emd(
            subjects=["a character based on <Picture 1>."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]こんにちは</d>"
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertIn("directly reused spoken-audio signal", prompt[0])
        self.assertIn("<Subject 1> (S1)", prompt[0])
        self.assertIn("[reference generation + audio reuse]", prompt[1])
        self.assertIn("<Audio 1>: partially_copy", prompt[2])
        self.assertNotIn("(S1)", prompt[2])
        self.assertIn("lip-syncs exactly to <d>[Japanese]こんにちは</d>", prompt[3])
        self.assertIn("source audio signal and exact words are preserved", prompt[3])

    def test_lip_sync_requires_dialogue_permission(self) -> None:
        emd = Emd(
            subjects=["one."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]x</d>"
                        )
                    ]
                )
            ],
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "not enabled"):
            jsongen.generate_json(emd)

    def test_audio_cannot_be_voice_reference_and_reused_signal(self) -> None:
        emd = Emd(
            subjects=["a character whose voice is based on <Audio 1>."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]one</d>",
                            "<Subject 1> says <d>[Japanese]two</d>.",
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "both as"):
            jsongen.generate_json(emd)

    def test_reused_audio_cannot_be_assigned_to_multiple_subjects(self) -> None:
        emd = Emd(
            subjects=["one.", "two."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]one</d>",
                            "Lip sync: <Subject 2> <- <Audio 1>: "
                            "<d>[Japanese]two</d>",
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "multiple Subjects"):
            jsongen.generate_json(emd)

    def test_lip_sync_transcript_must_contain_spoken_text(self) -> None:
        emd = Emd(
            subjects=["one."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]</d>"
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "empty lip-sync"):
            jsongen.generate_json(emd)

    def test_lip_sync_role_overrides_unused_subject_voice_reference(self) -> None:
        emd = Emd(
            subjects=["a character whose voice is based on <Audio 1>."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "Lip sync: <Subject 1> <- <Audio 1>: "
                            "<d>[Japanese]one</d>"
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertIn("directly reused spoken-audio signal", prompt[0])
        self.assertNotIn("voice-timbre reference", prompt[0])
        self.assertIn("[reference generation + audio reuse]", prompt[1])
        self.assertIn("<Audio 1>: partially_copy", prompt[2])
        self.assertNotIn("<Audio 1>: reference", prompt[2])

    def test_speaker_id_is_generated_from_subject_number(self) -> None:
        emd = Emd(
            subjects=["a character whose voice is based on <Audio 1>."],
            scenes=[
                Scene(
                    shots=[
                        make_shot(
                            "<Subject 1> says <d>[Japanese]こんにちは</d>."
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertIn("<Subject 1> (S1) says", prompt[3])
        self.assertIn("<Subject 1> (S1).", prompt[0])

    def test_speaker_id_is_not_added_to_non_dialogue_subject_references(self) -> None:
        emd = Emd(
            subjects=["one."],
            scenes=[Scene(shots=[make_shot("<Subject 1> turns around.")])],
        )
        prompt = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"]
        self.assertIn("<Subject 1> turns around.", prompt[3])
        self.assertNotIn("(S1)", prompt[3])

    def test_speech_requires_permission_direct_speech_and_subject(self) -> None:
        invalid = (
            Emd(
                subjects=["one."],
                scenes=[
                    Scene(shots=[make_shot("<Subject 1> says <d>[Japanese]x</d>.")])
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
                        shots=[make_shot("Someone says <d>[Japanese]x</d>.")],
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
                            "<Subject 1> says <d>[Japanese]はい</d>.",
                        )
                    ],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                )
            ],
        )
        with self.assertRaises(errors.JSONGenerationError):
            jsongen.generate_json(emd)

    def test_speaker_ids_equal_subject_numbers_independent_of_vocal_order(self) -> None:
        valid = Emd(
            subjects=["one.", "two."],
            scenes=[
                Scene(
                    shots=[make_shot("<Subject 2> says <d>[Japanese]a</d>.")],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                ),
                Scene(
                    shots=[make_shot("<Subject 1> says <d>[Japanese]b</d>.")],
                    soundscape=Soundscape(vocalization=EXPLICIT_DIALOGUE_ONLY),
                ),
            ],
        )
        parsed = json.loads(jsongen.generate_json(valid))
        self.assertIn("<Subject 2> (S2) says", parsed["shots"][0]["prompt"][3])
        self.assertIn("<Subject 1> (S1) says", parsed["shots"][1]["prompt"][3])

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
                                "<Subject 1> says <d>[Japanese]a</d>.",
                                "<Subject 2> (S2) says <d>[Japanese]b</d>.",
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

    def test_common_prompt_is_filtered_without_activating_subjects(self) -> None:
        emd = Emd(
            subjects=["one.", "two."],
            common_prompt=[
                "A clean global visual style.",
                "Do not merge <Subject 1> and <Subject 2>.",
                "Keep <Subject 1> sharply rendered.",
            ],
            scenes=[
                Scene(shots=[make_shot("<Subject 1> acts alone.")]),
                Scene(shots=[make_shot("<Subject 1> and <Subject 2> interact.")]),
                Scene(shots=[make_shot("A blue light crosses the frame.")]),
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        first = parsed["shots"][0]["prompt"][3]
        second = parsed["shots"][1]["prompt"][3]
        third = parsed["shots"][2]["prompt"][3]
        self.assertIn("A clean global visual style.", first)
        self.assertIn("Keep <Subject 1> sharply rendered.", first)
        self.assertNotIn("Do not merge <Subject 1> and <Subject 2>.", first)
        self.assertIn("Do not merge <Subject 1> and <Subject 2>.", second)
        self.assertEqual(parsed["shots"][2]["prompt"][0], jsongen.NO_ACTIVE_SUBJECT_BLOCK)
        self.assertIn("A clean global visual style.", third)
        self.assertNotIn("<Subject 1>", third)

    def test_common_prompt_precedes_scene_preamble_and_first_shot(self) -> None:
        emd = Emd(
            common_prompt=["Global style."],
            scenes=[
                Scene(
                    preamble=["Scene-specific setting."],
                    shots=[make_shot("The action occurs.")],
                )
            ],
        )
        detailed = json.loads(jsongen.generate_json(emd))["shots"][0]["prompt"][3]
        self.assertLess(detailed.index("Global style."), detailed.index("Scene-specific setting."))
        self.assertLess(detailed.index("Scene-specific setting."), detailed.index("[Shot 1]"))

    def test_common_audio_is_filtered_without_activating_audio(self) -> None:
        emd = Emd(
            common_prompt=[
                "Keep <Audio 1> continuous.",
                "Keep <Audio 2> continuous.",
            ],
            scenes=[
                Scene(shots=[make_shot("A landscape remains visible.")]),
                Scene(
                    shots=[make_shot("The next landscape remains visible.")],
                    soundscape=Soundscape(
                        background_music_reuse=BackgroundMusicReuse(
                            1, "partially_copy"
                        )
                    ),
                ),
                Scene(
                    shots=[make_shot("The final landscape remains visible.")],
                    soundscape=Soundscape(
                        background_music_reuse=BackgroundMusicReuse(
                            2, "partially_copy"
                        )
                    ),
                ),
            ],
        )
        parsed = json.loads(jsongen.generate_json(emd))
        first = parsed["shots"][0]["prompt"][3]
        second = parsed["shots"][1]["prompt"][3]
        third = parsed["shots"][2]["prompt"][3]
        self.assertNotIn("<Audio 1>", first)
        self.assertNotIn("<Audio 2>", first)
        self.assertIn("Keep <Audio 1> continuous.", second)
        self.assertNotIn("<Audio 2>", second)
        self.assertNotIn("<Audio 1>", third)
        self.assertIn("Keep <Audio 2> continuous.", third)

    def test_invalid_common_prompt_content_is_rejected(self) -> None:
        for line in (
            "Use <Audio 4>.",
            "Use <Audio 01>.",
            "Someone says <d>[Japanese]x</d>.",
            "Use (S1).",
            "A character speaks loudly.",
            "Keep <Subject 2> visible.",
        ):
            with self.subTest(line=line), self.assertRaises(errors.JSONGenerationError):
                jsongen.generate_json(
                    Emd(
                        subjects=["one."],
                        common_prompt=[line],
                        scenes=[Scene(shots=[make_shot("<Subject 1> acts.")])],
                    )
                )

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
            "No other ambience, physical sound, or character vocalization is present.",
        )

    def test_background_music_is_generated_in_sixth_section(self) -> None:
        scene = Scene(
            shots=[make_shot("A landscape remains visible.")],
            soundscape=Soundscape(
                background_music=(
                    "Sparse piano notes at a slow tempo with sustained low strings."
                )
            ),
        )
        prompt = json.loads(jsongen.generate_json(Emd(scenes=[scene])))["shots"][0]["prompt"]
        self.assertEqual(prompt[4], jsongen.NO_DIEGETIC_SOUND)
        self.assertEqual(
            prompt[5],
            "non_diegetic_music:\n"
            "Sparse piano notes at a slow tempo with sustained low strings.",
        )
        jsongen.validate_final_json(jsongen.generate_json(Emd(scenes=[scene])))

    def test_background_music_none_keeps_na(self) -> None:
        scene = Scene(
            shots=[make_shot("Action.")],
            soundscape=Soundscape(background_music="NONE"),
        )
        prompt = json.loads(jsongen.generate_json(Emd(scenes=[scene])))["shots"][0]["prompt"]
        self.assertEqual(prompt[5], jsongen.NON_DIEGETIC_MUSIC)

    def test_reused_background_music_vocal_drives_exact_lip_sync(self) -> None:
        scene = Scene(
            duration=8,
            shots=[
                make_shot(
                    "<Subject 1> faces the camera.",
                    "Lip sync: <Subject 1> <- <Audio 1>: "
                    "<d>[Japanese]夜空を越えて、君のもとへ。</d>",
                )
            ],
            soundscape=Soundscape(
                vocalization=EXPLICIT_DIALOGUE_ONLY,
                background_music_reuse=BackgroundMusicReuse(1, "fully_copy"),
            ),
        )
        text = jsongen.generate_json(Emd(subjects=["a singer."], scenes=[scene]))
        prompt = jsongen.validate_final_json(text)["shots"][0]["prompt"]
        self.assertIn("audience-only background-music signal", prompt[0])
        self.assertIn("<Subject 1> (S1)", prompt[0])
        self.assertIn("[reference generation + audio reuse]", prompt[1])
        self.assertIn("<Audio 1>: fully_copy", prompt[2])
        self.assertIn("complete final audio track", prompt[2])
        self.assertIn("visually performs and lip-syncs exactly", prompt[3])
        self.assertIn("<d>[Japanese]夜空を越えて、君のもとへ。</d>", prompt[3])
        self.assertIn("no replacement", prompt[3])
        self.assertIn("no new voice is generated", prompt[4])
        self.assertIn("No other separately generated ambience", prompt[4])
        self.assertNotIn(
            "No other ambience, physical sound, or character vocalization is present.",
            prompt[4],
        )
        self.assertIn("<Audio 1> is directly reused 1:1", prompt[5])
        self.assertIn("original vocal layer", prompt[5])

    def test_partially_copied_background_music_can_mix_other_sound_layers(self) -> None:
        scene = Scene(
            shots=[make_shot("A city skyline remains visible.")],
            soundscape=Soundscape(
                environment="Distant traffic.",
                sound_effects="A soft transition whoosh.",
                background_music_reuse=BackgroundMusicReuse(
                    2, "partially_copy"
                ),
            ),
        )
        prompt = json.loads(
            jsongen.generate_json(Emd(scenes=[scene]))
        )["shots"][0]["prompt"]
        self.assertIn("No character subject", prompt[0])
        self.assertIn("<Audio 2> is the directly reused", prompt[0])
        self.assertIn("<Audio 2>: partially_copy", prompt[2])
        self.assertIn("Environment: Distant traffic.", prompt[4])
        self.assertIn("Sound effects: A soft transition whoosh.", prompt[4])
        self.assertIn("background-music signal from <Audio 2>", prompt[5])

    def test_ranged_background_music_segment_is_preserved_across_sections(self) -> None:
        scene = Scene(
            duration=10,
            shots=[make_shot("<Subject 1> performs to the music.")],
            soundscape=Soundscape(
                background_music_reuse=BackgroundMusicReuse(
                    1,
                    "partially_copy",
                    source_start_ms=20_000,
                    source_end_ms=30_000,
                )
            ),
        )
        prompt = json.loads(
            jsongen.generate_json(Emd(subjects=["a singer."], scenes=[scene]))
        )["shots"][0]["prompt"]
        source_range = "source interval from 00:20.000 to 00:30.000"
        self.assertIn(source_range, prompt[0])
        self.assertIn(source_range, prompt[1])
        self.assertIn(source_range, prompt[2])
        self.assertIn(source_range, prompt[3])
        self.assertIn(source_range, prompt[5])
        self.assertIn("without recomposition", prompt[2])
        self.assertIn("without recomposition", prompt[3])
        self.assertIn("without recomposition", prompt[5])

    def test_fully_copied_background_music_rejects_added_audio_layers(self) -> None:
        invalid_scenes = (
            Scene(
                shots=[make_shot("Action.")],
                soundscape=Soundscape(
                    environment="Wind.",
                    background_music_reuse=BackgroundMusicReuse(1, "fully_copy"),
                ),
            ),
            Scene(
                shots=[make_shot("<Subject 1> says <d>[Japanese]台詞。</d>.")],
                soundscape=Soundscape(
                    vocalization=EXPLICIT_DIALOGUE_ONLY,
                    background_music_reuse=BackgroundMusicReuse(1, "fully_copy"),
                ),
            ),
            Scene(
                shots=[
                    make_shot(
                        "Lip sync: <Subject 1> <- <Audio 2>: "
                        "<d>[Japanese]台詞。</d>"
                    )
                ],
                soundscape=Soundscape(
                    vocalization=EXPLICIT_DIALOGUE_ONLY,
                    background_music_reuse=BackgroundMusicReuse(1, "fully_copy"),
                ),
            ),
        )
        for scene in invalid_scenes:
            with self.subTest(scene=scene), self.assertRaisesRegex(
                errors.JSONGenerationError, "fully_copy"
            ):
                jsongen.generate_json(Emd(subjects=["one."], scenes=[scene]))

    def test_generated_and_reused_background_music_are_mutually_exclusive(self) -> None:
        scene = Scene(
            shots=[make_shot("Action.")],
            soundscape=Soundscape(
                background_music="Piano.",
                background_music_reuse=BackgroundMusicReuse(1, "partially_copy"),
            ),
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "cannot combine"):
            jsongen.generate_json(Emd(scenes=[scene]))

    def test_invalid_background_music_reuse_structure_is_rejected(self) -> None:
        invalid_values = (
            "not structured",
            BackgroundMusicReuse(0, "fully_copy"),
            BackgroundMusicReuse(4, "partially_copy"),
            BackgroundMusicReuse(True, "fully_copy"),
            BackgroundMusicReuse(1, "reference"),
            BackgroundMusicReuse(1, ["fully_copy"]),
            BackgroundMusicReuse(1, "fully_copy", 0, 5_000),
            BackgroundMusicReuse(1, "partially_copy", 0, None),
            BackgroundMusicReuse(1, "partially_copy", -1, 4_999),
            BackgroundMusicReuse(1, "partially_copy", 5_000, 5_000),
            BackgroundMusicReuse(1, "partially_copy", 0, 4_000),
        )
        for value in invalid_values:
            scene = Scene(
                shots=[make_shot("Action.")],
                soundscape=Soundscape(background_music_reuse=value),
            )
            with self.subTest(value=value), self.assertRaises(
                errors.JSONGenerationError
            ):
                jsongen.generate_json(Emd(scenes=[scene]))

    def test_background_music_reuse_cannot_share_a_voice_reference_role(self) -> None:
        scene = Scene(
            shots=[make_shot("<Subject 1> says <d>[Japanese]台詞。</d>.")],
            soundscape=Soundscape(
                vocalization=EXPLICIT_DIALOGUE_ONLY,
                background_music_reuse=BackgroundMusicReuse(1, "partially_copy"),
            ),
        )
        with self.assertRaisesRegex(errors.JSONGenerationError, "both as"):
            jsongen.generate_json(
                Emd(
                    subjects=["a character whose voice is based on <Audio 1>."],
                    scenes=[scene],
                )
            )

    def test_soundscape_text_cannot_bypass_vocalization_policy(self) -> None:
        for soundscape in (
            Soundscape(environment="Reference <Audio 1>."),
            Soundscape(sound_effects="Play <d>[Japanese]x</d>."),
            Soundscape(background_music="Reuse <Audio 1>."),
            Soundscape(background_music="A song with <d>[Japanese]lyrics</d>."),
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

        for music in (
            "non_diegetic_music:\nReuse <Audio 1>.",
            "non_diegetic_music:\nSing <d>[Japanese]lyrics</d>.",
        ):
            with self.subTest(music=music):
                parsed = json.loads(text)
                parsed["shots"][0]["prompt"][5] = music
                with self.assertRaises(errors.JSONValidationError):
                    jsongen.validate_final_json(
                        json.dumps(parsed, ensure_ascii=False) + "\n"
                    )

        parsed = json.loads(text)
        parsed["shots"][0]["prompt"][5] = "non_diegetic_music:\nSparse piano."
        with self.assertRaisesRegex(errors.JSONValidationError, "complete silence"):
            jsongen.validate_final_json(json.dumps(parsed, ensure_ascii=False) + "\n")
