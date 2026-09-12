from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import PKG, ROOT, module


vocal = module("node_vocal_to_prompt_segments.node")
errors = module("node_vocal_to_prompt_segments.errors")


def lyric(index: int, text: str) -> object:
    return vocal.LyricLine(index, index, text, vocal.normalize_match_text(text))


def word(index: int, text: str, start: float, end: float) -> object:
    return vocal.WhisperWord(
        text,
        vocal.normalize_match_text(text),
        start,
        end,
        index,
    )


class FakeAudioShape:
    shape = (1, 1, 4000)


class FakeBackend:
    def __init__(self, result: dict | list[dict]) -> None:
        self.result = result
        self.ensure_calls: list[tuple[Path, str]] = []
        self.transcribe_calls: list[dict] = []
        self.clear_count = 0

    def ensure_loaded(self, model_path: Path, device: str):
        self.ensure_calls.append((model_path, device))
        return self

    def transcribe(
        self,
        audio,
        *,
        language,
        device,
        initial_prompt,
        condition_on_previous_text,
    ):
        self.transcribe_calls.append(
            {
                "audio": audio,
                "language": language,
                "device": device,
                "initial_prompt": initial_prompt,
                "condition_on_previous_text": condition_on_previous_text,
            }
        )
        if isinstance(self.result, list):
            return self.result[len(self.transcribe_calls) - 1]
        return self.result

    def clear_model(self) -> None:
        self.clear_count += 1


class VocalPromptNodeTests(unittest.TestCase):
    def test_registration_metadata_and_input_contract(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLVocalToPromptSegments"]
        self.assertIs(cls, vocal.CLVocalToPromptSegments)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLVocalToPromptSegments"],
            "CL Vocal to Prompt Segments",
        )
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Prompt Tools")
        self.assertEqual(cls.FUNCTION, "build_prompt_segments")
        self.assertEqual(
            cls.RETURN_NAMES,
            ("prompt_text", "srt_text", "segments_json", "status"),
        )
        with patch.object(
            cls, "discover_model_names", return_value=["large-v3.pt"]
        ):
            input_types = cls.INPUT_TYPES()
            required = input_types["required"]
        self.assertEqual(
            list(required),
            [
                "vocal_audio",
                "lyrics_text",
                "whisper_model",
                "language",
                "device",
                "keep_whisper_loaded",
                "max_scene_seconds",
                "silence_threshold_dbfs",
                "analysis_window_ms",
                "min_voiced_ms",
                "min_silence_ms",
                "voice_padding_ms",
                "lyrics_match_threshold",
                "lyrics_neighbor_threshold",
                "lyrics_search_seconds",
            ],
        )
        self.assertTrue(required["lyrics_text"][1]["forceInput"])
        self.assertFalse(required["keep_whisper_loaded"][1]["default"])
        self.assertEqual(required["max_scene_seconds"][1]["default"], 10)
        self.assertEqual(required["language"][0], ["ja", "en", "auto"])
        self.assertTrue(
            input_types["optional"]["condition_on_previous_text"][1]["default"]
        )
        self.assertEqual(
            input_types["optional"]["srt_time_offset"][1]["default"], 0
        )
        self.assertEqual(
            input_types["optional"]["srt_time_offset"][1]["step"], 1
        )
        self.assertTrue(
            input_types["optional"]["include_lyrics_comments"][1]["default"]
        )
        self.assertEqual(
            required["lyrics_neighbor_threshold"][1]["default"], 0.45
        )

    def test_suno_headings_are_ignored_and_lyrics_are_preserved_exactly(self) -> None:
        lines = vocal.parse_suno_lyrics(
            "[Verse 1]\r\n 赤い林檎を　ひとつ頬張り \r\n\r\n"
            "[Chorus]\r\n風よ　まだ答えを告げるな\r\n"
        )
        self.assertEqual(
            [line.text for line in lines],
            ["赤い林檎を　ひとつ頬張り", "風よ　まだ答えを告げるな"],
        )
        self.assertEqual([line.source_line for line in lines], [2, 5])

    def test_suno_sections_are_classified_and_emitted_for_planner(self) -> None:
        lines = vocal.parse_suno_lyrics(
            "[Verse 1]\nfirst line\n[Pre-Chorus 2]\nsecond line\n"
            "[Odd Movement]\nthird line\n"
        )
        self.assertEqual(
            [(line.section_label, line.section_kind) for line in lines],
            [
                ("[Verse 1]", "verse"),
                ("[Pre-Chorus 2]", "pre_chorus"),
                ("[Odd Movement]", "custom"),
            ],
        )
        scenes = [
            {
                "index": 1,
                "state": "voiced",
                "start_seconds": 0,
                "end_seconds": 5,
                "duration_seconds": 5,
                "lyrics_indices": [1, 2, 3],
            }
        ]
        alignments = [
            vocal.LyricAlignment(
                line, "resolved", 1.0, line.text, 0, 1000, 1
            )
            for line in lines
        ]
        prompt = vocal.build_prompt_text(scenes, alignments)
        self.assertIn("// 楽曲セクション: [Verse 1]", prompt)
        self.assertIn("// 楽曲セクション: [Pre-Chorus 2]", prompt)
        self.assertIn("// 楽曲セクション: [Odd Movement]", prompt)

    def test_normalization_handles_width_case_kana_spaces_and_punctuation(self) -> None:
        self.assertEqual(
            vocal.normalize_match_text(" ＡＢＣ・カタカナ！ "),
            "abcかたかな",
        )

    def test_whisper_initial_prompt_is_bounded_to_leading_lyrics(self) -> None:
        lines = [lyric(index, f"歌詞{index:02d}" * 8) for index in range(1, 15)]
        prompt = vocal.build_whisper_initial_prompt(lines)
        self.assertLessEqual(len(prompt), 160)
        self.assertLessEqual(len(prompt.splitlines()), 12)
        self.assertTrue(prompt.startswith(lines[0].text))
        self.assertNotIn(lines[-1].text, prompt)

    def test_vad_fills_short_gaps_and_removes_short_voice_runs(self) -> None:
        intervals = vocal.detected_intervals_from_dbfs(
            [-80, -10, -10, -80, -10, -10, -80, -80, -10, -80, -80, -80],
            total_samples=1200,
            sample_rate=1000,
            window_samples=100,
            silence_threshold_dbfs=-45,
            min_voiced_ms=150,
            min_silence_ms=150,
            voice_padding_ms=0,
        )
        self.assertEqual(
            intervals,
            [
                vocal.DetectedInterval("silent", 0, 100),
                vocal.DetectedInterval("voiced", 100, 600),
                vocal.DetectedInterval("silent", 600, 1200),
            ],
        )

    def test_vad_padding_merges_touching_voice_intervals(self) -> None:
        intervals = vocal.detected_intervals_from_dbfs(
            [-80, -10, -10, -80, -10, -10, -80],
            total_samples=700,
            sample_rate=1000,
            window_samples=100,
            silence_threshold_dbfs=-45,
            min_voiced_ms=0,
            min_silence_ms=0,
            voice_padding_ms=50,
        )
        self.assertEqual(
            intervals,
            [
                vocal.DetectedInterval("silent", 0, 50),
                vocal.DetectedInterval("voiced", 50, 650),
                vocal.DetectedInterval("silent", 650, 700),
            ],
        )

    def test_whisper_words_are_validated_sorted_and_vad_filtered(self) -> None:
        intervals = [
            vocal.DetectedInterval("silent", 0, 1000),
            vocal.DetectedInterval("voiced", 1000, 3000),
            vocal.DetectedInterval("silent", 3000, 4000),
        ]
        result = {
            "segments": [
                {
                    "text": "前 歌 後",
                    "words": [
                        {"word": "歌", "start": 1.2, "end": 1.6},
                        {"word": "前", "start": 0.2, "end": 0.4},
                        {"word": "後", "start": 3.2, "end": 3.4},
                        {"word": "bad", "start": -1, "end": 0},
                    ],
                }
            ]
        }
        words, invalid, outside = vocal.extract_whisper_words(
            result,
            audio_duration_seconds=4.0,
            intervals=intervals,
            sample_rate=1000,
        )
        self.assertEqual([entry.text for entry in words], ["歌"])
        self.assertEqual(invalid, 1)
        self.assertEqual(outside, 2)

        with self.assertRaisesRegex(errors.VocalPromptError, "word timestamps"):
            vocal.extract_whisper_words(
                {"segments": [{"text": "認識済みだがwordsなし"}]},
                audio_duration_seconds=4.0,
                intervals=intervals,
                sample_rate=1000,
            )

    def test_lyrics_alignment_is_monotonic_and_unresolved_line_keeps_cursor(self) -> None:
        lyrics = [
            lyric(1, "こんにちは"),
            lyric(2, "存在しない"),
            lyric(3, "世界"),
        ]
        words = [
            word(1, "こんにちは", 1.0, 1.8),
            word(2, "世界", 2.0, 2.5),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.8,
            search_seconds=10.0,
            audio_duration_seconds=4.0,
        )
        self.assertEqual(
            [entry.status for entry in aligned],
            ["resolved", "unresolved", "resolved"],
        )
        self.assertEqual((aligned[0].start_ms, aligned[0].end_ms), (1000, 1800))
        self.assertEqual((aligned[2].start_ms, aligned[2].end_ms), (2000, 2500))
        self.assertEqual(aligned[0].match_method, "primary")

    def test_neighbor_bounded_alignment_recovers_a_near_match(self) -> None:
        lyrics = [lyric(1, "開始"), lyric(2, "abcdefghij"), lyric(3, "終了")]
        words = [
            word(1, "開始", 1.0, 1.4),
            word(2, "abcdeXXXXX", 1.5, 2.0),
            word(3, "終了", 2.1, 2.5),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=10.0,
            audio_duration_seconds=4.0,
        )
        self.assertEqual([item.status for item in aligned], ["resolved"] * 3)
        self.assertEqual(aligned[1].match_method, "neighbor")
        self.assertEqual(aligned[1].whisper_text, "abcdeXXXXX")

    def test_alignment_skips_early_candidate_below_threshold(self) -> None:
        aligned = vocal.align_lyrics(
            [lyric(1, "target lyric")],
            [
                word(1, "unrelated", 1.0, 1.5),
                word(2, "target", 20.0, 20.3),
                word(3, "lyric", 20.3, 20.7),
            ],
            match_threshold=0.8,
            search_seconds=60.0,
            audio_duration_seconds=30.0,
        )
        self.assertEqual(aligned[0].whisper_text, "targetlyric")
        self.assertEqual(aligned[0].start_ms, 20000)

    def test_post_anchor_search_does_not_jump_to_later_repeated_chorus(self) -> None:
        lyrics = [
            lyric(1, "We walk the path we never chose"),
            lyric(2, "BIT BY BIT!"),
            lyric(3, "We become the wounds nobody knows"),
        ]
        words = [
            word(1, "We walk the path we never chose", 1.0, 2.0),
            word(2, "By", 2.1, 2.2),
            word(3, "breath", 2.2, 2.5),
            word(4, "We become the wounds nobody knows", 2.6, 4.0),
            word(5, "BIT", 55.0, 55.2),
            word(6, "BY", 55.2, 55.4),
            word(7, "BIT", 55.4, 55.6),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=60.0,
            audio_duration_seconds=60.0,
        )
        self.assertEqual(
            [entry.status for entry in aligned],
            ["resolved", "unresolved", "resolved"],
        )
        self.assertEqual(aligned[1].candidate_whisper_text, "Bybreath")
        self.assertEqual(aligned[2].start_ms, 2600)

    def test_repeated_refrain_uses_following_lyrics_to_select_earlier_match(self) -> None:
        lyrics = [
            lyric(1, "opening anchor"),
            lyric(2, "BIT BY BIT!"),
            lyric(3, "we carve our history in tears"),
            lyric(4, "through every scar"),
            lyric(5, "BIT BY BIT!"),
            lyric(6, "until the end is drawing near"),
        ]
        words = [
            word(1, "opening anchor", 1.0, 2.0),
            word(2, "BIT BY BIT", 2.1, 2.5),
            word(3, "we carve our history in tears", 2.6, 4.0),
            word(4, "through every scar", 4.1, 5.0),
            word(5, "BIT BY BIT", 10.0, 10.4),
            word(6, "until the end is drawing near", 10.5, 12.0),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=60.0,
            audio_duration_seconds=20.0,
        )
        self.assertEqual([entry.status for entry in aligned], ["resolved"] * 6)
        self.assertEqual(aligned[1].start_ms, 2100)
        self.assertEqual(aligned[4].start_ms, 10000)
        self.assertEqual(aligned[5].start_ms, 10500)

    def test_weaker_candidate_is_left_for_the_following_lyric(self) -> None:
        lyrics = [
            lyric(1, "opening anchor"),
            lyric(2, "through the ashes"),
            lyric(3, "through the years"),
            lyric(4, "closing anchor"),
        ]
        words = [
            word(1, "opening anchor", 1.0, 2.0),
            word(2, "through the years", 2.1, 3.0),
            word(3, "closing anchor", 3.1, 4.0),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=10.0,
            audio_duration_seconds=5.0,
        )
        self.assertEqual(
            [entry.status for entry in aligned],
            ["resolved", "unresolved", "resolved", "resolved"],
        )
        self.assertEqual(aligned[2].whisper_text, "through the years")
        self.assertEqual(aligned[2].start_ms, 2100)

    def test_targeted_retry_recovers_only_a_bounded_unresolved_run(self) -> None:
        lyrics = [
            lyric(1, "opening anchor"),
            lyric(2, "missing harsh line"),
            lyric(3, "second missing line"),
            lyric(4, "closing anchor"),
        ]
        alignments = [
            vocal.LyricAlignment(
                lyrics[0], "resolved", 1.0, "opening anchor", 0, 1000,
                match_method="primary",
            ),
            vocal.LyricAlignment(lyrics[1], "unresolved", 0.0, "", None, None),
            vocal.LyricAlignment(lyrics[2], "unresolved", 0.0, "", None, None),
            vocal.LyricAlignment(
                lyrics[3], "resolved", 1.0, "closing anchor", 4000, 5000,
                match_method="primary",
            ),
        ]
        retry_result = {
            "segments": [
                {
                    "text": "missing harsh line second missing line",
                    "words": [
                        {
                            "word": "missing harsh line",
                            "start": 1.2,
                            "end": 2.0,
                        },
                        {
                            "word": "second missing line",
                            "start": 2.1,
                            "end": 3.0,
                        },
                    ],
                }
            ]
        }
        backend = FakeBackend(retry_result)
        updated, attempts, recovered, invalid = (
            vocal.targeted_retry_unresolved_lyrics(
                alignments,
                list(range(5 * 16000)),
                backend,
                language="en",
                device="cpu",
                condition_on_previous_text=True,
                match_threshold=0.55,
                neighbor_match_threshold=0.45,
            )
        )
        self.assertEqual((attempts, recovered, invalid), (1, 2, 0))
        self.assertEqual(
            [entry.status for entry in updated],
            ["resolved", "resolved", "resolved", "resolved"],
        )
        self.assertEqual(updated[1].match_method, "targeted")
        self.assertEqual((updated[1].start_ms, updated[1].end_ms), (1200, 2000))
        self.assertEqual((updated[2].start_ms, updated[2].end_ms), (2100, 3000))
        self.assertEqual(
            backend.transcribe_calls[0]["initial_prompt"],
            "opening anchor",
        )
        scenes = [
            {
                "index": 1,
                "state": "voiced",
                "start_seconds": 0,
                "end_seconds": 1,
                "duration_seconds": 1,
                "lyrics_indices": [],
            },
            {
                "index": 2,
                "state": "silent",
                "start_seconds": 1,
                "end_seconds": 4,
                "duration_seconds": 3,
                "lyrics_indices": [],
            },
            {
                "index": 3,
                "state": "voiced",
                "start_seconds": 4,
                "end_seconds": 5,
                "duration_seconds": 1,
                "lyrics_indices": [],
            },
        ]
        self.assertEqual(vocal.promote_resolved_lyrics_scenes(scenes, updated), 1)
        self.assertEqual([scene["state"] for scene in scenes], ["voiced"] * 3)

    def test_resolved_primary_lyric_promotes_vad_boundary_scene(self) -> None:
        scenes = [
            {
                "index": 1,
                "state": "silent",
                "start_seconds": 50,
                "end_seconds": 60,
                "duration_seconds": 10,
                "lyrics_indices": [],
            },
            {
                "index": 2,
                "state": "voiced",
                "start_seconds": 60,
                "end_seconds": 70,
                "duration_seconds": 10,
                "lyrics_indices": [],
            },
        ]
        alignments = [
            vocal.LyricAlignment(
                lyric(1, "What is the meaning of the life we bear?"),
                "resolved",
                1.0,
                "What is the meaning of the life we bear?",
                59_940,
                63_140,
                match_method="primary",
            )
        ]

        self.assertEqual(
            vocal.promote_resolved_lyrics_scenes(scenes, alignments), 1
        )
        self.assertEqual([scene["state"] for scene in scenes], ["voiced", "voiced"])
        assigned = vocal.assign_lyrics_to_scenes(alignments, scenes)
        self.assertEqual(assigned[0].scene_index, 1)
        prompt = vocal.build_prompt_text(scenes, assigned)
        self.assertIn("// 歌詞: What is the meaning of the life we bear?", prompt)

    def test_targeted_retry_splits_a_long_gap_into_overlapping_windows(self) -> None:
        lyrics = [
            lyric(1, "opening anchor"),
            lyric(2, "look into the void"),
            lyric(3, "tell me what you see"),
            lyric(4, "no hand below"),
            lyric(5, "closing anchor"),
        ]
        alignments = [
            vocal.LyricAlignment(
                lyrics[0], "resolved", 1.0, "opening anchor", 0, 1000,
                match_method="primary",
            ),
            *[
                vocal.LyricAlignment(line, "unresolved", 0.0, "", None, None)
                for line in lyrics[1:4]
            ],
            vocal.LyricAlignment(
                lyrics[4], "resolved", 1.0, "closing anchor", 31000, 32000,
                match_method="primary",
            ),
        ]
        backend = FakeBackend(
            [
                {
                    "segments": [{
                        "text": "look into the void",
                        "words": [{
                            "word": "look into the void", "start": 2.0, "end": 3.0,
                        }],
                    }],
                },
                {
                    "segments": [{
                        "text": "tell me what you see",
                        "words": [{
                            "word": "tell me what you see", "start": 3.0, "end": 4.0,
                        }],
                    }],
                },
                {
                    "segments": [{
                        "text": "no hand below",
                        "words": [{
                            "word": "no hand below", "start": 5.0, "end": 6.0,
                        }],
                    }],
                },
            ]
        )
        updated, attempts, recovered, invalid = (
            vocal.targeted_retry_unresolved_lyrics(
                alignments,
                list(range(32 * 16000)),
                backend,
                language="en",
                device="cpu",
                condition_on_previous_text=True,
                match_threshold=0.55,
                neighbor_match_threshold=0.45,
            )
        )
        self.assertEqual((attempts, recovered, invalid), (1, 3, 0))
        self.assertEqual(len(backend.transcribe_calls), 3)
        self.assertEqual(
            [len(call["audio"]) for call in backend.transcribe_calls],
            [12 * 16000, 12 * 16000, 12 * 16000],
        )
        self.assertEqual(
            [(entry.start_ms, entry.end_ms) for entry in updated[1:4]],
            [(2000, 3000), (13000, 14000), (25000, 26000)],
        )
        self.assertEqual(
            [entry.match_method for entry in updated[1:4]],
            ["targeted"] * 3,
        )
        self.assertEqual(
            [call["initial_prompt"] for call in backend.transcribe_calls],
            [
                "opening anchor",
                "opening anchor",
                "opening anchor\nlook into the void",
            ],
        )

    def test_targeted_retry_can_move_an_early_following_anchor_later(self) -> None:
        lyrics = [
            lyric(1, "opening lyric"),
            lyric(2, "missing sung lyric"),
            lyric(3, "closing lyric"),
        ]
        alignments = [
            vocal.LyricAlignment(
                lyrics[0], "resolved", 1.0, "opening lyric", 0, 2000,
                match_method="primary",
            ),
            vocal.LyricAlignment(lyrics[1], "unresolved", 0.0, "", None, None),
            vocal.LyricAlignment(
                lyrics[2], "resolved", 1.0, "closing lyric", 4000, 6000,
                match_method="primary",
            ),
        ]
        backend = FakeBackend(
            [
                {
                    "segments": [{
                        "text": "indistinct growl closing lyric",
                        "words": [
                            {
                                "word": "indistinct growl",
                                "start": 2.1,
                                "end": 4.8,
                            },
                            {
                                "word": "closing lyric",
                                "start": 4.8,
                                "end": 5.8,
                            },
                        ],
                    }],
                },
                {
                    "segments": [{
                        "text": "missing sung lyric closing lyric",
                        "words": [
                            {
                                "word": "missing sung lyric",
                                "start": 2.1,
                                "end": 4.8,
                            },
                            {
                                "word": "closing lyric",
                                "start": 4.8,
                                "end": 5.8,
                            },
                        ],
                    }],
                },
            ]
        )

        updated, attempts, recovered, invalid = (
            vocal.targeted_retry_unresolved_lyrics(
                alignments,
                list(range(6 * 16000)),
                backend,
                language="en",
                device="cpu",
                condition_on_previous_text=True,
                match_threshold=0.55,
                neighbor_match_threshold=0.45,
            )
        )

        self.assertEqual((attempts, recovered, invalid), (1, 1, 0))
        self.assertEqual(len(backend.transcribe_calls), 2)
        self.assertEqual(updated[1].status, "resolved")
        self.assertEqual(updated[1].match_method, "targeted")
        self.assertEqual((updated[1].start_ms, updated[1].end_ms), (2100, 4800))
        self.assertEqual((updated[2].start_ms, updated[2].end_ms), (4800, 6000))
        self.assertEqual(updated[2].match_method, "primary")
        self.assertEqual(
            backend.transcribe_calls[0]["initial_prompt"],
            "opening lyric",
        )
        self.assertEqual(
            backend.transcribe_calls[1]["initial_prompt"],
            "opening lyric\nmissing sung lyric",
        )

    def test_guided_targeted_retry_cannot_replace_following_anchor_without_support(
        self,
    ) -> None:
        lyrics = [
            lyric(1, "opening lyric"),
            lyric(2, "missing sung lyric"),
            lyric(3, "closing lyric"),
        ]
        alignments = [
            vocal.LyricAlignment(
                lyrics[0], "resolved", 1.0, "opening lyric", 0, 2000,
                match_method="primary",
            ),
            vocal.LyricAlignment(lyrics[1], "unresolved", 0.0, "", None, None),
            vocal.LyricAlignment(
                lyrics[2], "resolved", 1.0, "closing lyric", 4000, 6000,
                match_method="primary",
            ),
        ]
        backend = FakeBackend(
            [
                {
                    "segments": [{
                        "text": "indistinct growl",
                        "words": [{
                            "word": "indistinct growl", "start": 2.1, "end": 4.8,
                        }],
                    }],
                },
                {
                    "segments": [{
                        "text": "missing sung lyric",
                        "words": [{
                            "word": "missing sung lyric", "start": 2.1, "end": 4.8,
                        }],
                    }],
                },
            ]
        )

        updated, attempts, recovered, invalid = (
            vocal.targeted_retry_unresolved_lyrics(
                alignments,
                list(range(6 * 16000)),
                backend,
                language="en",
                device="cpu",
                condition_on_previous_text=True,
                match_threshold=0.55,
                neighbor_match_threshold=0.45,
            )
        )

        self.assertEqual((attempts, recovered, invalid), (1, 0, 0))
        self.assertEqual(updated, alignments)

    def test_targeted_window_merge_deduplicates_overlap_by_timestamp(self) -> None:
        merged = vocal._merge_targeted_window_words(
            [
                word(1, "same", 10.9, 11.4),
                word(2, "same", 11.0, 11.2),
                word(3, "same", 12.0, 12.2),
            ]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual((merged[0].start, merged[0].end), (11.0, 11.2))
        self.assertEqual([entry.source_order for entry in merged], [0, 1])

    def test_unique_supported_line_can_resynchronize_after_long_gap(self) -> None:
        lyrics = [
            lyric(1, "opening anchor"),
            lyric(2, "missing vocal line"),
            lyric(3, "unique return point"),
            lyric(4, "following lyric confirms order"),
        ]
        words = [
            word(1, "opening anchor", 1.0, 2.0),
            word(2, "unrelated growl", 2.1, 2.8),
            word(3, "unique return point", 30.0, 31.0),
            word(4, "following lyric confirms order", 31.1, 32.5),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=60.0,
            audio_duration_seconds=40.0,
        )
        self.assertEqual(
            [entry.status for entry in aligned],
            ["resolved", "unresolved", "resolved", "resolved"],
        )
        self.assertEqual(aligned[2].match_method, "resync")
        self.assertEqual(aligned[2].start_ms, 30000)

    def test_unbounded_trailing_near_match_remains_unresolved_with_diagnostics(self) -> None:
        lyrics = [lyric(1, "開始"), lyric(2, "abcdefghij")]
        words = [
            word(1, "開始", 1.0, 1.4),
            word(2, "abcdeXXXXX", 1.5, 2.0),
        ]
        aligned = vocal.align_lyrics(
            lyrics,
            words,
            match_threshold=0.55,
            neighbor_match_threshold=0.45,
            search_seconds=10.0,
            audio_duration_seconds=4.0,
        )
        self.assertEqual(aligned[1].status, "unresolved")
        self.assertEqual(aligned[1].candidate_whisper_text, "abcdeXXXXX")
        self.assertEqual(aligned[1].candidate_start_ms, 1500)

    def test_scene_plan_quantizes_outward_and_splits_balanced_chunks(self) -> None:
        intervals = [
            vocal.DetectedInterval("silent", 0, 1250),
            vocal.DetectedInterval("voiced", 1250, 11750),
            vocal.DetectedInterval("silent", 11750, 12501),
        ]
        scenes, timeline, padding = vocal.build_scenes(
            intervals,
            total_samples=12501,
            sample_rate=1000,
            max_scene_seconds=5,
        )
        self.assertEqual(timeline, 13)
        self.assertAlmostEqual(padding, 0.499)
        self.assertEqual(
            [(s["state"], s["start_seconds"], s["end_seconds"]) for s in scenes],
            [
                ("silent", 0, 1),
                ("voiced", 1, 5),
                ("voiced", 5, 9),
                ("voiced", 9, 12),
                ("silent", 12, 13),
            ],
        )

    def test_context_normalization_merges_promoted_one_second_gap(self) -> None:
        scenes = [
            {
                "index": 1,
                "state": "voiced",
                "start_seconds": 0,
                "end_seconds": 13,
                "duration_seconds": 13,
                "lyrics_indices": [],
            },
            {
                "index": 2,
                "state": "voiced",
                "start_seconds": 13,
                "end_seconds": 14,
                "duration_seconds": 1,
                "lyrics_indices": [],
            },
            {
                "index": 3,
                "state": "voiced",
                "start_seconds": 14,
                "end_seconds": 24,
                "duration_seconds": 10,
                "lyrics_indices": [],
            },
        ]
        adjustments = vocal.normalize_chainable_scenes(
            scenes,
            max_scene_seconds=15,
        )
        self.assertEqual(adjustments, 1)
        self.assertEqual(
            [
                (scene["state"], scene["start_seconds"], scene["end_seconds"])
                for scene in scenes
            ],
            [("voiced", 0, 12), ("voiced", 12, 24)],
        )
        self.assertTrue(
            all(scene["duration_seconds"] >= 2 for scene in scenes[:-1])
        )

    def test_context_normalization_expands_isolated_one_second_vocal(self) -> None:
        scenes = [
            {
                "index": 1,
                "state": "silent",
                "start_seconds": 0,
                "end_seconds": 5,
                "duration_seconds": 5,
                "lyrics_indices": [],
            },
            {
                "index": 2,
                "state": "voiced",
                "start_seconds": 5,
                "end_seconds": 6,
                "duration_seconds": 1,
                "lyrics_indices": [],
            },
            {
                "index": 3,
                "state": "silent",
                "start_seconds": 6,
                "end_seconds": 10,
                "duration_seconds": 4,
                "lyrics_indices": [],
            },
        ]
        adjustments = vocal.normalize_chainable_scenes(
            scenes,
            max_scene_seconds=15,
        )
        self.assertEqual(adjustments, 1)
        self.assertEqual(
            [
                (scene["state"], scene["start_seconds"], scene["end_seconds"])
                for scene in scenes
            ],
            [("silent", 0, 4), ("voiced", 4, 6), ("silent", 6, 10)],
        )
        self.assertTrue(
            all(scene["duration_seconds"] >= 2 for scene in scenes[:-1])
        )

    def test_prompt_srt_and_json_include_only_resolved_lyrics(self) -> None:
        scenes = [
            {
                "index": 1,
                "state": "silent",
                "start_seconds": 0,
                "end_seconds": 1,
                "duration_seconds": 1,
                "lyrics_indices": [],
            },
            {
                "index": 2,
                "state": "voiced",
                "start_seconds": 1,
                "end_seconds": 3,
                "duration_seconds": 2,
                "lyrics_indices": [1],
            },
        ]
        alignments = [
            vocal.LyricAlignment(
                lyric(1, "こんにちは"), "resolved", 1.0, "こんにちは", 1200, 1900, 2
            ),
            vocal.LyricAlignment(
                lyric(2, "未解決"), "unresolved", 0.2, "", None, None, None
            ),
        ]
        prompt = vocal.build_prompt_text(scenes, alignments)
        scene_comments = [
            line for line in prompt.splitlines() if line.startswith("// シーン ")
        ]
        self.assertEqual(scene_comments, ["// シーン 1", "// シーン 2"])
        self.assertIn("// シーン 1\n# シーン 1秒", prompt)
        self.assertIn("// シーン 2\n# シーン 2秒 継続", prompt)
        self.assertIn("// 歌詞: こんにちは", prompt)
        self.assertNotIn("// 歌詞: 未解決", prompt)
        self.assertIn("* 発声: なし", prompt)
        self.assertIn("* リップシンク: <Subject 1> <- ソースボーカル", prompt)
        module("node_japanese_to_json.compiler.llmj2e").lex_japanese_markdown(prompt)
        prompt_without_lyrics = vocal.build_prompt_text(
            scenes,
            alignments,
            include_lyrics_comments=False,
        )
        self.assertNotIn("// 歌詞:", prompt_without_lyrics)
        self.assertIn("// 検出状態: voiced", prompt_without_lyrics)
        misassigned = [
            vocal.LyricAlignment(
                lyric(1, "誤割当"),
                "resolved",
                1.0,
                "誤割当",
                200,
                800,
                1,
            )
        ]
        with self.assertRaisesRegex(errors.VocalPromptError, "non-voiced scene"):
            vocal.build_prompt_text(scenes, misassigned)

        srt = vocal.build_srt_text(alignments)
        self.assertEqual(
            srt,
            "1\n00:00:01,200 --> 00:00:01,900\nこんにちは\n\n",
        )
        vocal._validate_srt_text(srt)
        shifted = vocal.build_srt_text(
            alignments,
            srt_time_offset=250,
            audio_duration_ms=2500,
        )
        self.assertIn(
            "00:00:01,450 --> 00:00:02,150",
            shifted,
        )
        negatively_shifted = vocal.build_srt_text(
            alignments,
            srt_time_offset=-200,
            audio_duration_ms=2500,
        )
        self.assertIn(
            "00:00:01,000 --> 00:00:01,700",
            negatively_shifted,
        )
        with self.assertRaisesRegex(errors.VocalPromptError, "before the audio start"):
            vocal.build_srt_text(
                alignments,
                srt_time_offset=-1201,
                audio_duration_ms=2500,
            )
        with self.assertRaisesRegex(errors.VocalPromptError, "beyond the audio end"):
            vocal.build_srt_text(
                alignments,
                srt_time_offset=601,
                audio_duration_ms=2500,
            )
        with self.assertRaisesRegex(errors.VocalPromptError, "must be an integer"):
            vocal.build_srt_text(alignments, srt_time_offset=True)
        with self.assertRaisesRegex(errors.VocalPromptError, "overlap"):
            vocal._validate_srt_text(
                "1\n00:00:01,000 --> 00:00:02,000\na\n\n"
                "2\n00:00:01,900 --> 00:00:03,000\nb\n\n"
            )

        self.assertFalse(
            vocal.lyrics_srt_self_test(
                [lyric(1, "こんにちは"), lyric(2, "未解決")], srt
            )["passed"]
        )

        payload = json.loads(
            vocal.build_segments_json(
                sample_rate=1000,
                total_samples=2500,
                timeline_seconds=3,
                trailing_padding=0.5,
                whisper_model="base.pt",
                language_requested="ja",
                language_detected="ja",
                device="cpu",
                settings={"max_scene_seconds": 10},
                intervals=[vocal.DetectedInterval("voiced", 0, 2500)],
                alignments=alignments,
                scenes=scenes,
            )
        )
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["lyrics"][1]["status"], "unresolved")
        self.assertIn("candidate_whisper_text", payload["lyrics"][1])
        self.assertEqual(payload["trailing_padding_seconds"], 0.5)

    def test_node_runs_end_to_end_with_fake_whisper_backend(self) -> None:
        whisper_result = {
            "language": "ja",
            "segments": [
                {
                    "text": "こんにちは 世界",
                    "words": [
                        {"word": "こんにちは", "start": 0.2, "end": 0.8},
                        {"word": "世界", "start": 1.0, "end": 1.5},
                    ],
                }
            ],
        }
        node = vocal.CLVocalToPromptSegments()
        backend = FakeBackend(whisper_result)
        node._backend = backend
        audio = {"waveform": FakeAudioShape(), "sample_rate": 1000}
        intervals = [vocal.DetectedInterval("voiced", 0, 4000)]

        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "base.pt"
            model.write_bytes(b"checkpoint")
            with patch.object(
                vocal, "resolve_whisper_model_name", return_value=model
            ), patch.object(
                vocal, "_resolve_device", return_value="cpu"
            ), patch.object(
                vocal, "analyze_vocal_audio", return_value=intervals
            ), patch.object(
                vocal, "_prepare_whisper_audio", return_value="whisper-pcm"
            ):
                prompt, srt, segments_text, status = node.build_prompt_segments(
                    vocal_audio=audio,
                    lyrics_text="[Verse]\nこんにちは\n世界\n",
                    whisper_model="base.pt",
                    language="ja",
                    device="auto",
                    keep_whisper_loaded=False,
                    max_scene_seconds=10,
                    silence_threshold_dbfs=-45.0,
                    analysis_window_ms=20,
                    min_voiced_ms=120,
                    min_silence_ms=300,
                    voice_padding_ms=80,
                    lyrics_match_threshold=0.8,
                    lyrics_neighbor_threshold=0.45,
                    lyrics_search_seconds=60.0,
                    srt_time_offset=100,
                )

        self.assertIn("// 歌詞: こんにちは", prompt)
        self.assertIn("// 歌詞: 世界", prompt)
        self.assertIn("00:00:00,300 --> 00:00:00,900", srt)
        segments = json.loads(segments_text)
        self.assertEqual(segments["whisper"]["device"], "cpu")
        self.assertEqual(segments["settings"]["srt_time_offset"], 100)
        self.assertTrue(segments["settings"]["include_lyrics_comments"])
        self.assertIn(
            "lyrics=2 resolved (0 neighbor-recovered, 0 resynchronized, "
            "0 targeted-recovered in 0 run(s), 0 scene(s) promoted), "
            "0 unresolved",
            status,
        )
        self.assertEqual(backend.ensure_calls, [(model, "cpu")])
        self.assertEqual(backend.transcribe_calls[0]["language"], "ja")
        self.assertEqual(
            backend.transcribe_calls[0]["initial_prompt"], "こんにちは\n世界"
        )
        self.assertTrue(
            backend.transcribe_calls[0]["condition_on_previous_text"]
        )
        self.assertIn("self_test=passed", status)
        self.assertIn("srt_time_offset=100ms", status)
        self.assertIn("lyrics_comments=enabled", status)
        self.assertGreaterEqual(backend.clear_count, 1)

    def test_quality_diagnostics_log_vad_similarity_and_colored_self_test(self) -> None:
        lyrics = [lyric(1, "one"), lyric(2, "two")]
        alignments = [
            vocal.LyricAlignment(
                lyrics[0], "resolved", 0.8, "one", 100, 200, match_method="primary"
            ),
            vocal.LyricAlignment(
                lyrics[1], "resolved", 1.0, "two", 300, 400, match_method="primary"
            ),
        ]
        srt = vocal.build_srt_text(alignments)
        with self.assertLogs("cl_vocal2promptseg", level="INFO") as captured:
            result = vocal.log_quality_diagnostics(
                lyrics=lyrics,
                srt_text=srt,
                alignments=alignments,
                intervals=[
                    vocal.DetectedInterval("silent", 0, 100),
                    vocal.DetectedInterval("voiced", 100, 900),
                    vocal.DetectedInterval("silent", 900, 1000),
                ],
                sample_rate=1000,
                total_samples=1000,
                silence_threshold_dbfs=-45.0,
                analysis_window_ms=20,
                min_voiced_ms=120,
                min_silence_ms=300,
                voice_padding_ms=80,
                accepted_whisper_words=2,
                invalid_whisper_words=0,
                outside_voiced_words=1,
            )
        output = "\n".join(captured.output)
        self.assertTrue(result["passed"])
        self.assertIn("VAD interval duration stats", output)
        self.assertIn("all similarity min=0.8000 max=1.0000 avg=0.9000", output)
        self.assertIn("\x1b[96m", output)
        self.assertIn("self test passed", output)

        with self.assertLogs("cl_vocal2promptseg", level="ERROR") as captured:
            failed = vocal.log_quality_diagnostics(
                lyrics=lyrics,
                srt_text=vocal.build_srt_text(alignments[:1]),
                alignments=[alignments[0], vocal.LyricAlignment(
                    lyrics[1], "unresolved", 0.2, "", None, None
                )],
                intervals=[vocal.DetectedInterval("voiced", 0, 1000)],
                sample_rate=1000,
                total_samples=1000,
                silence_threshold_dbfs=-45.0,
                analysis_window_ms=20,
                min_voiced_ms=120,
                min_silence_ms=300,
                voice_padding_ms=80,
                accepted_whisper_words=1,
                invalid_whisper_words=0,
                outside_voiced_words=0,
            )
        self.assertFalse(failed["passed"])
        self.assertIn("\x1b[91m", "\n".join(captured.output))
        self.assertIn("self test failed", "\n".join(captured.output))

    def test_neighbor_threshold_must_not_exceed_primary_threshold(self) -> None:
        node = vocal.CLVocalToPromptSegments()
        with self.assertRaisesRegex(
            errors.VocalPromptError, "less than or equal"
        ):
            node.build_prompt_segments(
                vocal_audio={"waveform": FakeAudioShape(), "sample_rate": 1000},
                lyrics_text="歌詞",
                whisper_model="base.pt",
                language="ja",
                device="auto",
                condition_on_previous_text=True,
                keep_whisper_loaded=False,
                max_scene_seconds=10,
                silence_threshold_dbfs=-45.0,
                analysis_window_ms=20,
                min_voiced_ms=120,
                min_silence_ms=300,
                voice_padding_ms=80,
                lyrics_match_threshold=0.4,
                lyrics_neighbor_threshold=0.45,
                lyrics_search_seconds=60.0,
            )

    def test_unsupported_us_language_alias_is_rejected(self) -> None:
        node = vocal.CLVocalToPromptSegments()
        with self.assertRaisesRegex(
            errors.VocalPromptError, "language must be ja, en, or auto"
        ):
            node.build_prompt_segments(
                vocal_audio={"waveform": FakeAudioShape(), "sample_rate": 1000},
                lyrics_text="lyrics",
                whisper_model="base.pt",
                language="us",
                device="auto",
                condition_on_previous_text=True,
                keep_whisper_loaded=False,
                max_scene_seconds=10,
                silence_threshold_dbfs=-45.0,
                analysis_window_ms=20,
                min_voiced_ms=120,
                min_silence_ms=300,
                voice_padding_ms=80,
                lyrics_match_threshold=0.55,
                lyrics_neighbor_threshold=0.45,
                lyrics_search_seconds=60.0,
            )

    def test_provided_suno_lyrics_asset_is_parseable(self) -> None:
        asset = ROOT / "assets" / "bgm" / "bgm_lirics.txt"
        if not asset.is_file():
            self.skipTest("provided Suno Lyrics asset is not present")
        lines = vocal.parse_suno_lyrics(asset.read_text(encoding="utf-8-sig"))
        self.assertGreater(len(lines), 30)
        self.assertTrue(lines[0].text.startswith("黄金の穂が"))
        self.assertIn("共に笑える", lines[-1].text)

    def test_invalid_inputs_are_rejected_before_model_loading(self) -> None:
        with self.assertRaisesRegex(errors.VocalPromptError, "lyrics_text"):
            vocal.parse_suno_lyrics("[Intro]\n")
        with self.assertRaisesRegex(errors.VocalPromptError, "one batch"):
            vocal._validate_audio(
                {"waveform": type("Shape", (), {"shape": (2, 1, 10)})(), "sample_rate": 1}
            )


if __name__ == "__main__":
    unittest.main()
