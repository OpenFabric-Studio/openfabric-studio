"""Text overlays render locally without depending on optional ffmpeg filters."""

from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from app.video_contracts import VideoOverlay


class VideoTextTests(unittest.TestCase):
    def test_export_font_is_bundled_identified_and_independent_of_os_fonts(self) -> None:
        from app.video_text import _font
        self.assertEqual(_font(36).getname(), ("Noto Sans", "Regular"))

    def test_enabled_cjk_scripts_render_distinct_glyphs_instead_of_missing_boxes(self) -> None:
        from app.video_text import _text_image
        for first, second in [('汉', '语'), ('語', '字'), ('カ', 'ナ'), ('한', '국')]:
            with self.subTest(first=first, second=second):
                base = VideoOverlay(id='a'*32, text=first, start_sec=0, end_sec=2)
                with _text_image(base, 704, 396) as left, _text_image(base.model_copy(update={'text': second}), 704, 396) as right:
                    self.assertTrue(left.tobytes() != right.tobytes(), "CJK letters rendered as identical missing-glyph boxes")

    def test_missing_script_glyph_is_reported_before_export(self) -> None:
        from app.video_text import validate_text
        from app.video_projects import VideoProjectError
        with self.assertRaisesRegex(VideoProjectError, 'caption_glyph_unavailable'):
            validate_text(VideoOverlay(id='a'*32, text='𓀀', start_sec=0, end_sec=2), 704, 396)

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
