from __future__ import annotations

import unittest

from .helpers import PKG, ROOT, module


audio_nodes = module("node_audio_pad.node")


class FakeWaveform:
    def __init__(self, samples, *, batch=1, channels=1):
        self.samples = list(samples)
        self.shape = (batch, channels, len(self.samples))

    def new_zeros(self, shape):
        return FakeWaveform(
            [0.0] * int(shape[-1]), batch=int(shape[0]), channels=int(shape[1])
        )

    def __setitem__(self, key, value):
        ellipsis, interval = key
        if ellipsis is not Ellipsis or not isinstance(interval, slice):
            raise AssertionError(f"unexpected fake tensor index: {key!r}")
        self.samples[interval] = value.samples


def audio(samples, sample_rate=4):
    return {
        "waveform": FakeWaveform(samples),
        "sample_rate": sample_rate,
        "metadata": "preserved",
    }


class AudioPadTests(unittest.TestCase):
    def test_dedicated_spec_tracks_public_contract(self) -> None:
        spec = (ROOT / "docs" / "cl_audio_pad_spec.md").read_text(encoding="utf-8")
        for marker in (
            "CLAudioPad",
            "CLAudioPadPair",
            "match_audio",
            "auto_safe",
            "exact_frames",
            "h3_target_frames",
            "pad_position",
            "padded_audio_a",
            "padded_audio_b",
        ):
            self.assertIn(marker, spec)

    def test_registration_and_metadata(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLAudioPad"]
        self.assertIs(cls, audio_nodes.CLAudioPad)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLAudioPad"],
            "CL Audio Pad (PCM Silence)",
        )
        self.assertEqual(cls.FUNCTION, "pad_audio")
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Audio Tools")
        self.assertEqual(cls.RETURN_TYPES[0], "AUDIO")
        self.assertFalse(cls.OUTPUT_NODE)

    def test_ui_inputs_expose_plan_independent_h3_targets(self) -> None:
        inputs = audio_nodes.CLAudioPad.INPUT_TYPES()
        self.assertEqual(
            list(inputs["required"]),
            [
                "audio",
                "target_duration_seconds",
                "extra_padding_seconds",
                "pad_position",
                "h3_frame_mode",
                "h3_target_frames",
            ],
        )
        self.assertEqual(inputs["required"]["target_duration_seconds"][1]["default"], 0.0)
        self.assertEqual(inputs["required"]["extra_padding_seconds"][1]["default"], 0.0)
        self.assertEqual(inputs["required"]["pad_position"][1]["default"], "end")
        self.assertEqual(inputs["required"]["h3_frame_mode"][1]["default"], "auto_safe")
        self.assertEqual(inputs["required"]["h3_target_frames"][1]["default"], 0)
        self.assertNotIn("plan", inputs.get("optional", {}))
        self.assertEqual(inputs["optional"]["match_audio"][0], "AUDIO")

    def test_ui_duration_pads_end_with_exact_zero_pcm(self) -> None:
        source = audio([1.0, 2.0, 3.0, 4.0])
        result = audio_nodes.CLAudioPad.pad_audio(
            source, 2.0, 0.0, "end", "disabled", 0
        )
        padded, original, duration, added, status = result
        self.assertEqual(padded["waveform"].samples, [1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(padded["metadata"], "preserved")
        self.assertEqual((original, duration, added), (1.0, 2.0, 1.0))
        self.assertIn("padded 4 zero sample(s)", status)

    def test_exact_frame_target_calculates_samples_and_extra_padding(self) -> None:
        source = audio([1.0, 2.0, 3.0, 4.0])
        padded, original, duration, added, status = audio_nodes.CLAudioPad.pad_audio(
            source,
            target_duration_seconds=0.0,
            extra_padding_seconds=0.5,
            pad_position="both",
            h3_frame_mode="exact_frames",
            h3_target_frames=60,
        )
        self.assertEqual(
            padded["waveform"].samples,
            [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 0.0],
        )
        self.assertEqual((original, duration, added), (1.0, 3.0, 2.0))
        self.assertIn("exact H3 target=60 frames at 24 fps", status)
        self.assertIn("extra=0.500000s", status)

    def test_exact_h3_shortage_is_calculated_at_sample_precision(self) -> None:
        target, description = audio_nodes._timeline_frame_target_samples(
            0.0,
            48000,
            "exact_frames",
            1348,
        )
        self.assertEqual(target, 2_696_000)
        self.assertEqual(target - 2_555_009, 140_991)
        self.assertEqual(description, "exact H3 target=1348 frames at 24 fps")

        corrected_target, _ = audio_nodes._timeline_frame_target_samples(
            0.0,
            48000,
            "exact_frames",
            1280,
        )
        self.assertEqual(corrected_target, 2_560_000)
        self.assertEqual(corrected_target - 2_555_009, 4_991)

    def test_auto_safe_rounds_to_whole_second_and_reserves_16_frames(self) -> None:
        target, description = audio_nodes._timeline_frame_target_samples(
            259.8,
            48000,
            "auto_safe",
            0,
        )
        self.assertEqual(target, 12_512_000)
        self.assertIn("6256 frames at 24 fps", description)
        self.assertIn("260s timeline + 16 safety frames", description)

    def test_long_audio_is_never_trimmed(self) -> None:
        source = audio([1.0, 2.0, 3.0, 4.0])
        result = audio_nodes.CLAudioPad.pad_audio(
            source,
            target_duration_seconds=0.5,
            extra_padding_seconds=0.0,
            pad_position="end",
            h3_frame_mode="disabled",
        )
        self.assertIs(result[0], source)
        self.assertEqual(result[1:4], (1.0, 1.0, 0.0))
        self.assertIn("unchanged", result[4])

    def test_match_audio_pads_shorter_track_to_authoritative_duration(self) -> None:
        source = audio([1.0, 2.0, 3.0])
        reference = audio([9.0, 8.0, 7.0, 6.0])
        result = audio_nodes.CLAudioPad.pad_audio(
            source,
            target_duration_seconds=0.0,
            extra_padding_seconds=0.0,
            pad_position="end",
            h3_frame_mode="disabled",
            match_audio=reference,
        )
        padded, original, duration, added, status = result
        self.assertEqual(padded["waveform"].samples, [1.0, 2.0, 3.0, 0.0])
        self.assertEqual((original, duration, added), (0.75, 1.0, 0.25))
        self.assertIn("match_audio=4 samples at 4 Hz (1.000000s)", status)

    def test_match_audio_duration_is_converted_to_primary_sample_rate(self) -> None:
        source = audio([1.0, 2.0], sample_rate=4)
        reference = audio([9.0, 8.0, 7.0], sample_rate=2)
        padded = audio_nodes.CLAudioPad.pad_audio(
            source,
            target_duration_seconds=0.0,
            extra_padding_seconds=0.0,
            pad_position="end",
            h3_frame_mode="disabled",
            match_audio=reference,
        )[0]
        self.assertEqual(
            padded["waveform"].samples,
            [1.0, 2.0, 0.0, 0.0, 0.0, 0.0],
        )

    def test_match_audio_never_trims_a_longer_primary_track(self) -> None:
        source = audio([1.0, 2.0, 3.0, 4.0])
        reference = audio([9.0, 8.0])
        result = audio_nodes.CLAudioPad.pad_audio(
            source,
            target_duration_seconds=0.0,
            extra_padding_seconds=0.0,
            pad_position="end",
            h3_frame_mode="disabled",
            match_audio=reference,
        )
        self.assertIs(result[0], source)
        self.assertEqual(result[1:4], (1.0, 1.0, 0.0))

    def test_audio_pad_uses_its_own_log_namespace(self) -> None:
        with self.assertLogs("cl_audiopad", level="INFO") as captured:
            audio_nodes.CLAudioPad.pad_audio(
                audio([1.0]), 0.5, 0.0, "end", "disabled", 0
            )
        output = "\n".join(captured.output)
        self.assertIn("\x1b[96m", output)
        self.assertIn("[cl_audiopad] success: padded", output)

    def test_start_padding_shifts_original_samples(self) -> None:
        source = audio([1.0, 2.0])
        padded = audio_nodes.CLAudioPad.pad_audio(
            source, 1.0, 0.0, "start", "disabled", 0
        )[0]
        self.assertEqual(padded["waveform"].samples, [0.0, 0.0, 1.0, 2.0])

    def test_invalid_audio_plan_and_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "AUDIO object"):
            audio_nodes.CLAudioPad.pad_audio(None, 0.0, 0.0, "end", "disabled", 0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            audio_nodes.CLAudioPad.pad_audio(
                audio([1.0]), 0.0, 0.0, "end", "exact_frames", 0
            )
        with self.assertRaisesRegex(ValueError, "target_duration_seconds"):
            audio_nodes.CLAudioPad.pad_audio(audio([1.0]), -1.0, 0.0, "end", "disabled", 0)
        with self.assertRaisesRegex(ValueError, "pad_position"):
            audio_nodes.CLAudioPad.pad_audio(audio([1.0]), 0.0, 0.0, "middle", "disabled", 0)
        with self.assertRaisesRegex(ValueError, "waveform"):
            audio_nodes.CLAudioPad.pad_audio(
                audio([1.0]), 0.0, 0.0, "end", "disabled", 0, match_audio={}
            )


class AudioPadPairTests(unittest.TestCase):
    def test_registration_and_metadata(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLAudioPadPair"]
        self.assertIs(cls, audio_nodes.CLAudioPadPair)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLAudioPadPair"],
            "CL Audio Pad Pair (PCM Silence)",
        )
        self.assertEqual(cls.FUNCTION, "pad_audio_pair")
        self.assertEqual(cls.RETURN_NAMES[:2], ("padded_audio_a", "padded_audio_b"))

    def test_ui_exposes_two_tracks_and_plan_independent_h3_targets(self) -> None:
        inputs = audio_nodes.CLAudioPadPair.INPUT_TYPES()
        self.assertEqual(
            list(inputs["required"]),
            [
                "audio_a",
                "audio_b",
                "target_duration_seconds",
                "extra_padding_seconds",
                "pad_position",
                "h3_frame_mode",
                "h3_target_frames",
            ],
        )
        self.assertNotIn("plan", inputs.get("optional", {}))
        self.assertEqual(inputs["required"]["h3_frame_mode"][1]["default"], "auto_safe")

    def test_shorter_first_track_is_padded_to_second_track(self) -> None:
        first = audio([1.0, 2.0, 3.0])
        second = audio([4.0, 5.0, 6.0, 7.0])
        result = audio_nodes.CLAudioPadPair.pad_audio_pair(
            first, second, 0.0, 0.0, "end", "disabled", 0
        )
        self.assertEqual(result[0]["waveform"].samples, [1.0, 2.0, 3.0, 0.0])
        self.assertIs(result[1], second)
        self.assertEqual(result[2:7], (0.75, 1.0, 1.0, 0.25, 0.0))

    def test_shorter_second_track_is_padded_to_first_track(self) -> None:
        first = audio([1.0, 2.0, 3.0, 4.0])
        second = audio([5.0, 6.0])
        result = audio_nodes.CLAudioPadPair.pad_audio_pair(
            first, second, 0.0, 0.0, "end", "disabled", 0
        )
        self.assertIs(result[0], first)
        self.assertEqual(result[1]["waveform"].samples, [5.0, 6.0, 0.0, 0.0])
        self.assertEqual(result[2:7], (1.0, 0.5, 1.0, 0.0, 0.5))

    def test_exact_frames_and_extra_padding_apply_once_to_both_tracks(self) -> None:
        result = audio_nodes.CLAudioPadPair.pad_audio_pair(
            audio([1.0, 2.0, 3.0]),
            audio([4.0, 5.0]),
            0.0,
            0.5,
            "both",
            "exact_frames",
            24,
        )
        self.assertEqual(
            result[0]["waveform"].samples,
            [0.0, 1.0, 2.0, 3.0, 0.0, 0.0],
        )
        self.assertEqual(
            result[1]["waveform"].samples,
            [0.0, 0.0, 4.0, 5.0, 0.0, 0.0],
        )
        self.assertEqual(result[4:7], (1.5, 0.75, 1.0))
        self.assertIn("exact H3 target=24 frames at 24 fps", result[7])

    def test_pair_uses_each_tracks_sample_rate(self) -> None:
        first = audio([1.0, 2.0], sample_rate=2)
        second = audio([3.0, 4.0, 5.0, 6.0, 7.0, 8.0], sample_rate=4)
        result = audio_nodes.CLAudioPadPair.pad_audio_pair(
            first, second, 0.0, 0.0, "end", "disabled", 0
        )
        self.assertEqual(result[0]["waveform"].samples, [1.0, 2.0, 0.0])
        self.assertIs(result[1], second)
        self.assertEqual(result[4], 1.5)

    def test_pair_logs_cyan_success(self) -> None:
        with self.assertLogs("cl_audiopad", level="INFO") as captured:
            audio_nodes.CLAudioPadPair.pad_audio_pair(
                audio([1.0]), audio([2.0, 3.0]), 0.0, 0.0, "end", "disabled", 0
            )
        output = "\n".join(captured.output)
        self.assertIn("\x1b[96m", output)
        self.assertIn("[cl_audiopad] success: aligned audio pair", output)
