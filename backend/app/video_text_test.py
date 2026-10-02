"""Text overlays render locally without depending on optional ffmpeg filters."""

from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from app.video_contracts import VideoOverlay


class VideoTextTests(unittest.TestCase):
    def test_unicode_and_filter_metacharacters_are_rasterized_as_text(self) -> None:
        from app.video_text import render_text
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "title.png"
            render_text(
                VideoOverlay(
                    id="a" * 32, text="Привет: [v] 'hello'", start_sec=0, end_sec=2
                ),
                704,
                396,
                output,
            )
            with Image.open(output) as image:
                self.assertEqual(image.size, (704, 396))
                self.assertEqual(image.mode, "RGBA")
                self.assertIsNotNone(image.getbbox())
