from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.module_catalog import ModuleEnvironment
from app.module_runtime import RuntimeTarget, runtime_status, speaker_status


class ModuleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_unverified_speaker_weights_are_never_labelled_verified(self) -> None:
        from app import speaker_review
        from app.speaker_review_contracts import SpeakerReviewCapability
        with tempfile.TemporaryDirectory() as root, patch.object(speaker_review, 'capability', return_value=SpeakerReviewCapability(available=False, deps_available=True, weights_available=True, reason='speaker_weights_unverified')):
            status = speaker_status(ModuleEnvironment.for_root(Path(root)))
        self.assertFalse(status.ready)
        self.assertFalse(next(item for item in status.evidence if item.code == 'speaker_weights').verified)

    async def test_ace_requires_service_identity_and_initialized_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = ModuleEnvironment.for_root(Path(temporary))
            target = RuntimeTarget('ace_step', 'http://127.0.0.1:8001')
            health: dict[str, object] = {'status': 'ok', 'service': 'ACE-Step API', 'version': '1.0', 'models_initialized': False, 'llm_initialized': True, 'loaded_model': 'turbo', 'loaded_lm_model': 'lm'}
            def response(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={'code': 200, 'data': health})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                state = await runtime_status(env, target, client=client)
                self.assertFalse(state.ready)
                health['models_initialized'] = True
                state = await runtime_status(env, target, client=client)
                self.assertTrue(state.ready)
                health['service'] = 'some other HTTP service'
                self.assertFalse((await runtime_status(env, target, client=client)).ready)

    async def test_native_health_alone_does_not_establish_loaded_generation_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = ModuleEnvironment.for_root(Path(temporary))
            models = [{'id': 'yue2', 'loaded': False}]
            def response(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={'status': 'ok', 'backend': 'metal'} if request.url.path == '/health' else {'data': models})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                target = RuntimeTarget('yue2', 'http://127.0.0.1:9001')
                self.assertFalse((await runtime_status(env, target, client=client)).ready)
                models[0]['loaded'] = True
                self.assertTrue((await runtime_status(env, target, client=client)).ready)

    async def test_speech_needs_expected_api_and_real_capability_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = ModuleEnvironment.for_root(Path(temporary))
            api = {'paths': {'/': {'get': {'parameters': [{'name': name} for name in ('refer_wav_path', 'prompt_text', 'prompt_language', 'text', 'text_language')]}, 'post': {}}, '/set_model': {}, '/change_refer': {}}}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api))) as client:
                target = RuntimeTarget('speech', 'http://127.0.0.1:9880')
                self.assertFalse((await runtime_status(env, target, client=client)).ready)
                with patch('app.module_runtime.capability_verified', return_value=True):
                    self.assertTrue((await runtime_status(env, target, client=client)).ready)
                with patch('app.module_runtime.capability_verified', return_value=True):
                    target = RuntimeTarget('speech', 'https://public.example')
                    self.assertFalse((await runtime_status(env, target, client=client)).ready)
