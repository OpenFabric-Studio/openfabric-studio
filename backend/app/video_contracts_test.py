"""Public video workspace boundaries reject ambiguous or unsafe inputs."""

from __future__ import annotations
import unittest
from pydantic import ValidationError


class VideoContractTests(unittest.TestCase):
    def test_shots_reject_overlap_and_non_frame_aligned_starts(self) -> None:
        from app.video_contracts import UpdateVideoProjectRequest, VideoShotDraft

        with self.assertRaises(ValidationError):
            UpdateVideoProjectRequest(
                revision=1,
                shots=[
                    VideoShotDraft(id="a" * 32, start_sec=0, seconds=4, prompt="one"),
                    VideoShotDraft(
                        id="b" * 32, start_sec=3.75, seconds=4, prompt="two"
                    ),
                ],
            )
        with self.assertRaises(ValidationError):
            VideoShotDraft(id="a" * 32, start_sec=0.01, seconds=4, prompt="one")

    def test_settings_and_overlays_are_bounded(self) -> None:
        from app.video_contracts import VideoProjectSettings, VideoOverlay

        with self.assertRaises(ValidationError):
            VideoProjectSettings(stage2_steps=4)
        with self.assertRaises(ValidationError):
            VideoOverlay(id="a" * 32, text="title", start_sec=4, end_sec=2)
        with self.assertRaises(ValidationError):
            VideoProjectSettings(engine_pack="../model")

    def test_ids_and_revision_are_not_paths(self) -> None:
        from app.video_contracts import VideoRenderRequest, VideoShotDraft

        with self.assertRaises(ValidationError):
            VideoShotDraft(id="../private", start_sec=0, seconds=4, prompt="one")
        with self.assertRaises(ValidationError):
            VideoRenderRequest(revision=0, shot_ids=[])
