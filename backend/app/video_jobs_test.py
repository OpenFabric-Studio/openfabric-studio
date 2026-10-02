"""Frame counts, prompts, and the audio-to-video command. No model download."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.video_jobs import (
    VideoJobError,
    _style_caption,
    a2v_argv,
    check_window,
    frames_for_seconds,
    video_settings,
    normalize_prompt,
    parse_video_phase,
    partial_mp4,
    picture_size,
    propose_plan,
    reconcile_interrupted,
    shot_ends,
    shot_timeline,
    timeline_duration,
    validate_shots,
)
from app.work_busy import model_work_busy


class FrameTests(unittest.TestCase):
    def test_clip_lengths_land_on_the_ltx_grid(self):
        self.assertEqual(frames_for_seconds(2), 49)
        self.assertEqual(frames_for_seconds(4), 97)
        self.assertEqual(frames_for_seconds(6), 145)
        self.assertEqual(frames_for_seconds(8), 193)
        self.assertEqual(frames_for_seconds(10), 241)
        self.assertEqual(frames_for_seconds(12), 289)
        for frames in (49, 97, 145, 193, 241, 289):
            self.assertEqual(frames % 8, 1)

    def test_other_lengths_are_rejected(self):
        with self.assertRaises(VideoJobError) as caught:
            frames_for_seconds(3)
        self.assertEqual(caught.exception.code, "bad_length")


class PromptTests(unittest.TestCase):
    def test_prompt_collapses_space_and_rejects_empty_or_long(self):
        self.assertEqual(normalize_prompt("  a   singer  "), "a singer")
        with self.assertRaises(VideoJobError) as empty:
            normalize_prompt("   ")
        self.assertEqual(empty.exception.code, "bad_prompt")
        with self.assertRaises(VideoJobError) as long:
            normalize_prompt("a" * 401)
        self.assertEqual(long.exception.code, "bad_prompt")

    def test_window_stays_inside_a_known_song(self):
        check_window(0, 4, 180_000)
        check_window(0, 4, None)
        with self.assertRaises(VideoJobError) as past:
            check_window(179, 4, 180_000)
        self.assertEqual(past.exception.code, "past_end")
        with self.assertRaises(VideoJobError) as start:
            check_window(-1, 4, 180_000)
        self.assertEqual(start.exception.code, "bad_start")


class CommandTests(unittest.TestCase):
    def test_refinement_steps_cannot_exceed_vendor_schedule(self):
        with self.assertRaises(VideoJobError):
            video_settings(30, 4, 3)

    def test_a2v_command_uses_q8_and_keeps_an_mp4_name(self):
        dest = Path("/videos/clip/generated.mp4")
        partial = partial_mp4(dest)
        self.assertEqual(partial.name, "generated.partial.mp4")
        self.assertEqual(partial.suffix, ".mp4")
        argv = a2v_argv(
            Path("/engine/ltx-2-mlx"),
            prompt="a singer",
            audio=Path("/videos/clip/source.wav"),
            frames=97,
            output=partial,
            seed=7,
        )
        self.assertEqual(argv[1], "a2v")
        self.assertIn("--model", argv)
        self.assertIn("dgrauet/ltx-2.3-mlx-q8", argv)
        self.assertIn("--frame-rate", argv)
        self.assertEqual(argv[argv.index("--frame-rate") + 1], "24")
        self.assertEqual(argv[argv.index("--frames") + 1], "97")
        self.assertIn("--low-ram", argv)
        self.assertEqual(argv[argv.index("--height") + 1], "448")
        self.assertEqual(argv[argv.index("--width") + 1], "704")
        self.assertNotIn("--tile-spatial", argv)
        self.assertEqual(argv[argv.index("--stage1-steps") + 1], "30")
        self.assertEqual(argv[argv.index("--stage2-steps") + 1], "3")
        self.assertEqual(argv[argv.index("--cfg-scale") + 1], "3")
        self.assertNotIn("--tile-frames", argv)
        self.assertNotIn("--enable-teacache", argv)
        self.assertNotIn("--stepwise-image-output-dir", argv)
        long = a2v_argv(
            Path("/engine/ltx-2-mlx"),
            prompt="a singer",
            audio=Path("/videos/clip/source.wav"),
            frames=193,
            output=partial,
            seed=7,
        )
        self.assertIn("--low-ram", long)
        self.assertEqual(long[long.index("--tile-frames") + 1], "2")
        self.assertNotIn("--tile-spatial", long)
        self.assertNotIn("--enable-teacache", long)
        self.assertNotIn("--stepwise-image-output-dir", long)
        sharp = a2v_argv(
            Path("/engine/ltx-2-mlx"),
            prompt="a singer",
            audio=Path("/videos/clip/source.wav"),
            frames=97,
            output=partial,
            seed=7,
            stage1_steps=40,
            stage2_steps=3,
            cfg_scale=3.5,
        )
        self.assertEqual(sharp[sharp.index("--stage1-steps") + 1], "40")
        self.assertEqual(sharp[sharp.index("--stage2-steps") + 1], "3")
        self.assertEqual(sharp[sharp.index("--cfg-scale") + 1], "3.5")
        self.assertIn("--low-ram", sharp)
        with self.assertRaises(VideoJobError) as bad:
            video_settings(9, 3, 3)
        self.assertEqual(bad.exception.code, "bad_settings")
        self.assertEqual(picture_size(704, 448), (704, 448))
        self.assertEqual(picture_size(768, 512), (768, 512))
        self.assertEqual(picture_size(1280, 704), (1280, 704))
        with self.assertRaises(VideoJobError) as size:
            picture_size(1920, 1080)
        self.assertEqual(size.exception.code, "bad_settings")
        with self.assertRaises(VideoJobError):
            picture_size(True, 448)
        big = a2v_argv(
            Path("/engine/ltx-2-mlx"),
            prompt="a singer",
            audio=Path("/videos/clip/source.wav"),
            frames=97,
            output=partial,
            seed=7,
            width=1280,
            height=704,
        )
        self.assertEqual(big[big.index("--width") + 1], "1280")
        self.assertEqual(big[big.index("--height") + 1], "704")
        self.assertEqual(big[big.index("--tile-frames") + 1], "2")
        self.assertEqual(big[big.index("--tile-spatial") + 1], "2")

    def test_reconcile_marks_only_abandoned_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / ("a" * 32)
            stale = root / ("b" * 32)
            ready = root / ("c" * 32)
            for path, status in (
                (live, "running"),
                (stale, "queued"),
                (ready, "ready"),
            ):
                path.mkdir()
                (path / "video.json").write_text(
                    json.dumps({"id": path.name, "status": status}), encoding="utf-8"
                )
            reconcile_interrupted(root, live.name)
            self.assertEqual(
                json.loads((live / "video.json").read_text())["status"], "running"
            )
            stale_meta = json.loads((stale / "video.json").read_text())
            self.assertEqual(stale_meta["status"], "failed")
            self.assertEqual(stale_meta["error_code"], "interrupted")
            self.assertEqual(
                json.loads((ready / "video.json").read_text())["status"], "ready"
            )


class PlanTests(unittest.TestCase):
    def test_shot_ends_land_on_whole_shots(self):
        ends = shot_ends(180)
        self.assertEqual(ends[-1], 180)
        self.assertLessEqual(len(ends), 40)
        previous = 0.0
        for end in ends:
            self.assertIn(end - previous, (2, 4, 6, 8, 10, 12))
            previous = end
        self.assertEqual(shot_ends(10), [8, 10])
        self.assertEqual(shot_ends(3), [2])
        self.assertEqual(shot_ends(1), [])
        plan = propose_plan("Night drive", "", 180, None)
        self.assertEqual(
            [shot["start_sec"] + shot["seconds"] for shot in plan["shots"]], ends
        )

    def test_a_short_prefix_does_not_grow_a_black_tail(self):
        shots = [{"start_sec": 0, "seconds": 8}, {"start_sec": 8, "seconds": 8}]
        self.assertEqual(timeline_duration(shots, 180), 16)
        self.assertEqual(timeline_duration([{"start_sec": 0, "seconds": 8}], 9), 9)

    def test_model_work_counts_a_running_song_or_training(self):
        self.assertFalse(
            model_work_busy(
                {"jobs": {"queued": 0, "running": 0}, "queue_size": 0},
                {"is_training": False},
            )
        )
        self.assertTrue(model_work_busy({"jobs": {"queued": 0, "running": 1}}, {}))
        self.assertTrue(model_work_busy({"jobs": {"queued": 1, "running": 0}}, {}))
        self.assertTrue(model_work_busy({"queue_size": 2}, {}))
        self.assertTrue(model_work_busy({}, {"is_training": True}))

    def test_a_three_minute_song_is_covered_by_editable_shots(self):
        plan = propose_plan("Night drive", "[Chorus]\nhello there\n", 180, None)
        shots = plan["shots"]
        self.assertGreaterEqual(sum(shot["seconds"] for shot in shots), 179)
        self.assertLessEqual(sum(shot["seconds"] for shot in shots), 180.25)
        self.assertLessEqual(len(shots), 40)
        self.assertTrue(all(shot["seconds"] in (2, 4, 6, 8, 10, 12) for shot in shots))
        opening = shots[0]["prompt"].lower()
        self.assertNotIn("hello there", opening)
        self.assertNotIn("night drive", opening)
        self.assertIn("group", opening)
        self.assertIn("stage", opening)
        self.assertIn("low warm light", opening)

    def test_shot_lines_are_pictures_of_the_style(self):
        lyrics = "\n".join(
            [
                "[Verse]",
                "Love is the frequency",
                "I hear it under glass",
                "[Pre-Chorus]",
                "hold",
                "[Chorus]",
                "Love is the frequency",
                "[Припев]",
                "привет",
            ]
        )
        plan = propose_plan(
            "gypsy-test-1",
            lyrics,
            32,
            None,
            "gypsy jazz, passionate, female vocal, violin, cello, acoustic guitar",
        )
        shots = plan["shots"]
        self.assertGreaterEqual(len(shots), 4)
        first, second = shots[0]["prompt"], shots[1]["prompt"]
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 400)
        self.assertTrue(first.endswith("."))
        for prompt in (first, second):
            lowered = prompt.lower()
            self.assertNotIn("gypsy-test-1", lowered)
            self.assertNotIn("love is the frequency", lowered)
            self.assertNotIn("under glass", lowered)
            self.assertNotIn("on-screen", lowered)
            self.assertNotIn("no words", lowered)
            self.assertNotIn(" a man ", f" {lowered} ")
            self.assertIn("woman", lowered)
            self.assertIn("jazz club", lowered)
            self.assertIn("wooden stage", lowered)
        joined = f"{first} {second}".lower()
        self.assertIn("acoustic guitar", joined)
        self.assertIn("violin", joined)
        self.assertNotIn("a guitar fills", joined)
        chorus = propose_plan(
            "t", "[Chorus]\nhello there\n", 8, None, "jazz, female vocal"
        )
        self.assertIn("group", chorus["shots"][0]["prompt"].lower())
        self.assertNotIn("hello there", chorus["shots"][0]["prompt"].lower())
        pre = propose_plan(
            "t", "[Pre-Chorus]\nhello there\n", 8, None, "jazz, female vocal"
        )
        self.assertIn("rises", pre["shots"][0]["prompt"].lower())
        russian = propose_plan("t", "[Припев]\nпривет\n", 8, None, "jazz, female vocal")
        self.assertNotIn("привет", russian["shots"][0]["prompt"].lower())
        self.assertIn("group", russian["shots"][0]["prompt"].lower())
        quiet = propose_plan(
            "t",
            "[Verse]\na\n[Verse]\nb\n[Verse]\nc\n",
            24,
            [0.1] * 30,
            "jazz, female vocal, violin",
        )
        self.assertIn(
            "movement stays small and slow", quiet["shots"][2]["prompt"].lower()
        )
        loud = propose_plan("t", "[Verse]\na\n", 8, [0.9] * 10, "jazz, female vocal")
        self.assertIn("the playing is full", loud["shots"][0]["prompt"].lower())
        instrumental = propose_plan("t", "", 8, None, "jazz, instrumental, piano")
        self.assertIn("musicians", instrumental["shots"][0]["prompt"].lower())
        self.assertNotIn("microphone", instrumental["shots"][0]["prompt"].lower())
        self.assertEqual(
            _style_caption(
                {"params_json": json.dumps({"prompt": "gypsy jazz, violin"})}
            ),
            "gypsy jazz, violin",
        )

    def test_overlapping_shots_are_rejected(self):
        with self.assertRaises(VideoJobError) as caught:
            validate_shots(
                [
                    {"start_sec": 0, "seconds": 8, "prompt": "a singer"},
                    {"start_sec": 7, "seconds": 8, "prompt": "a singer"},
                ],
                180_000,
            )
        self.assertEqual(caught.exception.code, "overlap")

    def test_gaps_between_shots_stay_in_the_timeline(self):
        events = shot_timeline(
            [
                {"start_sec": 0, "seconds": 8},
                {"start_sec": 10, "seconds": 8},
            ],
            20,
        )
        self.assertEqual(
            events, [("shot", 0.0), ("gap", 2.0), ("shot", 1.0), ("gap", 2.0)]
        )

    def test_phase_uses_the_latest_download_or_denoise_bar(self):
        text = "Fetching 3/23 files\nDenoising 6/30"
        self.assertEqual(parse_video_phase(text), ("denoise", 6, 30))
