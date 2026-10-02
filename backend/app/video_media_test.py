"""Real CPU media validation, with generated temporary audiovisual fixtures."""

from __future__ import annotations
import shutil, subprocess, tempfile, unittest
from pathlib import Path


class VideoMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_video_cannot_publish_with_long_audio(self) -> None:
        from app.video_media import validate_media, VideoMediaError

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "short.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=704x448:r=24:d=0.5",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=4",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(output),
                ],
                check=True,
            )
            with self.assertRaises(VideoMediaError):
                await validate_media(output, 4, (704, 448), require_audio=True)

    async def test_valid_cpu_video_and_dimensions(self) -> None:
        from app.video_media import validate_media, VideoMediaError

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "valid.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=704x448:r=24:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=2",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(output),
                ],
                check=True,
            )
            info = await validate_media(output, 2, (704, 448), require_audio=True)
            self.assertAlmostEqual(info.video_duration, 2, places=1)
            with self.assertRaises(VideoMediaError):
                await validate_media(output, 2, (768, 512), require_audio=True)
