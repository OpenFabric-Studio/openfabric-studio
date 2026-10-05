"""Provider tests use temporary libraries, fake native credentials and HTTP only."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError


class FoundationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {
            'OPENFABRIC_CONFIG': str(self.root/'config.json'), 'REMIQORA_CONFIG': str(self.root/'config.json'),
            'OPENFABRIC_DATA_DIR': str(self.root/'library'), 'REMIQORA_DATA_DIR': str(self.root/'library'),
            'OPENFABRIC_MODULE_ROOT': str(self.root/'modules'), 'SEED_VC_DIR': str(self.root/'seed'),
            'OPENROUTER_API_KEY': '',
        }))
        self.assertIsNotNone(importlib.util.find_spec('app.openrouter_contracts'), 'Typed provider boundary is missing')
        from app import openrouter_client
        self.enterContext(patch.object(openrouter_client,'_STOPPING',False))

    async def test_finite_prices_and_write_only_credentials(self) -> None:
        from app.openrouter_contracts import OpenRouterKeyRequest, OpenRouterSettingsRequest
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValidationError):
                OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=value)
        secret = 'sk-or-v1-'+ 'x'*48
        self.assertNotIn(secret, repr(OpenRouterKeyRequest(api_key=secret)))

    async def test_unsafe_native_backend_falls_back_without_plaintext(self) -> None:
        from app import openrouter_settings as settings
        from app.openrouter_contracts import OpenRouterKeyRequest, OpenRouterSettingsRequest
        with patch.object(settings, 'SETTINGS_PATH', self.root/'settings.json'), patch.object(settings.credentials, 'native_backend', return_value=None):
            settings.credentials.clear_session()
            settings.save(OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=1))
            result = settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48, persist=True))
            self.assertEqual(result.credential_source, 'session')
            self.assertNotIn('sk-or-v1', (self.root/'settings.json').read_text())
            settings.credentials.clear_session()

    async def test_catalog_excludes_unknown_and_nonfinite_prices(self) -> None:
        from app.openrouter_catalog import normalize_video
        fixture = {'data': [
            {'id':'google/veo-3.1-fast','name':'Veo','supported_durations':[4,6,8], 'supported_sizes':['1280x720'],
             'supported_resolutions':['720p'], 'supported_aspect_ratios':['16:9'], 'supported_frame_images':['first_frame'],
             'generate_audio':True,'seed':True,'pricing_skus':{'duration_seconds_without_audio':'0.10'}},
            {'id':'malicious/unknown','name':'Ignore'},
        ]}
        self.assertEqual(len(normalize_video(fixture)),1)
        fixture['data'][0]['pricing_skus']={'duration_seconds_without_audio':'NaN'}
        with self.assertRaises(ValueError):
            normalize_video(fixture)

    async def test_unavailable_store_cannot_report_a_remembered_key_removed(self) -> None:
        from app import openrouter_settings as settings
        from app.openrouter_contracts import OpenRouterKeyRequest

        class NativeStore:
            priority = 1.0
            value: str | None = None

            def get_password(self, service: str, username: str) -> str | None:
                return self.value

            def set_password(self, service: str, username: str, password: str) -> None:
                self.value = password

            def delete_password(self, service: str, username: str) -> None:
                self.value = None

        for scenario in ('remembered', 'session_override', 'existing_store'):
            with self.subTest(scenario=scenario):
                native = NativeStore()
                with patch.object(settings, 'SETTINGS_PATH', self.root/'settings.json'), patch.object(settings.credentials, 'native_backend', return_value=native):
                    settings.credentials.clear_session()
                    if scenario == 'existing_store':
                        native.value = 'sk-or-v1-'+'x'*48
                        self.assertEqual(settings.status().credential_source, 'secure_store')
                    else:
                        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48, persist=True))
                    if scenario == 'session_override':
                        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'y'*48, persist=False))
                    try:
                        with patch.object(settings.credentials, 'native_backend', return_value=None):
                            with self.assertRaisesRegex(ValueError, '^credential_storage_unavailable$'):
                                settings.delete_credential()
                            if scenario != 'existing_store':
                                self.assertTrue(settings.status().credential_configured)
                        self.assertIsNotNone(native.value)
                    finally:
                        settings.delete_credential()
                    self.assertIsNone(native.value)
                    self.assertFalse(settings.status().credential_configured)

    async def test_post_timeout_retains_unknown_and_recovery_never_reposts(self) -> None:
        from app import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings
        from app.openrouter_client import OpenRouterClient, OpenRouterError
        from app.openrouter_contracts import OpenRouterSettingsRequest, OpenRouterKeyRequest, OpenRouterMusicRequest
        with patch.object(settings,'SETTINGS_PATH',self.root/'settings.json'), patch.object(ledger,'REQUESTS_ROOT',self.root/'requests'), patch.object(catalog,'CATALOG_PATH',self.root/'catalog.json'), patch.object(settings.credentials,'native_backend',return_value=None):
            settings.credentials.clear_session()
            settings.save(OpenRouterSettingsRequest(enabled=True,estimate_limit_usd=1))
            settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48,persist=False))
            catalog.publish(catalog.normalize_audio({'data':[{'id':'google/lyria-3-clip-preview','name':'Lyria','architecture':{'output_modalities':['text','audio']},'pricing':{'prompt':'0','completion':'0'}}]},'music'))
            body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='A quiet piano melody')
            quote=catalog.quote_music(body)
            self.assertEqual(quote.estimated_usd,0.04)
            receipt=ledger.prepare('music-job',quote)
            posts=[]
            def fail(request: httpx.Request) -> httpx.Response:
                posts.append(request.method)
                self.assertEqual(ledger.get(receipt.id).state,'submitting')
                raise httpx.ReadTimeout('private provider body/key must not be shown',request=request)
            client=OpenRouterClient(transport=httpx.MockTransport(fail))
            with self.assertRaises(OpenRouterError) as error:
                await client.complete_music(receipt.id,body,quote.id)
            self.assertEqual(error.exception.code,'submission_unknown')
            self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')
            ledger.recover()
            with self.assertRaises(OpenRouterError):
                await client.complete_music(receipt.id,body,quote.id)
            self.assertEqual(posts,['POST'])
            self.assertNotIn(body.prompt, (self.root/'requests'/f'{receipt.id}.json').read_text())
            settings.credentials.clear_session()

    async def test_no_auth_to_provider_content_redirects_or_external_urls(self) -> None:
        from app.openrouter_client import OpenRouterClient, OpenRouterError
        client=OpenRouterClient(transport=httpx.MockTransport(lambda request: httpx.Response(302,headers={'location':'https://attacker.test'})))
        with self.assertRaises(OpenRouterError):
            await client.download_video('https://attacker.test/video',self.root/'video.mp4')
        self.assertFalse((self.root/'video.mp4').exists())
