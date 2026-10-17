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
                patch.dict(os.environ, {'OPENFABRIC_CONFIG': str(config), 'OPENFABRIC_DATA_DIR': str(root / 'library'),
                                       'SEED_VC_DIR': str(root / 'absent-engine'), 'OPENFABRIC_MODULE_ROOT': str(root / 'modules')}),
                patch.object(routes_settings, "DATA_DIR", root / "library"),
                patch.object(routes_settings, "SEED_VC_DIR", root / "absent-engine"),
            ):
                app = FastAPI()
                app.include_router(routes_settings.router)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as client:
                    response = await client.get("/api/settings/library")
            self.assertEqual(response.status_code, 200)
            status = LibraryStatusResponse.model_validate(response.json())
            self.assertEqual(
                [folder.key for folder in status.folders],
                ["tracks", "voices", "videos", "videoProjects", "videoCharacters", "videoCharacterTraining", "videoCharacterTrainer", "providers", "audiobooks", "speechProfiles",
                 "speechTrials", "localMusic", "musicAdapters", "musicTraining", "musicTrainingRuns", "musicAnalysis", "musicArtifacts", "models", "logs", "database"],
            )
            self.assertFalse((root / "absent-engine").exists())

    async def _damaged_config(self, *, unreadable: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / 'config.json'
            payload = b'{"active_data_dir":"/private/recovery-pointer"}' if unreadable else b'{"active_data_dir":"/private/recovery-pointer"'
            config.write_bytes(payload)
            read_text = Path.read_text
            def read(path: Path, *args: object, **kwargs: object) -> str:
                if path == config and unreadable:
                    raise PermissionError('private internal read error')
                return read_text(path)
            with patch.dict(os.environ, {'OPENFABRIC_CONFIG': str(config), 'OPENFABRIC_DATA_DIR': str(root / 'library'),
                                        'SEED_VC_DIR': str(root / 'engine'), 'OPENFABRIC_MODULE_ROOT': str(root / 'modules')}), \
                 patch.object(routes_settings, 'DATA_DIR', root / 'library'), \
                 patch.object(routes_settings, 'SEED_VC_DIR', root / 'engine'), \
                 patch.object(Path, 'read_text', autospec=True, side_effect=read):
                app = FastAPI()
                app.include_router(routes_settings.router)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://127.0.0.1') as client:
                    for method in ('GET', 'PUT'):
                        response = await client.request(method, '/api/settings/library', json={'data_dir': str(root / 'new')} if method == 'PUT' else None)
                        self.assertEqual(response.status_code, 503, response.text)
                        self.assertEqual(response.json(), {'detail': 'config_unreadable' if unreadable else 'invalid_config'})
            self.assertEqual(config.read_bytes(), payload)
            self.assertFalse((root / 'new').exists())
            self.assertFalse((root / 'library').exists())

    async def test_malformed_config_is_preserved_by_library_get_and_put(self) -> None:
        await self._damaged_config(unreadable=False)

    async def test_unreadable_config_returns_stable_error_without_writing_defaults(self) -> None:
        await self._damaged_config(unreadable=True)
