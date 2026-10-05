"""Rasterize bounded, timed title/lyric overlays; text never enters ffmpeg syntax."""

from __future__ import annotations
import hashlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .video_contracts import VideoOverlay
from .video_projects import VideoProjectError


FONT_PATH = Path(__file__).resolve().parents[1] / "assets/fonts/NotoSans.ttf"
CJK_FONT_PATH = FONT_PATH.with_name("NotoSansCJKsc-Regular.otf")
CJK_FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
FONT_SHA256 = "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d"


def font_identity() -> str:
    # Both fonts affect export layout; verify packaged bytes, not OS discovery.
    for path, identity in ((FONT_PATH, FONT_SHA256), (CJK_FONT_PATH, CJK_FONT_SHA256)):
        try:
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != identity:
                raise VideoProjectError("caption_font_unavailable")
        except OSError as exc:
            raise VideoProjectError("caption_font_unavailable") from exc
    return FONT_SHA256 + ":" + CJK_FONT_SHA256


def _font(size: int, text: str = "") -> ImageFont.FreeTypeFont:
    font_identity()
    # A single pinned pan-CJK face covers Han, kana and Hangul. Its SC glyph
    # conventions are explicit; no locale-dependent operating-system fallback.
    cjk = any(0x2E80 <= ord(char) <= 0xA4CF or 0xAC00 <= ord(char) <= 0xD7AF
              or 0xF900 <= ord(char) <= 0xFAFF or 0x20000 <= ord(char) <= 0x3134F for char in text)
    try:
        return ImageFont.truetype(str(CJK_FONT_PATH if cjk else FONT_PATH), size)
    except OSError as exc:
        raise VideoProjectError("caption_font_unavailable") from exc


def _text_image(overlay: VideoOverlay, width: int, height: int) -> Image.Image:
    font = _font(overlay.font_size, overlay.text)
    missing = font.getmask("\U0010ffff")
    for char in set(overlay.text):
        if not char.isspace():
            glyph = font.getmask(char)
            if glyph.size == missing.size and bytes(glyph) == bytes(missing):
                raise VideoProjectError("caption_glyph_unavailable")
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
