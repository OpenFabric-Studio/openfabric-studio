"""Artist defaults stay bounded and migrate with the library files subtree."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import artist_settings, db


class ArtistSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, "FILES_DIR", self.root / "files"))

    def test_artist_normalizes_and_survives_readback(self) -> None:
        self.assertEqual(artist_settings.get_settings().artist, "")
        self.assertFalse((self.root / "files").exists())
        result = artist_settings.save_settings(artist_settings.ArtistSettings(artist="  Björk\n  東京  "))
        self.assertEqual(result.artist, "Björk 東京")
        self.assertEqual(artist_settings.get_settings(), result)

    def test_malformed_or_oversized_values_do_not_replace_saved_artist(self) -> None:
        artist_settings.save_settings(artist_settings.ArtistSettings(artist="Saved"))
        path = artist_settings.settings_path()
        before = path.read_bytes()
        for value in ["x" * 121, 7, None, "a\x00b"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                artist_settings.ArtistSettings.model_validate({"artist": value})
        with self.assertRaises(ValidationError):
            artist_settings.ArtistSettings.model_validate({})
        self.assertEqual(path.read_bytes(), before)
        with patch("app.artist_settings.write_object", side_effect=OSError("private disk detail")):
            with self.assertRaises(artist_settings.ArtistSettingsError) as caught:
                artist_settings.save_settings(artist_settings.ArtistSettings(artist="New"))
        self.assertEqual(caught.exception.code, "artist_settings_write_failed")
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_settings_and_symlink_escape_are_rejected(self) -> None:
        path = artist_settings.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text('{broken')
        with self.assertRaises(artist_settings.ArtistSettingsError) as caught:
            artist_settings.get_settings()
        self.assertEqual(caught.exception.code, "artist_settings_invalid")
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(artist_settings.ArtistSettingsError) as escaped:
            artist_settings.get_settings()
        self.assertEqual(escaped.exception.code, "artist_settings_invalid")
