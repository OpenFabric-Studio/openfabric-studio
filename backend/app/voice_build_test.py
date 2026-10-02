"""Pure helpers and a short ffmpeg merge, without Demucs or Seed-VC."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.voice_build import (
    apply_view,
    ProcSlot,
    ClipStat,
    choose_reference_clips,
    concat_filter,
    failure_summary,
    last_log_line,
    merge_vocals,
    build_extract_report,
    judge_extract,
    parse_fraction_percent,
    parse_mean_volume,
    parse_prep_report,
    parse_train_progress,
    public_view,
    rank_reference_clips,
    reconcile_interrupted,
    partial_wav,
    remix_filter,
    slice_dataset,
    vocals_ready,
    VoiceBuildError,
)


class ApplyViewTests(unittest.TestCase):
    def test_status_keeps_the_voice_and_the_clock(self):
        view = apply_view({
            "status": "running",
            "phase": "converting",
            "voice_id": "a" * 32,
            "started_at": 100.5,
            "duration_sec": 180,
        })
        self.assertEqual(view["status"], "running")
        self.assertEqual(view["phase"], "converting")
        self.assertEqual(view["voice_id"], "a" * 32)
        self.assertEqual(view["voice_name"], "")
        self.assertEqual(view["started_at"], 100.5)
        self.assertEqual(view["duration_sec"], 180)
        self.assertEqual(apply_view({"phase": "nope"})["phase"], "")


class ParseTests(unittest.TestCase):
    def test_mean_volume_uses_the_last_reading(self):
        text = "mean_volume: -12.5 dB\nmean_volume: -inf dB"
        self.assertEqual(parse_mean_volume(text), float("-inf"))

    def test_train_step_uses_the_last_line(self):
        text = "epoch 0, step 10, loss: 1.5\nepoch 0, step 20, loss: 1.2"
        self.assertEqual(parse_train_progress(text), 20)

    def test_train_step_adds_batches_after_the_logged_step(self):
        text = (
            " 13%|███ | 6/46 [00:08<00:54,  1.36s/it]epoch 4, step 190, loss: 0.67\n"
            " 15%|███ | 7/46 [00:09<00:52,  1.35s/it]\n"
            " 33%|███ | 15/46 [00:20<00:42,  1.36s/it]\n"
        )
        self.assertEqual(parse_train_progress(text), 199)

    def test_train_step_counts_across_an_epoch_wrap(self):
        text = (
            "epoch 0, step 40, loss: 1.0  40/46\n"
            "100%|██████████| 46/46 [01:01<00:00,  1.34s/it]\n"
            "  0%|          | 0/46 [00:00<?, ?it/s]\n"
            " 11%|█         | 5/46 [00:06<00:56,  1.37s/it]\n"
        )
        self.assertEqual(parse_train_progress(text), 51)

    def test_fraction_percent_uses_the_last_ratio(self):
        text = "  0%| | 0/100 [00:00]\r 40%|████ | 40/100 [00:10]"
        self.assertEqual(parse_fraction_percent(text), 40)
        self.assertIsNone(parse_fraction_percent("no fraction here"))

    def test_reference_prefers_a_clear_clip_over_a_hot_one(self):
        ranked = rank_reference_clips([
            ClipStat("a_hot.wav", 20, -2),
            ClipStat("z_clear.wav", 8, -18),
            ClipStat("m_quiet.wav", 8, -30),
        ])
        self.assertEqual(ranked[0].name, "z_clear.wav")
        self.assertNotIn("a_hot.wav", [clip.name for clip in ranked])
        chosen = choose_reference_clips([
            ClipStat("a_hot.wav", 20, -2),
            ClipStat("z_clear.wav", 20, -18),
        ])
        self.assertEqual(chosen, ["z_clear.wav"])

    def test_reference_stops_at_the_clearest_clip(self):
        from app.voice_build import reference_trim_seconds
        chosen = choose_reference_clips([
            ClipStat("b_ok.wav", 19.5, -18),
            ClipStat("a_clearer.wav", 19.5, -16),
        ])
        self.assertEqual(chosen, ["b_ok.wav"])
        short = choose_reference_clips([
            ClipStat("c.wav", 6, -20),
            ClipStat("a.wav", 6, -18),
            ClipStat("b.wav", 6, -14),
        ])
        self.assertEqual(short, ["a.wav", "c.wav"])
        self.assertIsNone(reference_trim_seconds(9.9))
        self.assertEqual(reference_trim_seconds(40), 10.0)

    def test_last_log_line_ignores_carriage_returns(self):
        self.assertEqual(last_log_line("loading\rdownloading model"), "downloading model")

    def test_failure_summary_skips_warnings(self):
        text = (
            "FutureWarning: torch.jit.script is deprecated\n"
            "  warnings.warn(\n"
            "Traceback (most recent call last):\n"
            '  File "train.py", line 7, in <module>\n'
            "ModuleNotFoundError: No module named 'dac'"
        )
        self.assertEqual(failure_summary(text), "ModuleNotFoundError: No module named 'dac'")

    def test_prep_report_uses_the_last_json_line(self):
        text = (
            "loading\n"
            '{"input_seconds": 3.5, "output_seconds": 2.0, "kept": 1, "dropped": 0}\n'
            '{"input_seconds": 9, "output_seconds": 8, "kept": 2, "dropped": 1}\n'
            "too_quiet\n"
        )
        report = parse_prep_report(text)
        self.assertEqual(report["input_seconds"], 9)
        self.assertEqual(report["output_seconds"], 8)
        self.assertEqual(parse_prep_report("not json"), {})

    def test_vocals_ready_requires_clips_newer_than_the_recordings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.wav"
            source.write_bytes(b"song")
            self.assertFalse(vocals_ready(root, [source]))
            (root / "dataset").mkdir()
            (root / "dataset" / "clip_000.wav").write_bytes(b"clip")
            (root / "voice.wav").write_bytes(b"voice")
            (root / "reference.wav").write_bytes(b"ref")
            self.assertFalse(vocals_ready(root, [source]))
            (root / "dataset" / "prep.txt").write_text("1\n", encoding="utf-8")
            self.assertTrue(vocals_ready(root, [source]))
            self.assertFalse(vocals_ready(root, [source], clean=True))
            (root / "dataset" / "clean.txt").write_text("1\n", encoding="utf-8")
            self.assertTrue(vocals_ready(root, [source], clean=True))
            self.assertFalse(vocals_ready(root, [source], clean=False))
            (root / "dataset" / "prep.txt").write_text("0\n", encoding="utf-8")
            self.assertFalse(vocals_ready(root, [source], clean=True))

    def test_filters_keep_level_and_channel_count(self):
        self.assertIn("concat=n=2:v=0:a=1", concat_filter(2))
        self.assertIn("normalize=0", remix_filter())
        self.assertIn("amix=inputs=4", remix_filter())

    def test_partial_mix_keeps_a_wav_extension(self):
        dest = Path("/songs/track.voiced.wav")
        partial = partial_wav(dest)
        self.assertEqual(partial.suffix, ".wav")
        self.assertEqual(partial.name, "track.voiced.partial.wav")
        self.assertNotEqual(partial, dest)

    def test_public_view_hides_checkpoint_paths(self):
        view = public_view(
            {"id": "abc", "name": "A", "checkpoint": "/secret/model.pth", "status": "ready"},
            has_preview=True,
            usable=True,
        )
        self.assertNotIn("checkpoint", view)
        self.assertEqual(view["status"], "ready")
        self.assertTrue(view["usable"])
        self.assertEqual(view["recordings"], [])
        self.assertFalse(view["prepared"])
        self.assertEqual(view["prep_kept_sec"], 0)
        prepared = public_view(
            {"prep_version": "1", "prep_kept_sec": 90, "prep_total_sec": "600", "status": "ready"},
            has_preview=False,
            usable=True,
        )
        self.assertTrue(prepared["prepared"])
        self.assertEqual(prepared["prep_kept_sec"], 90)
        self.assertEqual(prepared["prep_total_sec"], 600)
        self.assertNotIn("prep_version", prepared)
        self.assertIsNone(view["extract_report"])
        reported = public_view(
            {
                "status": "ready",
                "extract_report": {
                    "songs": 10,
                    "skipped": 0,
                    "cleaned": True,
                    "gaps_shortened": True,
                    "extracted_sec": 2135.5,
                    "kept_sec": 908.18,
                    "kept_pieces": 95,
                    "dropped": 75,
                    "trimmed": 14,
                    "max_gap_sec": 0.52,
                    "level_db": -17.6,
                    "pitch": 0.914,
                    "peak": 0.99,
                    "quality": "clear",
                    "notes": ["clipped", "hack"],
                    "secret": "/tmp/voice.wav",
                },
            },
            has_preview=True,
            usable=True,
        )
        shown = reported["extract_report"]
        self.assertEqual(shown["quality"], "clear")
        self.assertEqual(shown["notes"], ["clipped"])
        self.assertEqual(shown["kept_pieces"], 95)
        self.assertEqual(shown["kept_sec"], 908.18)
        self.assertNotIn("secret", shown)

    def test_extract_report_describes_clear_limited_singing(self):
        quality, notes = judge_extract(0.91, -17.6, 0.99)
        self.assertEqual(quality, "clear")
        self.assertEqual(notes, ["clipped"])
        weak, weak_notes = judge_extract(0.1, -40.0, 0.2)
        self.assertEqual(weak, "weak")
        self.assertIn("quiet", weak_notes)
        report = build_extract_report(
            {"output_seconds": 12.0, "kept": 2, "dropped": 1, "pitch": 0.8, "peak": 0.4, "level_db": -16.0, "max_gap_sec": 0.4},
            songs=3,
            skipped=1,
            cleaned=False,
            extracted_sec=40,
            trimmed=4,
        )
        self.assertEqual(report["quality"], "clear")
        self.assertEqual(report["songs"], 3)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["kept_sec"], 12.0)
        self.assertEqual(report["trimmed"], 4)
        self.assertFalse(report["cleaned"])

    def test_reconcile_only_running_builds(self):
        self.assertIsNone(reconcile_interrupted({"status": "ready"}))
        fixed = reconcile_interrupted({"status": "training", "error_code": ""})
        self.assertEqual(fixed["status"], "failed")
        self.assertEqual(fixed["error_code"], "interrupted")
        preparing = reconcile_interrupted({"status": "preparing"})
        self.assertEqual(preparing["status"], "failed")
        self.assertEqual(preparing["error_code"], "interrupted")


class FfmpegTests(unittest.IsolatedAsyncioTestCase):
    async def test_merge_and_slice_keeps_a_tone(self):
        from app.voice_build import _ffmpeg

        slot = ProcSlot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tone_a = root / "a.wav"
            tone_b = root / "b.wav"
            merged = root / "voice.wav"
            reference = root / "reference.wav"
            for dest, freq in ((tone_a, 440), (tone_b, 554)):
                await _ffmpeg(
                    ["-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2", "-ar", "44100", "-ac", "2", str(dest)],
                    slot,
                    lambda: False,
                )
            await merge_vocals([tone_a, tone_b], merged, slot, lambda: False)
            kept = await slice_dataset(merged, root / "dataset", reference, slot, lambda: False)
            self.assertGreaterEqual(kept, 1)
            self.assertTrue(reference.is_file())
            self.assertGreater(reference.stat().st_size, 1000)

    async def test_silence_is_rejected(self):
        from app.voice_build import _ffmpeg

        slot = ProcSlot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            silent = root / "silent.wav"
            await _ffmpeg(
                ["-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", str(silent)],
                slot,
                lambda: False,
            )
            with self.assertRaises(VoiceBuildError) as raised:
                await slice_dataset(silent, root / "dataset", root / "reference.wav", slot, lambda: False)
            self.assertEqual(raised.exception.code, "too_quiet")
