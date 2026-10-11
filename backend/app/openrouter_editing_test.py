"""Synthetic editing jobs exercise quote ownership and one paid submission."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from pydantic import TypeAdapter, ValidationError
from app.clip_host_contracts import HostedClip

from app import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings, openrouter_client as client_module, openrouter_credentials as credentials
from app.contracts import JsonObject, JsonValue
from app.openrouter_client import OpenRouterClient
from app.openrouter_errors import OpenRouterError
from app.openrouter_contracts import OpenRouterKeyRequest, OpenRouterSettingsRequest, OpenRouterQuoteRequest, OpenRouterVideoRequest

IMAGE_MODEL = 'bytedance-seed/seedream-4.5'
VIDEO_MODEL = 'runway/aleph-2'
FRAME = 'data:image/png;base64,'+base64.b64encode(b'source-frame').decode()
CHARACTER = 'data:image/png;base64,'+base64.b64encode(b'character').decode()
VIDEO = 'data:video/mp4;base64,'+base64.b64encode(b'prepared-video').decode()


def image_metadata(rate: float = 0.04, refs: int = 14, provider: str = 'seed') -> JsonObject:
    return {'id': IMAGE_MODEL, 'endpoints': [{'provider_slug': provider,
        'supported_parameters': {'resolution': {'type': 'enum', 'values': ['1K', '2K', '4K']},
            'aspect_ratio': {'type': 'enum', 'values': ['16:9', '9:16', '1:1']},
            'n': {'type': 'range', 'min': 1, 'max': 10},
            'input_references': {'type': 'range', 'min': 0, 'max': refs}, 'seed': {'type': 'boolean'}},
        'pricing': [{'billable': 'output_image', 'unit': 'image', 'cost_usd': rate},
            {'billable': 'input_image', 'unit': 'image', 'cost_usd': 0}]}]}


def video_metadata(rate: str = '28', minimum: str = '56', ratios: list[str] | None = None) -> JsonObject:
    aspect_ratios: list[JsonValue] = list(ratios) if ratios is not None else ['16:9', '9:16', '1:1']
    return {'data': [{'id': VIDEO_MODEL, 'name': 'Runway Aleph 2',
        'supported_durations': None, 'supported_sizes': None, 'supported_resolutions': None,
        'supported_aspect_ratios': aspect_ratios, 'supported_frame_images': None,
        'generate_audio': False, 'seed': True, 'allowed_passthrough_parameters': ['keyframes', 'contentModeration'],
        'pricing_skus': {'cents_per_second_output': rate, 'minimum_cents_per_generation': minimum}}]}


def raster() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


class EditingTests(unittest.IsolatedAsyncioTestCase):
    def hosted(self, receipt_id: str) -> HostedClip:
        return HostedClip(receipt_id=receipt_id,
            source_sha256=hashlib.sha256(base64.b64decode(VIDEO.partition(',')[2])).hexdigest(),
            url='https://approved-clip.trycloudflare.com/clip/'+'a'*64+'.mp4',
            expires_at=datetime.now(timezone.utc)+timedelta(hours=24))

    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'OPENFABRIC_CONFIG': str(self.root/'config.json'),
            'OPENFABRIC_DATA_DIR': str(self.root/'library'), 'OPENFABRIC_MODULE_ROOT': str(self.root/'modules'),
            'SEED_VC_DIR': str(self.root/'seed'), 'OPENROUTER_API_KEY': ''}))
        self.enterContext(patch.object(catalog, 'CATALOG_PATH', self.root/'catalog.json'))
        self.enterContext(patch.object(catalog, '_QUOTES', {}))
        self.enterContext(patch.object(ledger, 'REQUESTS_ROOT', self.root/'requests'))
        self.enterContext(patch.object(settings, 'SETTINGS_PATH', self.root/'settings.json'))
        self.enterContext(patch.object(credentials, 'native_backend', return_value=None))
        self.enterContext(patch.object(client_module, '_STOPPING', False))
        credentials.clear_session()
        settings.save(OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=5))
        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48, persist=False))
        self.assertTrue(hasattr(catalog, 'normalize_image_edit'), 'image editor catalog not implemented')
        catalog.publish([catalog.normalize_image_edit(image_metadata()), *catalog.normalize_video(video_metadata())])

    async def asyncTearDown(self) -> None:
        credentials.clear_session()

    async def test_quotes_bind_workflow_both_references_and_live_prices(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest, OpenRouterVideoEditRequest
        image = OpenRouterImageEditRequest(prompt='Replace the person', source_frame=FRAME,
            reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        image_quote = catalog.quote_image_edit(image)
        self.assertEqual(image_quote.estimated_usd, 0.04)
        self.assertEqual(image_quote.transfers, ['prompt', 'source_frame', 'reference_image'])
        changed = image.model_copy(update={'source_frame': CHARACTER})
        with self.assertRaises(OpenRouterError):
            catalog.validate_quote(image_quote.id, catalog.image_parameters(changed))
        video = OpenRouterVideoEditRequest(prompt='Preserve the scene', reference_video=VIDEO,
            reference_image=FRAME, duration=2, width=854, height=480, seed=7)
        quote = catalog.quote_video_edit(video)
        self.assertAlmostEqual(quote.estimated_usd, 0.56)
        self.assertEqual(quote.transfers, ['prompt', 'reference_video', 'reference_image'])
        parameters = catalog.video_edit_parameters(video)
        self.assertEqual(parameters.workflow, 'video_edit')
        with self.assertRaises(OpenRouterError):
            catalog.validate_quote(quote.id, parameters.model_copy(update={'workflow': 'generate'}))
        catalog.publish([catalog.normalize_image_edit(image_metadata(0.05)), *catalog.normalize_video(video_metadata())])
        with self.assertRaises(OpenRouterError):
            catalog.validate_quote(image_quote.id, catalog.image_parameters(image))

    async def test_edit_models_cannot_enter_storyboard_generation(self) -> None:
        aleph = catalog.require_model(VIDEO_MODEL, 'video')
        self.assertTrue(aleph.supports_video_editing)
        self.assertFalse(aleph.supports_image_editing)
        with self.assertRaises(OpenRouterError):
            catalog.quote_video(OpenRouterVideoRequest(model=VIDEO_MODEL, prompt='Scene', duration=4, size='854x480'))
        with self.assertRaises(OpenRouterError):
            catalog.quote(OpenRouterQuoteRequest(kind='image', model_id=IMAGE_MODEL, text='Scene'))

    async def test_video_feasibility_estimate_never_admits_paid_submission(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        self.assertTrue(hasattr(catalog, 'estimate_video_edit'), 'video editing feasibility estimate is missing')
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=2, width=854, height=480, seed=7)
        count = len(catalog._QUOTES)
        estimate = catalog.estimate_video_edit(body)
        self.assertAlmostEqual(estimate.estimated_usd, 0.56)
        self.assertEqual(len(catalog._QUOTES), count)
        with self.assertRaises(OpenRouterError):
            catalog.saved_quote(estimate.id)
        with self.assertRaises(OpenRouterError):
            ledger.prepare('preview-is-not-payment', estimate)
        with self.assertRaises(OpenRouterError):
            catalog.validate_quote(estimate.id, catalog.video_edit_parameters(body))
        model = catalog.require_model(VIDEO_MODEL, 'video')
        for changed in (model.model_copy(update={'supported_aspect_ratios': ['1:1']}),
                model.model_copy(update={'supported_durations': [4]}),
                model.model_copy(update={'supports_seed': False})):
            catalog.publish([changed])
            with self.assertRaises(OpenRouterError):
                catalog.estimate_video_edit(body)
        catalog.publish([model])
        settings.save(OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=0.50))
        with self.assertRaises(OpenRouterError) as failure:
            catalog.estimate_video_edit(body)
        self.assertEqual(failure.exception.code, 'estimate_limit_exceeded')
        self.assertEqual(len(catalog._QUOTES), count)

    async def test_catalog_rejects_unknown_pricing_and_insufficient_references(self) -> None:
        for fixture in (image_metadata(float('nan')), image_metadata(refs=1), image_metadata(provider='unpriced-provider')):
            with self.assertRaises((ValueError, ValidationError)):
                catalog.normalize_image_edit(fixture)
        for fixture in (video_metadata('NaN'), video_metadata(minimum='-1')):
            with self.assertRaises((ValueError, ValidationError)):
                catalog.normalize_video(fixture)

    async def test_aleph_catalog_rejects_invalid_numeric_aspect_ratios(self) -> None:
        for ratios in (['0:1'], ['16:0'], ['garbage']):
            with self.assertRaises(ValueError):
                catalog.normalize_video(video_metadata(ratios=ratios))

    async def test_image_decode_limit_is_enforced_before_receipt_completion(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        body = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME, reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        quote = catalog.quote_image_edit(body)
        receipt = ledger.prepare('image-small-limit', quote)
        with patch.object(client_module, '_IMAGE_LIMIT', 4):
            with self.assertRaises(OpenRouterError) as failure:
                await OpenRouterClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
                    json={'data': [{'b64_json': base64.b64encode(raster()).decode()}]}))).submit_image_edit(receipt.id, body, quote.id)
        self.assertEqual(failure.exception.code, 'submission_unknown')
        self.assertEqual(ledger.get(receipt.id).state, 'submission_unknown')

    async def test_image_usage_does_not_coerce_boolean_or_string_money(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        body = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME, reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        for cost in (True, '0.04'):
            quote = catalog.quote_image_edit(body)
            receipt = ledger.prepare('image-invalid-cost', quote)
            with self.assertRaises(OpenRouterError):
                await OpenRouterClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
                    json={'data': [{'b64_json': base64.b64encode(raster()).decode()}], 'usage': {'cost': cost}}))).submit_image_edit(receipt.id, body, quote.id)
            self.assertEqual(ledger.get(receipt.id).state, 'submission_unknown')

    async def test_refresh_uses_public_live_image_metadata_without_authentication(self) -> None:
        calls: list[str] = []
        def serve(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            self.assertNotIn('authorization', request.headers)
            if request.url.path.endswith('/videos/models'):
                return httpx.Response(200, json=video_metadata())
            if request.url.path.endswith('/images/models/'+IMAGE_MODEL+'/endpoints'):
                return httpx.Response(200, json=image_metadata(0.05))
            return httpx.Response(200, json={'data': []})
        result = await OpenRouterClient(transport=httpx.MockTransport(serve)).refresh_catalog()
        self.assertEqual(len(calls), 4)
        self.assertEqual(catalog.require_model(IMAGE_MODEL, 'image').prices[0].rate_usd, 0.05)
        self.assertEqual(len(result.models), 2)

    async def test_unavailable_editor_does_not_disable_existing_catalog_or_reuse_stale_editor(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        valid_video: JsonObject = {'data': [{'id': 'google/veo-3.1-fast', 'name': 'Veo',
            'supported_durations': [4, 6, 8], 'supported_sizes': ['1280x720'],
            'supported_resolutions': ['720p'], 'supported_aspect_ratios': ['16:9'],
            'supported_frame_images': ['first_frame'], 'generate_audio': True, 'seed': True,
            'pricing_skus': {'duration_seconds_without_audio': '0.10'}}]}
        image = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME,
            reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        for image_response in (httpx.Response(404), httpx.Response(200, json={'unexpected': 'schema'})):
            catalog.publish([catalog.normalize_image_edit(image_metadata())])
            def serve(request: httpx.Request) -> httpx.Response:
                self.assertNotIn('authorization', request.headers)
                if request.url.path.endswith('/videos/models'):
                    return httpx.Response(200, json=valid_video)
                if request.url.path.endswith('/images/models/'+IMAGE_MODEL+'/endpoints'):
                    return image_response
                return httpx.Response(200, json={'data': []})
            refreshed = await OpenRouterClient(transport=httpx.MockTransport(serve)).refresh_catalog()
            self.assertEqual([model.id for model in refreshed.models], ['google/veo-3.1-fast'])
            self.assertEqual(catalog.require_model('google/veo-3.1-fast', 'video').name, 'Veo')
            with self.assertRaises(OpenRouterError) as failure:
                catalog.quote_image_edit(image)
            self.assertEqual(failure.exception.code, 'model_unavailable')

    async def test_quote_limit_applies_to_both_edit_stages(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest, OpenRouterVideoEditRequest
        settings.save(OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=0.01))
        image = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME, reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        video = OpenRouterVideoEditRequest(prompt='Edit', reference_video=VIDEO, reference_image=FRAME, duration=2, width=854, height=480)
        for body in (catalog.image_parameters(image), catalog.video_edit_parameters(video)):
            with self.assertRaises(OpenRouterError) as failure:
                catalog.quote(body)
            self.assertEqual(failure.exception.code, 'estimate_limit_exceeded')

    async def test_image_submit_exact_wire_and_receipt_never_store_media(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        body = OpenRouterImageEditRequest(prompt='Replace the person', source_frame=FRAME,
            reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        quote = catalog.quote_image_edit(body)
        receipt = ledger.prepare('image-job', quote)
        image = raster()
        def serve(request: httpx.Request) -> httpx.Response:
            self.assertEqual(ledger.get(receipt.id).state, 'submitting')
            self.assertEqual(str(request.url), 'https://openrouter.ai/api/v1/images')
            self.assertEqual(TypeAdapter(JsonObject).validate_json(request.content), {
                'model': IMAGE_MODEL, 'prompt': body.prompt, 'resolution': '2K', 'n': 1,
                'aspect_ratio': '16:9', 'seed': 7,
                'provider': {'only': ['seed'], 'allow_fallbacks': False},
                'input_references': [{'type': 'image_url', 'image_url': {'url': FRAME}},
                    {'type': 'image_url', 'image_url': {'url': CHARACTER}}]})
            return httpx.Response(200, json={'created': 123, 'data': [{'b64_json': base64.b64encode(image).decode(),
                'media_type': 'image/png'}], 'usage': {'cost': 0.04}})
        result = await OpenRouterClient(transport=httpx.MockTransport(serve)).submit_image_edit(receipt.id, body, quote.id)
        self.assertEqual(result.data, image)
        self.assertEqual(result.media_type, 'image/png')
        self.assertEqual(result.actual_cost_usd, 0.04)
        self.assertEqual(ledger.get(receipt.id).state, 'completed')
        self.assertNotIn(FRAME, (self.root/'requests'/f'{receipt.id}.json').read_text())

    async def test_video_submit_uses_source_video_and_reviewed_keyframe_only(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480, seed=7)
        quote = catalog.quote_video_edit(body)
        receipt = ledger.prepare('video-job', quote)
        def serve(request: httpx.Request) -> httpx.Response:
            self.assertEqual(ledger.get(receipt.id).state, 'submitting')
            self.assertEqual(TypeAdapter(JsonObject).validate_json(request.content), {'model': VIDEO_MODEL,
                'prompt': body.prompt, 'seed': 7,
                'input_references': [{'type': 'video_url', 'video_url': {'url': self.hosted(receipt.id).url}}],
                'provider': {'options': {'runway': {'parameters': {'keyframes': [{'seconds': 0, 'uri': FRAME}]}}}}})
            return httpx.Response(202, json={'id': 'remote-edit', 'status': 'pending', 'polling_url': '/api/v1/videos/remote-edit'})
        result = await OpenRouterClient(transport=httpx.MockTransport(serve)).submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
        self.assertEqual(result.id, 'remote-edit')
        self.assertEqual(ledger.get(receipt.id).remote_id, 'remote-edit')
        self.assertEqual(ledger.get(receipt.id).request_fingerprint, catalog.digest(catalog.video_edit_parameters(body)))
        self.assertNotIn('trycloudflare.com', (self.root/'requests'/f'{receipt.id}.json').read_text())

    async def test_video_host_identity_and_expiry_are_checked_before_a_paid_intent(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        for changed, code in (({'receipt_id': 'b'*32}, 'request_mismatch'),
                ({'source_sha256': 'b'*64}, 'request_mismatch'),
                ({'expires_at': datetime.now(timezone.utc)}, 'clip_host_expired')):
            with self.subTest(code=code, changed=changed):
                quote = catalog.quote_video_edit(body)
                receipt = ledger.prepare('video-host-boundary', quote)
                hosted = HostedClip.model_validate({**self.hosted(receipt.id).model_dump(), **changed})
                calls: list[str] = []
                def serve(request: httpx.Request) -> httpx.Response:
                    calls.append(request.method)
                    return httpx.Response(202, json={'id': 'should-not-submit'})
                client = OpenRouterClient(transport=httpx.MockTransport(serve))
                with self.assertRaises(OpenRouterError) as failure:
                    await client.submit_video_edit(receipt.id, body, quote.id, hosted=hosted)
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(ledger.get(receipt.id).state, 'intent')
                self.assertEqual(calls, [])
                self.assertEqual(catalog.validate_quote(quote.id, catalog.video_edit_parameters(body)), quote)

    async def test_post_failures_and_cancellation_are_ambiguous_without_replay(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        body = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME, reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        quote = catalog.quote_image_edit(body)
        receipt = ledger.prepare('image-job', quote)
        calls: list[str] = []
        def fail(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            raise httpx.ReadTimeout('private provider message', request=request)
        client = OpenRouterClient(transport=httpx.MockTransport(fail))
        with self.assertRaises(OpenRouterError) as failure:
            await client.submit_image_edit(receipt.id, body, quote.id)
        self.assertEqual(failure.exception.code, 'submission_unknown')
        ledger.recover()
        with self.assertRaises(OpenRouterError):
            await client.submit_image_edit(receipt.id, body, quote.id)
        self.assertEqual(calls, ['POST'])
        quote = catalog.quote_image_edit(body)
        receipt = ledger.prepare('image-cancel', quote)
        started = asyncio.Event()
        async def hang(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            return httpx.Response(500)
        task = asyncio.create_task(OpenRouterClient(transport=httpx.MockTransport(hang)).submit_image_edit(receipt.id, body, quote.id))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(ledger.get(receipt.id).state, 'submission_unknown')

    async def test_image_response_requires_one_bounded_raster(self) -> None:
        from app.openrouter_contracts import OpenRouterImageEditRequest
        body = OpenRouterImageEditRequest(prompt='Edit', source_frame=FRAME, reference_image=CHARACTER, aspect_ratio='16:9', seed=7)
        for data in ([], [{'b64_json': '!!!!'}], [{'b64_json': base64.b64encode(b'<svg/>').decode()}],
                [{'b64_json': base64.b64encode(raster()[:-14]).decode()}],
                [{'b64_json': base64.b64encode(raster()).decode()}]*2,
                [{'url': 'https://attacker.test/image.png'}],
                [{'b64_json': base64.b64encode(raster()).decode(), 'media_type': 'image/jpeg'}]):
            quote = catalog.quote_image_edit(body)
            receipt = ledger.prepare('image-invalid', quote)
            with self.assertRaises(OpenRouterError) as failure:
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:
                    httpx.Response(200, json={'data': data}))).submit_image_edit(receipt.id, body, quote.id)
            self.assertEqual(failure.exception.code, 'submission_unknown')
        quote = catalog.quote_image_edit(body)
        receipt = ledger.prepare('image-large', quote)
        with patch.object(client_module, '_IMAGE_JSON_LIMIT', 4):
            with self.assertRaises(OpenRouterError):
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:
                    httpx.Response(200, content=b' '*5))).submit_image_edit(receipt.id, body, quote.id)

    async def test_edit_references_and_geometry_reject_before_paid_admission(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        for value in ('https://example.com/video.mp4', 'data:image/png;base64,c29tZQ==', 'data:video/mp4;base64,!!!!'):
            with self.assertRaises((ValidationError, OpenRouterError)):
                body = OpenRouterVideoEditRequest(prompt='Edit', reference_video=value, reference_image=FRAME,
                    duration=2, width=854, height=480)
                catalog.quote_video_edit(body)
        with self.assertRaises(ValidationError):
            OpenRouterVideoEditRequest(prompt='Edit', reference_video=VIDEO, reference_image='x'*5000001,
                duration=2, width=854, height=480)
        for width, height in ((1, 480), (854, 479), (5000, 480), (854, 2000)):
            with self.assertRaises((ValidationError, OpenRouterError)):
                body = OpenRouterVideoEditRequest(prompt='Edit', reference_video=VIDEO, reference_image=FRAME,
                    duration=2, width=width, height=height)
                catalog.quote_video_edit(body)

    async def test_shared_poll_ignores_unused_remote_urls_and_accepts_expired(self) -> None:
        for status in ('completed', 'expired'):
            def serve(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={'id': 'remote-edit', 'status': status,
                    'polling_url': '/api/v1/videos/remote-edit',
                    'unsigned_urls': ['/api/v1/videos/remote-edit/content?index=0']})
            result = await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-edit')
            self.assertEqual(result.status, status)
        calls: list[str] = []
        def unused_metadata(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={'id': 'remote-edit', 'status': 'completed',
                'polling_url': '//attacker.test/path', 'unsigned_urls': ['https://storage.example.com/video.mp4']})
        try:
            result = await OpenRouterClient(transport=httpx.MockTransport(unused_metadata)).poll_video('remote-edit')
        except OpenRouterError as error:
            self.fail(f'Unused delivery metadata rejected a valid completed job: {error.code}')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(calls, ['https://openrouter.ai/api/v1/videos/remote-edit'])

    async def test_video_external_delivery_metadata_preserves_receipt_and_fixed_download(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        quote = catalog.quote_video_edit(body)
        receipt = ledger.prepare('video-delivery-metadata', quote)
        calls: list[tuple[str, str]] = []
        def serve(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, str(request.url)))
            self.assertTrue(request.headers['authorization'].startswith('Bearer '))
            if request.url.path.endswith('/content'):
                return httpx.Response(200, content=b'synthetic video', headers={'content-type': 'video/mp4'})
            return httpx.Response(202 if request.method == 'POST' else 200, json={
                'id': 'remote-edit', 'status': 'pending' if request.method == 'POST' else 'completed',
                'polling_url': 'https://untrusted.example/poll',
                'unsigned_urls': ['https://storage.example.com/video.mp4']})
        client = OpenRouterClient(transport=httpx.MockTransport(serve))
        try:
            remote = await client.submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
        except OpenRouterError as error:
            self.fail(f'Unused delivery metadata discarded an accepted job identity: {error.code}')
        self.assertEqual(ledger.get(receipt.id).state, 'submitted')
        self.assertEqual(ledger.get(receipt.id).remote_id, remote.id)
        self.assertEqual((await client.poll_video(remote.id)).status, 'completed')
        await client.download_video(remote.id, self.root/'download.mp4')
        self.assertEqual(calls, [('POST', 'https://openrouter.ai/api/v1/videos'),
            ('GET', 'https://openrouter.ai/api/v1/videos/remote-edit'),
            ('GET', 'https://openrouter.ai/api/v1/videos/remote-edit/content?index=0')])

    async def test_definitive_post_rejections_retain_status_and_never_replay(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        codes = {400: 'provider_request_rejected', 404: 'provider_request_rejected',
            405: 'provider_request_rejected', 413: 'provider_request_rejected',
            415: 'provider_request_rejected', 422: 'provider_request_rejected',
            401: 'credential_rejected', 402: 'provider_credit_limit',
            403: 'provider_access_denied', 429: 'provider_rate_limited'}
        for status, code in codes.items():
            with self.subTest(status=status):
                quote = catalog.quote_video_edit(body)
                receipt = ledger.prepare('video-rejected', quote)
                calls: list[str] = []
                def serve(request: httpx.Request) -> httpx.Response:
                    calls.append(request.method)
                    return httpx.Response(status, json={'error': {'message': 'private upstream message '+VIDEO}})
                client = OpenRouterClient(transport=httpx.MockTransport(serve))
                with self.assertRaises(OpenRouterError) as failure:
                    await client.submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
                record = ledger.get(receipt.id)
                self.assertEqual(record.state, 'failed')
                self.assertEqual(record.error_code, code)
                self.assertEqual(record.provider_http_status, status)
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(failure.exception.provider_http_status, status)
                ledger.recover()
                with self.assertRaises(OpenRouterError):
                    await client.submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
                self.assertEqual(calls, ['POST'])
                self.assertNotIn('private upstream message', (self.root/'requests'/f'{receipt.id}.json').read_text())

    async def test_ambiguous_http_and_malformed_success_retain_status_without_replay(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        for status in (408, 500, 502, 503, 202):
            with self.subTest(status=status):
                quote = catalog.quote_video_edit(body)
                receipt = ledger.prepare('video-unknown', quote)
                calls: list[str] = []
                def serve(request: httpx.Request) -> httpx.Response:
                    calls.append(request.method)
                    return httpx.Response(status, json={'unexpected': 'private upstream message'})
                client = OpenRouterClient(transport=httpx.MockTransport(serve))
                with self.assertRaises(OpenRouterError) as failure:
                    await client.submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
                record = ledger.get(receipt.id)
                self.assertEqual(record.state, 'submission_unknown')
                self.assertEqual(failure.exception.code, 'submission_unknown')
                self.assertEqual(record.provider_http_status, status)
                ledger.recover()
                with self.assertRaises(OpenRouterError):
                    await client.submit_video_edit(receipt.id, body, quote.id, hosted=self.hosted(receipt.id))
                self.assertEqual(calls, ['POST'])

    async def test_historical_unknown_receipt_is_read_without_rewriting_or_inventing_status(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        quote = catalog.quote_video_edit(body)
        receipt = ledger.prepare('old-unknown', quote)
        ledger.begin_submit(receipt.id, quote.id, quote.request_fingerprint)
        ledger.update(receipt.id, state='submission_unknown', error_code='submission_unknown')
        path = self.root/'requests'/f'{receipt.id}.json'
        document = TypeAdapter(JsonObject).validate_json(path.read_bytes())
        document.pop('provider_http_status', None)
        path.write_text(TypeAdapter(JsonObject).dump_json(document).decode())
        before = path.read_bytes()
        ledger.recover()
        record = ledger.get(receipt.id)
        self.assertEqual(record.state, 'submission_unknown')
        self.assertIsNone(record.provider_http_status)
        self.assertEqual(path.read_bytes(), before)

    async def test_receipt_status_is_strict_bounded_and_cannot_change_a_settled_receipt(self) -> None:
        from app.openrouter_contracts import OpenRouterReceipt, OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        quote = catalog.quote_video_edit(body)
        receipt = ledger.prepare('status-boundary', quote)
        values = receipt.model_dump()
        self.assertEqual(OpenRouterReceipt.model_validate({**values, 'provider_http_status': 202}).provider_http_status, 202)
        for status in (99, 600, True, '202', 202.0):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                OpenRouterReceipt.model_validate({**values, 'provider_http_status': status})
        ledger.begin_submit(receipt.id, quote.id, quote.request_fingerprint)
        ledger.record_http_status(receipt.id, 202)
        self.assertEqual(ledger.get(receipt.id).state, 'submitting')
        ledger.update(receipt.id, state='submitted', remote_id='remote-edit')
        with self.assertRaises(OpenRouterError):
            ledger.record_http_status(receipt.id, 400)

    async def test_crash_after_definitive_headers_recovers_failure_without_replaying(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoEditRequest
        body = OpenRouterVideoEditRequest(prompt='Preserve motion', reference_video=VIDEO,
            reference_image=FRAME, duration=4, width=854, height=480)
        quote = catalog.quote_video_edit(body)
        receipt = ledger.prepare('rejected-before-body', quote)
        ledger.begin_submit(receipt.id, quote.id, quote.request_fingerprint)
        ledger.record_http_status(receipt.id, 422)
        ledger.recover()
        record = ledger.get(receipt.id)
        self.assertEqual(record.state, 'failed')
        self.assertEqual(record.error_code, 'provider_request_rejected')
        self.assertEqual(record.provider_http_status, 422)
