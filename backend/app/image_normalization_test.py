"""Real bounded raster normalization, without models or a user library."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms


class ImageNormalizationTest(unittest.TestCase):
    def test_palette_srgb_profile_and_transparency_are_supported(self) -> None:
        from app.image_normalization import normalize_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new('P', (4, 4))
            image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
            image.putpixel((0, 0), 1)
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            image.save(root / 'palette.png', transparency=0, icc_profile=profile)
            normalize_image(root / 'palette.png', root / 'output.png')
            with Image.open(root / 'output.png') as output:
                self.assertEqual(output.mode, 'RGBA')
                self.assertEqual(output.getpixel((0, 0)), (0, 0, 255, 255))
                self.assertEqual(output.getpixel((1, 0)), (255, 0, 0, 0))

    def test_orientation_is_applied_once_and_source_bytes_are_retained(self) -> None:
        from app.image_normalization import normalize_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "input.jpg", root / "normalized.png"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (32, 16), "red").save(source, exif=exif)
            original = source.read_bytes()
            normalize_image(source, target)
            with Image.open(target) as result:
                self.assertEqual(result.size, (16, 32))
                self.assertEqual(result.getexif().get(274), None)
            self.assertEqual(source.read_bytes(), original)
            receipt = json.loads(target.with_suffix(".normalization.json").read_text())
            self.assertEqual(receipt["source_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(receipt["width"], 16)

    def test_mirrored_orientation_and_tagged_srgb_are_normalized(self) -> None:
        from app.image_normalization import normalize_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGB", (2, 1))
            image.putpixel((0, 0), (255, 0, 0))
            image.putpixel((1, 0), (0, 0, 255))
            exif = Image.Exif()
            exif[274] = 2
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            image.save(root / "input.png", exif=exif, icc_profile=profile)
            normalize_image(root / "input.png", root / "output.png")
            with Image.open(root / "output.png") as output:
                self.assertEqual(output.getpixel((0, 0)), (0, 0, 255))
                self.assertEqual(output.getpixel((1, 0)), (255, 0, 0))

    def test_bad_colour_profile_fails_without_publishing(self) -> None:
        from app.image_normalization import ImageNormalizationError, normalize_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (4, 4)).save(root / "input.png", icc_profile=b"private invalid profile")
            with self.assertRaises(ImageNormalizationError) as caught:
                normalize_image(root / "input.png", root / "output.png")
            self.assertEqual(caught.exception.code, "invalid_reference")
            self.assertFalse((root / "output.png").exists())

    def test_oversized_dimensions_and_other_formats_are_refused(self) -> None:
        from app.image_normalization import ImageNormalizationError, normalize_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for size, image_format, code in [((8193, 1), "PNG", "reference_too_large"), ((4, 4), "GIF", "invalid_reference")]:
                Image.new("RGB", size).save(root / "input.bin", format=image_format)
                with self.assertRaises(ImageNormalizationError) as caught:
                    normalize_image(root / "input.bin", root / "output.png")
                self.assertEqual(caught.exception.code, code)
                self.assertFalse((root / "output.png").exists())


if __name__ == "__main__":
    unittest.main()
