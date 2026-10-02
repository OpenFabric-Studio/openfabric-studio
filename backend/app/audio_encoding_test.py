"""Explicit export profiles and isolated settings persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import audio_encoding as encoding
from app import db


class AudioEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, "FILES_DIR", self.directory / "files"))

    def test_defaults_and_saved_profiles_survive_reload(self) -> None:
        defaults = encoding.load_settings()
        self.assertEqual(defaults.mp3.bitrate_kbps, 320)
        self.assertEqual(defaults.wav.bit_depth, 24)
        self.assertEqual(defaults.flac.compression_level, 5)
        changed = defaults.model_copy(
            update={"wav": defaults.wav.model_copy(update={"bit_depth": 32})}
        )
        result = encoding.save_settings(changed)
        self.assertEqual(result.settings.wav.bit_depth, 32)
        self.assertEqual(result.defaults.wav.bit_depth, 24)
        self.assertEqual(encoding.load_settings(), changed)

    def test_invalid_settings_never_replace_last_good_document(self) -> None:
        encoding.save_settings(encoding.AudioEncodingSettings())
        path = encoding.settings_path()
        before = path.read_bytes()
        data = encoding.AudioEncodingSettings().model_dump()
        data["mp3"]["bitrate_kbps"] = 321
        with self.assertRaises(ValidationError):
            encoding.AudioEncodingSettings.model_validate(data)
        self.assertEqual(path.read_bytes(), before)
        with (
            patch("app.audio_encoding.write_object", side_effect=OSError("disk full")),
            self.assertRaises(encoding.AudioEncodingError) as caught,
        ):
            encoding.save_settings(encoding.AudioEncodingSettings())
        self.assertEqual(caught.exception.code, "settings_write_failed")
        self.assertEqual(path.read_bytes(), before)

    def test_explicit_cbr_vbr_and_lossless_encoders(self) -> None:
        settings = encoding.AudioEncodingSettings()
        self.assertEqual(
            encoding.encoding_args("mp3", settings),
            [
                "-c:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-f",
                "mp3",
            ],
        )
        vbr = settings.model_copy(
            update={
                "mp3": settings.mp3.model_copy(update={"mode": "vbr", "vbr_quality": 0})
            }
        )
        self.assertEqual(
            encoding.encoding_args("mp3", vbr)[:4], ["-c:a", "libmp3lame", "-q:a", "0"]
        )
        float_wav = settings.model_copy(
            update={"wav": settings.wav.model_copy(update={"bit_depth": 32})}
        )
        self.assertIn("pcm_f32le", encoding.encoding_args("wav", float_wav))
        self.assertEqual(
            encoding.encoding_args("flac", settings),
            [
                "-c:a",
                "flac",
                "-sample_fmt",
                "s32",
                "-bits_per_raw_sample",
                "24",
                "-compression_level",
                "5",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-f",
                "flac",
            ],
        )

    def test_missing_defaults_are_read_only_and_corruption_is_honest(self) -> None:
        encoding.load_settings()
        self.assertFalse(self.directory.joinpath("files").exists())
        path = encoding.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        with self.assertRaises(encoding.AudioEncodingError) as caught:
            encoding.load_settings()
        self.assertEqual(caught.exception.code, "settings_invalid")

    def test_numeric_literals_reject_boolean_and_string_inputs(self) -> None:
        for value in [True, "1"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                encoding.Mp3EncodingSettings.model_validate({"channels": value})
