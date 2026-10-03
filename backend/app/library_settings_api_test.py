"""Library inventory must survive the app's response validation boundary."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.api import routes_settings
from app.client_contracts import LibraryStatusResponse


class LibrarySettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_library_inventory_includes_saved_speech_and_audiobooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            with (
                patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(config)}),
                patch.object(routes_settings, "DATA_DIR", root / "library"),
                patch.object(routes_settings, "SEED_VC_DIR", root / "absent-engine"),
            ):
                app = FastAPI()
                app.include_router(routes_settings.router)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/api/settings/library")
            self.assertEqual(response.status_code, 200)
            status = LibraryStatusResponse.model_validate(response.json())
            self.assertEqual(
                [folder.key for folder in status.folders],
                ["tracks", "voices", "videos", "audiobooks", "speechProfiles",
                 "speechTrials", "models", "logs", "database"],
            )
            self.assertFalse((root / "absent-engine").exists())
