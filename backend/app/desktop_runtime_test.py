"""A launcher must identify its backend, rather than any process on the port."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.desktop_runtime import router


class DesktopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_identifies_configured_launch_and_process(self) -> None:
        app = FastAPI()
        app.include_router(router)
        nonce = "ab" * 32
        with patch.dict(os.environ, {"OPENFABRIC_DESKTOP_STARTUP_NONCE": nonce}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/desktop/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"service": "openfabric-studio", "nonce": nonce, "pid": os.getpid()})
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_unconfigured_or_malformed_launch_is_not_ready(self) -> None:
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for nonce in ("", "private/path", "AB" * 32, "ab" * 31, "ab" * 33):
                with self.subTest(nonce=nonce), patch.dict(os.environ, {"OPENFABRIC_DESKTOP_STARTUP_NONCE": nonce}):
                    response = await client.get("/api/desktop/ready")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "not_found"})
