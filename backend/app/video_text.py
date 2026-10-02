"""Rasterize bounded, timed title/lyric overlays; text never enters ffmpeg syntax."""

from __future__ import annotations
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .video_contracts import VideoOverlay
from .video_projects import VideoProjectError


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    choices = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        windows,
    ]
    for path in choices:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _text_image(overlay: VideoOverlay, width: int, height: int) -> Image.Image:
    font = _font(overlay.font_size)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    limit = width * 0.9
    lines: list[str] = []
    for paragraph in overlay.text.splitlines():
        current = ""
        for word in paragraph.split():
            candidate = (current + " " + word).strip()
            if draw.textlength(candidate, font=font) > limit:
                if current:
                    lines.append(current)
                    current = ""
                if draw.textlength(word, font=font) > limit:
                    for char in word:
                        if draw.textlength(current + char, font=font) > limit:
                            lines.append(current)
                            current = ""
                        current += char
                else:
                    current = word
            else:
                current = candidate
        lines.append(current)
    text = "\n".join(lines)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6, stroke_width=2)
    text_height = bbox[3] - bbox[1]
    if text_height > height * 0.8:
        raise VideoProjectError("overlay_text_too_large")
    y = (
        height * 0.08
        if overlay.position == "top"
        else height * 0.5 - text_height / 2
        if overlay.position == "center"
        else height * 0.9 - text_height
    )
    draw.multiline_text(
        (width / 2, y - bbox[1]),
        text,
        font=font,
        fill=overlay.color,
        anchor="ma",
        align="center",
        spacing=6,
        stroke_width=2,
        stroke_fill="#000000",
    )
    return image


def validate_text(overlay: VideoOverlay, width: int, height: int) -> None:
    """Check the exact export layout before spending time rendering shots."""
    with _text_image(overlay, width, height):
        pass


def render_text(overlay: VideoOverlay, width: int, height: int, output: Path) -> None:
    with _text_image(overlay, width, height) as image:
        image.save(output, "PNG")
