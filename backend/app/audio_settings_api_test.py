"""Wire validation and stable errors for global encoding profiles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import db
from app.api import routes_audio_settings


class AudioSettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_put_and_invalid_request_preserve_profiles(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(db, "FILES_DIR", Path(directory) / "files"),
        ):
            app = FastAPI()
            app.include_router(routes_audio_settings.router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                initial = await client.get("/api/settings/audio")
                self.assertEqual(initial.status_code, 200)
                data = initial.json()["settings"]
                data["wav"]["bit_depth"] = 32
                saved = await client.put("/api/settings/audio", json=data)
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["settings"]["wav"]["bit_depth"], 32)
                self.assertEqual(saved.json()["defaults"]["wav"]["bit_depth"], 24)
                data["flac"]["compression_level"] = 9
                invalid = await client.put("/api/settings/audio", json=data)
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(
                    (await client.get("/api/settings/audio")).json()["settings"][
                        "flac"
                    ]["compression_level"],
                    5,
                )

    async def test_storage_failure_never_exposes_internal_error(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(db, "FILES_DIR", Path(directory) / "files"),
        ):
            app = FastAPI()
            app.include_router(routes_audio_settings.router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                settings = (await client.get("/api/settings/audio")).json()["settings"]
                with patch(
                    "app.audio_encoding.write_object",
                    side_effect=OSError("/private/secret"),
                ):
                    response = await client.put("/api/settings/audio", json=settings)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "settings_write_failed"})
