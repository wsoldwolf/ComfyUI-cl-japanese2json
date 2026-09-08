from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .helpers import PKG, ROOT, module


vocal = module("vocal_prompt_nodes")
errors = module("compiler.errors")


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
    def __init__(self, result: dict) -> None:
        self.result = result
        self.ensure_calls: list[tuple[Path, str]] = []
        self.transcribe_calls: list[dict] = []
        self.clear_count = 0

    def ensure_loaded(self, model_path: Path, device: str):
        self.ensure_calls.append((model_path, device))
        return self

    def transcribe(self, audio, *, language, device):
        self.transcribe_calls.append(
            {"audio": audio, "language": language, "device": device}
        )
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
            required = cls.INPUT_TYPES()["required"]
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
                "lyrics_search_seconds",
            ],
        )
        self.assertTrue(required["lyrics_text"][1]["forceInput"])
        self.assertEqual(required["max_scene_seconds"][1]["default"], 10)

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

    def test_normalization_handles_width_case_kana_spaces_and_punctuation(self) -> None:
        self.assertEqual(
            vocal.normalize_match_text(" ＡＢＣ・カタカナ！ "),
            "abcかたかな",
        )

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
        self.assertIn("// こんにちは", prompt)
        self.assertNotIn("// 未解決", prompt)
        self.assertIn("* 発声: なし", prompt)
        self.assertIn("* リップシンク: <Subject 1> <- ソースボーカル", prompt)
        module("compiler.llmj2e").lex_japanese_markdown(prompt)

        srt = vocal.build_srt_text(alignments)
        self.assertEqual(
            srt,
            "1\n00:00:01,200 --> 00:00:01,900\nこんにちは\n\n",
        )
        vocal._validate_srt_text(srt)
        with self.assertRaisesRegex(errors.VocalPromptError, "overlap"):
            vocal._validate_srt_text(
                "1\n00:00:01,000 --> 00:00:02,000\na\n\n"
                "2\n00:00:01,900 --> 00:00:03,000\nb\n\n"
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
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["lyrics"][1]["status"], "unresolved")
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
                    lyrics_search_seconds=60.0,
                )

        self.assertIn("// こんにちは", prompt)
        self.assertIn("// 世界", prompt)
        self.assertIn("00:00:00,200 --> 00:00:00,800", srt)
        self.assertEqual(json.loads(segments_text)["whisper"]["device"], "cpu")
        self.assertIn("lyrics=2 resolved, 0 unresolved", status)
        self.assertEqual(backend.ensure_calls, [(model, "cpu")])
        self.assertEqual(backend.transcribe_calls[0]["language"], "ja")
        self.assertGreaterEqual(backend.clear_count, 1)

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
