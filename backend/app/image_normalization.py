"""Bounded EXIF/ICC raster normalization, executed in an owned child process."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

MAX_PIXELS = 16_777_216
MAX_BYTES = 20 * 1024 * 1024


class ImageNormalizationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def normalization_argv(source: Path, output: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), str(source), str(output)]


def normalize_image(source: Path, output: Path) -> None:
    """Normalize one PNG/JPEG/WebP; never modify the original or infer identity."""
    temporary: Path | None = None
    receipt_temp: Path | None = None
    if source.is_symlink() or output.is_symlink():
        raise ImageNormalizationError("invalid_reference")
    if source.stat().st_size > MAX_BYTES:
        raise ImageNormalizationError("reference_too_large")
    try:
        with Image.open(source) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                raise ImageNormalizationError("invalid_reference")
            original_width, original_height = image.size
            if (not 1 <= original_width <= 8192 or not 1 <= original_height <= 8192
                    or original_width * original_height > MAX_PIXELS):
                raise ImageNormalizationError("reference_too_large")
            image.load()
            normalized = ImageOps.exif_transpose(image)
            alpha = normalized.convert("RGBA").getchannel("A") if "A" in normalized.getbands() or "transparency" in normalized.info else None
            profile: object = image.info.get("icc_profile")
            tagged = profile is not None
            if tagged:
                if not isinstance(profile, bytes) or len(profile) > 1024 * 1024:
                    raise ImageNormalizationError("invalid_reference")
                try:
                    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                    if normalized.mode in {'P', 'PA', '1'}:
                        normalized = normalized.convert('RGB')
                    converted = ImageCms.profileToProfile(normalized, source_profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
                    if converted is None:
                        raise ImageNormalizationError("invalid_reference")
                    normalized = converted
                except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                    raise ImageNormalizationError("invalid_reference") from exc
            else:
                normalized = normalized.convert("RGB")
            if alpha is not None:
                normalized.putalpha(alpha)
            normalized.info.clear()
            with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".png", delete=False) as stream:
                temporary = Path(stream.name)
                normalized.save(stream, format="PNG")
                stream.flush()
                os.fsync(stream.fileno())
            receipt = {
                "policy": "exif_transpose_icc_srgb_v1",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "normalized_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
                "source_width": original_width, "source_height": original_height,
                "width": normalized.width, "height": normalized.height,
                "colour": "converted_to_srgb" if tagged else "untagged_assumed_srgb",
            }
            with tempfile.NamedTemporaryFile("w", dir=output.parent, delete=False, encoding="utf-8") as stream:
                receipt_temp = Path(stream.name)
                json.dump(receipt, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            # The parent publishes the enclosing object only after both exist.
            temporary.replace(output)
            receipt_temp.replace(output.with_suffix(".normalization.json"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageNormalizationError("reference_too_large") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageNormalizationError("invalid_reference") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if receipt_temp is not None:
            receipt_temp.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    try:
        normalize_image(Path(sys.argv[1]), Path(sys.argv[2]))
    except ImageNormalizationError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2
    except Exception:
        # Never return a filesystem path, image metadata or raw decoder error.
        print(json.dumps({"error_code": "invalid_reference"}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
