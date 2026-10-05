"""Provider setup is local-origin protected and returns no credential material."""
from __future__ import annotations
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI

class OpenRouterApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ,{'OPENFABRIC_CONFIG':str(self.root/'config'),'REMIQORA_CONFIG':str(self.root/'config'),
            'OPENFABRIC_DATA_DIR':str(self.root/'library'),'REMIQORA_DATA_DIR':str(self.root/'library'),'OPENFABRIC_MODULE_ROOT':str(self.root/'modules'),'SEED_VC_DIR':str(self.root/'seed'),'OPENROUTER_API_KEY':''}))
        self.assertIsNotNone(importlib.util.find_spec('app.api.routes_openrouter'),'Provider origin boundary is missing')
        from app.api.routes_openrouter import router
        from app import openrouter_settings as settings
        self.enterContext(patch.object(settings,'SETTINGS_PATH',self.root/'settings.json'))
        self.enterContext(patch.object(settings.credentials,'native_backend',return_value=None))
        settings.credentials.clear_session()
        app=FastAPI(); app.include_router(router)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:9000')

    async def asyncTearDown(self) -> None:
        if hasattr(self,'client'): await self.client.aclose()
        from app import openrouter_credentials
        openrouter_credentials.clear_session()

    async def test_foreign_origin_cannot_set_or_erase_credentials(self) -> None:
        response=await self.client.post('/api/openrouter/credential',json={'api_key':'sk-or-v1-'+'x'*48,'persist':False},headers={'Origin':'https://attacker.test'})
        self.assertEqual(response.status_code,403)
        response=await self.client.delete('/api/openrouter/credential',headers={'Origin':'https://attacker.test'})
        self.assertEqual(response.status_code,403)

    async def test_key_write_returns_status_only_and_get_is_secret_free(self) -> None:
        key='sk-or-v1-'+'x'*48
        response=await self.client.post('/api/openrouter/credential',json={'api_key':key,'persist':False},headers={'Origin':'http://127.0.0.1:9000'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(key,response.text)
        response=await self.client.get('/api/openrouter/settings')
        self.assertTrue(response.json()['credential_configured'])
        self.assertNotIn('api_key',response.text)

    async def test_rejected_key_never_echoes_untrusted_secret(self) -> None:
        secret='private secret with spaces'
        response=await self.client.post('/api/openrouter/credential',json={'api_key':secret,'persist':False})
        self.assertEqual(response.status_code,422)
        self.assertNotIn(secret,response.text)
