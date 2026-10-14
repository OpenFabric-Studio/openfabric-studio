"""Provider HTTP, cancellation, replay and parser behavior with synthetic inputs."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import tempfile
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from pydantic import ValidationError
from app import openrouter_catalog as catalog, openrouter_settings as settings, openrouter_requests as ledger, openrouter_client as client_module
from app.openrouter_client import OpenRouterClient, OpenRouterError
from app.openrouter_contracts import OpenRouterMusicRequest, OpenRouterVideoRequest, OpenRouterSpeechRequest, OpenRouterSettingsRequest, OpenRouterKeyRequest
from app.contracts import JsonObject

VIDEO={'data':[{'id':'google/veo-3.1-fast','name':'Veo Fast','supported_durations':[4,6,8], 'supported_sizes':['1280x720'],'supported_resolutions':['720p'],'supported_aspect_ratios':['16:9'], 'supported_frame_images':['first_frame'],'generate_audio':True,'seed':True,'pricing_skus':{'duration_seconds_without_audio':'0.10','duration_seconds_without_audio_720p':'0.08'}}]}
AUDIO={'data':[{'id':'hexgrad/kokoro-82m','name':'Kokoro','architecture':{'output_modalities':['speech']},'supported_voices':['af_heart'],'pricing':{'prompt':'0.000001'}}]}
MUSIC={'data':[{'id':'google/lyria-3-clip-preview','name':'Lyria','architecture':{'output_modalities':['text','audio']},'pricing':{'prompt':'0','completion':'0'}}]}

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ,{'OPENFABRIC_CONFIG':str(self.root/'config'),'REMIQORA_CONFIG':str(self.root/'config'),'OPENFABRIC_DATA_DIR':str(self.root/'library'),'REMIQORA_DATA_DIR':str(self.root/'library'),'OPENFABRIC_MODULE_ROOT':str(self.root/'modules'),'SEED_VC_DIR':str(self.root/'seed'),'OPENROUTER_API_KEY':''}))
        for module,name,path in ((settings,'SETTINGS_PATH',self.root/'settings.json'),(catalog,'CATALOG_PATH',self.root/'catalog.json'),(ledger,'REQUESTS_ROOT',self.root/'requests')): self.enterContext(patch.object(module,name,path))
        self.enterContext(patch.object(settings.credentials,'native_backend',return_value=None))
        self.enterContext(patch.object(client_module,'_STOPPING',False))
        settings.credentials.clear_session()
        settings.save(OpenRouterSettingsRequest(enabled=True,estimate_limit_usd=1))
        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48,persist=False))
        catalog.publish([*catalog.normalize_video(VIDEO),*catalog.normalize_audio(AUDIO,'speech'),*catalog.normalize_audio(MUSIC,'music')])

    async def asyncTearDown(self) -> None: settings.credentials.clear_session()

    async def test_poll_retries_transient_http_without_repeating_paid_submission(self) -> None:
        calls: list[tuple[str, str]] = []
        responses: list[httpx.Response] = []
        body = OpenRouterVideoRequest(model='google/veo-3.1-fast', prompt='Sunrise', duration=4, size='1280x720')
        quote = catalog.quote_video(body); receipt = ledger.prepare('retry-job', quote)
        def serve(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == 'POST': return httpx.Response(202, json={'id': 'remote-retry', 'status': 'pending'})
            self.assertEqual(ledger.get(receipt.id).remote_id, 'remote-retry')
            response = httpx.Response((429, 503, 200)[len(responses)], json={'id': 'remote-retry', 'status': 'completed'})
            responses.append(response)
            return response
        async def backoff(delay: float) -> None:
            self.assertTrue(responses[-1].is_closed)
            self.assertTrue(client_module.work_busy())
        client = OpenRouterClient(transport=httpx.MockTransport(serve))
        remote = await client.submit_video(receipt.id, body, quote.id)
        with patch.object(client_module.asyncio, 'sleep', side_effect=backoff) as sleep:
            result = await client.poll_video(remote.id)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(calls, [('POST', '/api/v1/videos')] + [('GET', '/api/v1/videos/remote-retry')] * 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1.0, 2.0])
        self.assertFalse(client_module.work_busy())

    async def test_read_retry_bound_and_permanent_rejections(self) -> None:
        for status in (408, 429, 500, 502, 503, 504, 400, 401, 402, 403, 404, 501, 302):
            with self.subTest(status=status), patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep:
                calls: list[str] = []
                def serve(request: httpx.Request) -> httpx.Response:
                    calls.append(request.method)
                    return httpx.Response(status)
                with self.assertRaises(OpenRouterError) as failure:
                    await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-bound')
                expected = 3 if status in {408, 429, 500, 502, 503, 504} else 1
                self.assertEqual(len(calls), expected)
                self.assertEqual(sleep.await_count, expected - 1)
                self.assertEqual(failure.exception.provider_http_status, status)

    async def test_read_retries_network_failures(self) -> None:
        for kind in (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            with self.subTest(error=kind), patch.object(client_module.asyncio, 'sleep', AsyncMock()):
                calls = 0
                def serve(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if calls < 3: raise kind('synthetic network interruption', request=request)
                    return httpx.Response(200, json={'id': 'remote-network', 'status': 'completed'})
                result = await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-network')
                self.assertEqual(result.status, 'completed'); self.assertEqual(calls, 3)

    async def test_read_network_retry_exhaustion_keeps_stable_error(self) -> None:
        calls = 0
        def serve(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError('synthetic outage', request=request)
        with patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep:
            with self.assertRaisesRegex(OpenRouterError, 'provider_unavailable'):
                await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-outage')
        self.assertEqual(calls, 3); self.assertEqual(sleep.await_count, 2)
        self.assertFalse(client_module.work_busy())

    async def test_read_retry_discards_partial_body_and_closes_stream(self) -> None:
        closed = False; calls = 0
        class Interrupted(httpx.AsyncByteStream):
            async def __aiter__(inner) -> AsyncIterator[bytes]:
                yield b'{"id":"partial'
                raise httpx.ReadError('synthetic interrupted body')
            async def aclose(inner) -> None:
                nonlocal closed
                closed = True
        def serve(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, stream=Interrupted()) if calls == 1 else httpx.Response(200, json={'id': 'remote-stream', 'status': 'completed'})
        async def backoff(delay: float) -> None:
            self.assertTrue(closed)
        with patch.object(client_module.asyncio, 'sleep', side_effect=backoff):
            result = await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-stream')
        self.assertEqual(result.status, 'completed'); self.assertEqual(calls, 2)

    async def test_read_does_not_retry_invalid_or_oversized_responses(self) -> None:
        for content, code in ((b'not json', 'provider_response_invalid'), (b'{}', 'provider_response_invalid'),
                              (b'{"id":"other","status":"completed"}', 'provider_response_invalid'),
                              (b'x' * 65, 'provider_output_too_large')):
            with self.subTest(content=content), patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep:
                calls = 0
                def serve(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(200, content=content)
                with patch.object(client_module, '_JSON_LIMIT', 64), self.assertRaisesRegex(OpenRouterError, code):
                    await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-invalid')
                self.assertEqual(calls, 1); sleep.assert_not_awaited()

    async def test_read_retries_share_one_total_deadline(self) -> None:
        actual_timeout = asyncio.timeout; calls = 0; timeouts: list[float | None] = []
        def deadline(seconds: float | None) -> asyncio.Timeout:
            timeouts.append(seconds)
            return actual_timeout(0.05)
        async def serve(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3: return httpx.Response(503)
            await asyncio.Event().wait()
            return httpx.Response(200)
        with patch.object(client_module.asyncio, 'timeout', side_effect=deadline), patch.object(client_module.asyncio, 'sleep', AsyncMock()):
            with self.assertRaisesRegex(OpenRouterError, 'provider_unavailable') as failure:
                await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-deadline')
        self.assertIsInstance(failure.exception.__cause__, TimeoutError)
        self.assertEqual(calls, 3); self.assertEqual(timeouts, [180])
        self.assertFalse(client_module.work_busy())

    async def test_read_retry_after_seconds_dates_and_invalid_headers(self) -> None:
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        future = format_datetime(now + timedelta(seconds=20), usegmt=True)
        for value, expected in (('7', 7.0), ('0', 1.0), ('30', 30.0), ('invalid', 1.0), ('-1', 1.0), ('NaN', 1.0),
                                ('١', 1.0), ('Wed, 21 Oct 2015 07:28:00 GMT', 1.0), (future, 20.0)):
            with self.subTest(header=value), patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep, patch.object(client_module, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = now
                calls = 0
                def serve(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(429, headers=[(b'Retry-After', value.encode('utf-8'))]) if calls == 1 else httpx.Response(200, json={'id': 'remote-header', 'status': 'completed'})
                await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-header')
                self.assertEqual(calls, 2); self.assertEqual(sleep.await_count, 1)
                delay = sleep.await_args_list[0].args[0]
                self.assertEqual(delay, expected)

    async def test_read_never_retries_before_a_long_retry_after(self) -> None:
        future = format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True)
        for value in ('31', '9999999999', '1' * 129, '0' * 127 + '31', future):
            with self.subTest(header=value), patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep:
                calls = 0
                def serve(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(429, headers={'Retry-After': value})
                with self.assertRaisesRegex(OpenRouterError, 'provider_rate_limited'):
                    await OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-later')
                self.assertEqual(calls, 1); sleep.assert_not_awaited()

    async def test_paid_video_submission_never_retries_transient_failures(self) -> None:
        for status in (429, 503, None):
            with self.subTest(status=status), patch.object(client_module.asyncio, 'sleep', AsyncMock()) as sleep:
                calls = 0
                def serve(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if status is None: raise httpx.ReadError('ambiguous submission', request=request)
                    return httpx.Response(status)
                body = OpenRouterVideoRequest(model='google/veo-3.1-fast', prompt='Sunrise', duration=4, size='1280x720')
                quote = catalog.quote_video(body); receipt = ledger.prepare('single-post', quote)
                with self.assertRaises(OpenRouterError):
                    await OpenRouterClient(transport=httpx.MockTransport(serve)).submit_video(receipt.id, body, quote.id)
                self.assertEqual(calls, 1); sleep.assert_not_awaited()
                self.assertEqual(ledger.get(receipt.id).state, 'failed' if status == 429 else 'submission_unknown')

    async def test_shutdown_cancels_read_backoff_without_another_request(self) -> None:
        entered = asyncio.Event(); responses: list[httpx.Response] = []
        def serve(request: httpx.Request) -> httpx.Response:
            response = httpx.Response(503); responses.append(response); return response
        async def backoff(delay: float) -> None:
            self.assertTrue(responses[-1].is_closed)
            entered.set(); await asyncio.Event().wait()
        with patch.object(client_module.asyncio, 'sleep', side_effect=backoff):
            task = asyncio.create_task(OpenRouterClient(transport=httpx.MockTransport(serve)).poll_video('remote-stop'))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await client_module.shutdown()
                self.assertTrue(task.cancelled()); self.assertFalse(client_module.work_busy())
                self.assertEqual(len(responses), 1)
            finally:
                task.cancel(); await asyncio.gather(task, return_exceptions=True)

    async def test_quote_single_claim_and_changed_body_refused(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano'); quote=catalog.quote_music(body); first=ledger.prepare('job',quote)
        self.assertEqual(ledger.prepare('job',quote).id,first.id)
        with self.assertRaises(OpenRouterError): ledger.prepare('other-job',quote)
        with self.assertRaises(OpenRouterError): catalog.validate_quote(quote.id,catalog.music_parameters(body.model_copy(update={'prompt':'Changed'})))
        ledger.begin_submit(first.id,quote.id,quote.request_fingerprint)
        with self.assertRaises(OpenRouterError): ledger.begin_submit(first.id,quote.id,quote.request_fingerprint)
        ledger.recover(); self.assertEqual(ledger.get(first.id).state,'submission_unknown')

    async def test_music_sse_audio_cost_and_no_invented_voice(self) -> None:
        audio=b'RIFFtestWAVEfixture'
        chunks=[{'id':'gen-test','choices':[{'index':0,'delta':{'audio':{'data':base64.b64encode(audio).decode(),'transcript':'Melody'}}}]},{'id':'gen-test','choices':[],'usage':{'cost':0.04}}]
        wire=''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
        def serve(request: httpx.Request) -> httpx.Response:
            payload=json.loads(request.content); self.assertTrue(payload['stream']); self.assertEqual(payload['modalities'],['text','audio']); self.assertEqual(payload['audio'],{'format':'wav'})
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire.encode())
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Melody'); quote=catalog.quote_music(body); receipt=ledger.prepare('job',quote)
        result=await OpenRouterClient(transport=httpx.MockTransport(serve)).complete_music(receipt.id,body,quote.id)
        self.assertEqual(result.data,audio); self.assertEqual(result.actual_cost_usd,0.04); self.assertEqual(ledger.get(receipt.id).state,'completed')

    async def test_truncated_stream_is_unknown(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Melody'); quote=catalog.quote_music(body); receipt=ledger.prepare('job',quote)
        client=OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {"choices":[]}\n\n')))
        with self.assertRaises(OpenRouterError) as failure: await client.complete_music(receipt.id,body,quote.id)
        self.assertEqual(failure.exception.code,'submission_unknown'); self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')

    async def test_video_id_durable_before_poll_and_unused_content_urls_not_followed(self) -> None:
        calls: list[str]=[]
        def serve(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.method=='POST': return httpx.Response(202,json={'id':'remote-1','status':'pending','polling_url':'https://openrouter.ai/api/v1/videos/remote-1'})
            self.assertEqual(ledger.get(receipt.id).remote_id,'remote-1')
            return httpx.Response(200,json={'id':'remote-1','status':'completed','unsigned_urls':['https://attacker.test/download']})
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Sunrise',duration=4,size='1280x720'); quote=catalog.quote_video(body); receipt=ledger.prepare('video-job',quote)
        client=OpenRouterClient(transport=httpx.MockTransport(serve)); result=await client.submit_video(receipt.id,body,quote.id)
        self.assertEqual(ledger.get(receipt.id).state,'submitted')
        self.assertEqual((await client.poll_video(result.id)).status,'completed')
        self.assertEqual(calls,['https://openrouter.ai/api/v1/videos','https://openrouter.ai/api/v1/videos/remote-1'])

    async def test_speech_mp3_presets_do_not_send_invented_references(self) -> None:
        def serve(request: httpx.Request) -> httpx.Response:
            self.assertEqual(json.loads(request.content),{'model':'hexgrad/kokoro-82m','input':'Hello','voice':'af_heart','response_format':'mp3'})
            return httpx.Response(200,headers={'content-type':'audio/mpeg','x-generation-id':'gen-speech'},content=b'mock-mp3')
        body=OpenRouterSpeechRequest(model='hexgrad/kokoro-82m',input='Hello',voice='af_heart'); quote=catalog.quote_speech(body); receipt=ledger.prepare('speech-job',quote)
        result=await OpenRouterClient(transport=httpx.MockTransport(serve)).speech(receipt.id,body,quote.id)
        self.assertEqual(result.generation_id,'gen-speech')

    async def test_download_byte_bounds_and_redirects_leave_no_partial(self) -> None:
        dest=self.root/'clip.mp4'; client=OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'video/mp4'},content=b'toolarge')))
        with patch.object(client_module,'_VIDEO_LIMIT',2):
            with self.assertRaises(OpenRouterError): await client.download_video('remote-1',dest)
        self.assertFalse(dest.exists())
        calls=[]
        def redirect(request: httpx.Request) -> httpx.Response: calls.append(str(request.url)); return httpx.Response(302,headers={'location':'https://attacker.test'})
        with self.assertRaises(OpenRouterError): await OpenRouterClient(transport=httpx.MockTransport(redirect)).download_video('remote-1',dest)
        self.assertEqual(len(calls),1); self.assertFalse(dest.exists())

    async def test_shutdown_owns_http_and_records_ambiguous_cancellation(self) -> None:
        started=asyncio.Event()
        async def hanging(request: httpx.Request) -> httpx.Response: started.set(); await asyncio.Event().wait(); return httpx.Response(500)
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano'); quote=catalog.quote_music(body); receipt=ledger.prepare('job',quote)
        task=asyncio.create_task(OpenRouterClient(transport=httpx.MockTransport(hanging)).complete_music(receipt.id,body,quote.id))
        await started.wait(); self.assertTrue(client_module.work_busy()); await client_module.shutdown()
        self.assertTrue(task.cancelled()); self.assertFalse(client_module.work_busy()); self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')

    async def test_capability_booleans_and_geometry_not_coerced(self) -> None:
        fixture=json.loads(json.dumps(VIDEO)); fixture['data'][0]['seed']='yes'
        with self.assertRaises(ValidationError): catalog.normalize_video(fixture)
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720',aspect_ratio='9:16')
        with self.assertRaises(OpenRouterError): catalog.quote_video(body)

    async def test_more_than_history_limit_does_not_prevent_recovery(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano'); quote=catalog.quote_music(body); receipt=ledger.prepare('job',quote)
        for index in range(1002):
            record=receipt.model_copy(update={'id':f'{index:032x}','state':'submitting'})
            (self.root/'requests'/f'{record.id}.json').write_text(record.model_dump_json())
        ledger.recover(); self.assertEqual(ledger.get('0'*32).state,'submission_unknown'); self.assertEqual(len(ledger.list_receipts()),1000)

    async def test_shutdown_drains_http_in_synthesis_worker_event_loop(self) -> None:
        started=asyncio.Event(); main_loop=asyncio.get_running_loop()
        foreign: list[asyncio.Task[object]]=[]
        async def hanging(request: httpx.Request) -> httpx.Response:
            task: asyncio.Task[object] | None=asyncio.current_task()
            if task is None: raise RuntimeError('Missing owned task')
            foreign.append(task); main_loop.call_soon_threadsafe(started.set)
            await asyncio.Event().wait(); return httpx.Response(500)
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano'); quote=catalog.quote_music(body); receipt=ledger.prepare('thread-job',quote)
        def worker() -> None:
            try: asyncio.run(OpenRouterClient(transport=httpx.MockTransport(hanging)).complete_music(receipt.id,body,quote.id))
            except asyncio.CancelledError: pass
        worker_task=asyncio.create_task(asyncio.to_thread(worker))
        await started.wait()
        try:
            await client_module.shutdown()
            await worker_task
            self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')
            self.assertFalse(client_module.work_busy())
        finally:
            for task in foreign:
                if not task.done(): task.get_loop().call_soon_threadsafe(task.cancel)
            await worker_task

    async def test_corrupt_receipt_never_blocks_other_recovery_or_reposts(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano'); quote=catalog.quote_music(body); receipt=ledger.prepare('job',quote)
        ledger.begin_submit(receipt.id,quote.id,quote.request_fingerprint)
        (self.root/'requests'/('f'*32+'.json')).write_text('{malformed')
        ledger.recover()
        self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')
        self.assertEqual([row.id for row in ledger.list_receipts()],[receipt.id])

    async def test_remote_identity_cannot_be_replaced(self) -> None:
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('video-job',quote)
        ledger.begin_submit(receipt.id,quote.id,quote.request_fingerprint)
        ledger.update(receipt.id,state='submitted',remote_id='owned-remote')
        with self.assertRaises(OpenRouterError):
            ledger.update(receipt.id,state='submitted',remote_id='unrelated-remote')
        self.assertEqual(ledger.get(receipt.id).remote_id,'owned-remote')

    async def test_download_creation_race_preserves_unowned_file(self) -> None:
        dest=self.root/'clip.mp4'
        def race(request: httpx.Request) -> httpx.Response:
            dest.write_bytes(b'existing-verified-output')
            return httpx.Response(200,headers={'content-type':'video/mp4'},content=b'new-output')
        with self.assertRaises(OpenRouterError):
            await OpenRouterClient(transport=httpx.MockTransport(race)).download_video('remote-1',dest)
        self.assertEqual(dest.read_bytes(),b'existing-verified-output')

    async def test_live_aspect_ratios_must_be_positive_numeric_pairs(self) -> None:
        fixture=json.loads(json.dumps(VIDEO)); fixture['data'][0]['supported_aspect_ratios']=['0:0']
        with self.assertRaises(ValueError): catalog.normalize_video(fixture)

    async def test_music_seed_requires_advertised_capability(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano',seed=7)
        with self.assertRaises(OpenRouterError): catalog.quote_music(body)

    async def test_final_speech_consent_check_precedes_paid_submission(self) -> None:
        posts=[]
        def serve(request: httpx.Request) -> httpx.Response:
            posts.append(request.method)
            return httpx.Response(200,headers={'content-type':'audio/mpeg'},content=b'audio')
        body=OpenRouterSpeechRequest(model='hexgrad/kokoro-82m',input='Hello',voice='af_heart')
        quote=catalog.quote_speech(body); receipt=ledger.prepare('speech-job',quote)
        def revoked() -> None: raise OpenRouterError('cloud_consent_required',409)
        with self.assertRaises(OpenRouterError):
            await OpenRouterClient(transport=httpx.MockTransport(serve)).speech(receipt.id,body,quote.id,before_submit=revoked)
        self.assertEqual(posts,[]); self.assertEqual(ledger.get(receipt.id).state,'intent')

    async def test_clone_quote_prices_utf8_bytes_and_binds_transcript(self) -> None:
        fixture={'data':[{'id':'fish-audio/s2.1-pro','name':'Fish','architecture':{'output_modalities':['speech']},'pricing':{'prompt':'0.000015'}}]}
        model=catalog.normalize_audio(fixture,'speech')[0]
        model=catalog.apply_endpoints(model,{'data':{'endpoints':[{'model_id':model.id,'supports_voice_cloning':True,'pricing':{'prompt':'0.000015'}}]}})
        catalog.publish([model])
        body=OpenRouterSpeechRequest(model=model.id,input='你好',reference_audio='data:audio/wav;base64,'+base64.b64encode(b'pcm').decode(),reference_transcript='Original words')
        quote=catalog.quote_speech(body)
        self.assertAlmostEqual(quote.estimated_usd,6*0.000015)
        with self.assertRaises(OpenRouterError):
            catalog.validate_quote(quote.id,catalog.speech_parameters(body.model_copy(update={'reference_transcript':'Different words'})))

    async def test_speech_batch_estimates_do_not_allocate_paid_quotes(self) -> None:
        body=OpenRouterSpeechRequest(model='hexgrad/kokoro-82m',input='Hello',voice='af_heart')
        before=len(catalog._QUOTES)
        estimate=catalog.estimate_speech(body)
        self.assertEqual(len(catalog._QUOTES),before)
        self.assertEqual(estimate.estimated_usd,5*0.000001)
        with self.assertRaises(OpenRouterError): ledger.prepare('unapproved-preview',estimate)

    async def test_consumed_quote_releases_admission_after_durable_begin(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='Piano')
        quote=catalog.quote_music(body); receipt=ledger.prepare('music-job',quote)
        def failed(request: httpx.Request) -> httpx.Response:
            self.assertEqual(ledger.get(receipt.id).state,'submitting')
            self.assertNotIn(quote.id,catalog._QUOTES)
            raise httpx.ReadTimeout('ambiguous',request=request)
        with self.assertRaises(OpenRouterError):
            await OpenRouterClient(transport=httpx.MockTransport(failed)).complete_music(receipt.id,body,quote.id)
        self.assertEqual(ledger.get(receipt.id).state,'submission_unknown')

    async def test_rejection_logs_only_redacted_typed_details(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='private actor role')
        quote=catalog.quote_music(body); receipt=ledger.prepare('diagnostic-job',quote)
        ledger.begin_submit(receipt.id,quote.id,quote.request_fingerprint)
        key='sk-or-v1-'+'x'*48
        payload: JsonObject={'model':'runway/aleph-2','prompt':body.prompt,
            'input_references':[{'type':'video_url','video_url':{'url':'data:video/mp4;base64,Y2xpcA=='}}],
            'provider':{'only':['runway'],'options':{'runway':{'parameters':{'keyframes':[{'uri':'data:image/png;base64,ZnJhbWU='}]}}}}}
        message=('Unsupported geometry: private actor role runway/aleph-2 video_url runway '
            'data:video/mp4;base64,Y2xpcA== data:image/png;base64,ZnJhbWU= '
            +key+' Bearer unknown-credential sk-unknown-secret https://private.test/path '
            'http://other.test/secret www.private.test/path\n\x1b\x00\u202e')
        posts: list[str]=[]
        def reject(request: httpx.Request) -> httpx.Response:
            posts.append(request.method)
            return httpx.Response(400,json={'error':{'message':message,'code':'bad_geometry'},
                'metadata':{'private':'never log arbitrary error metadata'}})
        with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
            with self.assertRaises(OpenRouterError) as failure:
                await OpenRouterClient(transport=httpx.MockTransport(reject))._json_request(
                    'POST','/videos',payload=payload,receipt_id=receipt.id)
        diagnostic=' '.join(record.getMessage() for record in logged.records)
        self.assertIn('Unsupported geometry',diagnostic)
        self.assertIn('bad_geometry',diagnostic)
        for private in (body.prompt,'runway/aleph-2','video_url','runway','Y2xpcA==','ZnJhbWU=',
                key,'unknown-credential','sk-unknown-secret','private.test','other.test','never log arbitrary',
                '\n','\x1b','\x00','\u202e'):
            self.assertNotIn(private,diagnostic)
        self.assertEqual(failure.exception.code,'provider_request_rejected')
        self.assertEqual(failure.exception.provider_http_status,400)
        self.assertEqual(str(failure.exception),'provider_request_rejected')
        self.assertEqual(posts,['POST'])

    async def test_rejection_message_is_bounded_after_redaction(self) -> None:
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='secret-marker '+'q'*1200,duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('bounded-diagnostic',quote)
        response=httpx.Response(400,json={'error':{'message':'Unsupported geometry '+body.prompt+'x'*5000,'code':400}})
        with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
            with self.assertRaises(OpenRouterError):
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:response)).submit_video(receipt.id,body,quote.id)
        self.assertLessEqual(len(logged.records[0].getMessage()),1250)
        self.assertIn('Unsupported geometry',logged.records[0].getMessage())
        self.assertNotIn('secret-marker',str(logged.output))
        self.assertNotIn('q'*50,str(logged.output))
        self.assertEqual(ledger.get(receipt.id).state,'failed')

    async def test_short_payload_values_cannot_hide_urls_from_redaction(self) -> None:
        body=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='data')
        quote=catalog.quote_music(body); receipt=ledger.prepare('short-diagnostic',quote)
        ledger.begin_submit(receipt.id,quote.id,quote.request_fingerprint)
        payload: JsonObject={'prompt':body.prompt,'parts':['https','sk','Bearer']}
        message='Unsupported geometry data:image/png;base64,cHJpdmF0ZQ== https://private.test/secret sk-private-secret Bearer private-token'
        with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
            with self.assertRaises(OpenRouterError):
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'error':{'message':message}})))._json_request(
                    'POST','/videos',payload=payload,receipt_id=receipt.id)
        diagnostic=' '.join(record.getMessage() for record in logged.records)
        for private in ('cHJpdmF0ZQ==','private.test','private-secret','private-token'):
            self.assertNotIn(private,diagnostic)

    async def test_json_escaped_prompt_echoes_are_redacted(self) -> None:
        prompt='private-marker first line\nsecond line café'
        for ensure_ascii in (True,False):
            with self.subTest(ensure_ascii=ensure_ascii):
                body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt=prompt,duration=4,size='1280x720')
                quote=catalog.quote_video(body); receipt=ledger.prepare('escaped-diagnostic-'+str(ensure_ascii),quote)
                key='sk-or-v1-'+'x'*48
                message='Unsupported geometry '+json.dumps({'prompt':prompt,'credential':key},ensure_ascii=ensure_ascii)
                response=httpx.Response(400,json={'error':{'message':message,'code':'bad_geometry'}})
                with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
                    with self.assertRaises(OpenRouterError):
                        await OpenRouterClient(transport=httpx.MockTransport(lambda request:response)).submit_video(receipt.id,body,quote.id)
                diagnostic=' '.join(record.getMessage() for record in logged.records)
                self.assertIn('Unsupported geometry',diagnostic)
                for private in ('private-marker','first line','second line','café','caf\\u00e9',key):
                    self.assertNotIn(private,diagnostic)
                self.assertEqual(ledger.get(receipt.id).state,'failed')

    async def test_url_filter_does_not_break_complete_private_prompt_redaction(self) -> None:
        prompt='private-marker see https://private.test/path then\nsecond line café'
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt=prompt,duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('url-in-private-prompt',quote)
        message='Unsupported geometry '+json.dumps({'prompt':prompt})
        with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
            with self.assertRaises(OpenRouterError):
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'error':{'message':message}}))).submit_video(receipt.id,body,quote.id)
        diagnostic=' '.join(record.getMessage() for record in logged.records)
        for private in ('private-marker','second line','café','caf\\u00e9','private.test'):
            self.assertNotIn(private,diagnostic)

    async def test_invalid_rejection_bodies_do_not_change_definite_http_failure(self) -> None:
        bodies=(b'',b'private malformed body',b'{"error":{"message":"private truncated body"',
            b'[]',b'{"error":{"message":12,"code":true}}',
            json.dumps({'error':{'message':'private oversized body '+'x'*17000}}).encode())
        for index,wire in enumerate(bodies):
            with self.subTest(index=index):
                body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720')
                quote=catalog.quote_video(body); receipt=ledger.prepare('invalid-diagnostic-'+str(index),quote)
                posts: list[str]=[]
                def reject(request: httpx.Request) -> httpx.Response:
                    posts.append(request.method); return httpx.Response(400,content=wire)
                with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
                    with self.assertRaises(OpenRouterError) as failure:
                        await OpenRouterClient(transport=httpx.MockTransport(reject)).submit_video(receipt.id,body,quote.id)
                self.assertNotIn('private',str(logged.output))
                self.assertEqual(failure.exception.code,'provider_request_rejected')
                self.assertEqual(ledger.get(receipt.id).state,'failed')
                self.assertEqual(ledger.get(receipt.id).provider_http_status,400)
                self.assertEqual(posts,['POST'])

    async def test_rejection_read_is_bounded_and_stream_is_closed(self) -> None:
        class Oversized(httpx.AsyncByteStream):
            read_bytes=0
            closed=False
            async def __aiter__(self) -> AsyncIterator[bytes]:
                for _ in range(100000):
                    self.read_bytes+=1
                    yield b'x'
            async def aclose(self) -> None: self.closed=True
        stream=Oversized()
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('oversized-diagnostic',quote)
        with self.assertRaises(OpenRouterError):
            await OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(400,stream=stream))).submit_video(receipt.id,body,quote.id)
        self.assertGreater(stream.read_bytes,0)
        self.assertLessEqual(stream.read_bytes,16385)
        self.assertTrue(stream.closed)
        self.assertEqual(ledger.get(receipt.id).state,'failed')

    async def test_rejection_read_failure_retains_received_http_status(self) -> None:
        class Broken(httpx.AsyncByteStream):
            read=False
            async def __aiter__(self) -> AsyncIterator[bytes]:
                self.read=True
                yield b'{"error":'
                raise httpx.ReadTimeout('private read failure')
        stream=Broken()
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('broken-diagnostic',quote)
        with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
            with self.assertRaises(OpenRouterError) as failure:
                await OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(400,stream=stream))).submit_video(receipt.id,body,quote.id)
        self.assertTrue(stream.read)
        self.assertNotIn('private read failure',str(logged.output))
        self.assertEqual(failure.exception.code,'provider_request_rejected')
        self.assertEqual(failure.exception.provider_http_status,400)
        self.assertEqual(ledger.get(receipt.id).state,'failed')

    async def test_cancelled_rejection_read_retains_definite_failure(self) -> None:
        started=asyncio.Event()
        class Waiting(httpx.AsyncByteStream):
            closed=False
            async def __aiter__(self) -> AsyncIterator[bytes]:
                started.set()
                await asyncio.Event().wait()
                yield b'never'
            async def aclose(self) -> None: self.closed=True
        stream=Waiting()
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Scene',duration=4,size='1280x720')
        quote=catalog.quote_video(body); receipt=ledger.prepare('cancelled-diagnostic',quote)
        task=asyncio.create_task(OpenRouterClient(transport=httpx.MockTransport(lambda request:httpx.Response(400,stream=stream))).submit_video(receipt.id,body,quote.id))
        waiter=asyncio.create_task(started.wait())
        try:
            await asyncio.wait({task,waiter},timeout=1,return_when=asyncio.FIRST_COMPLETED)
            if task.done(): await asyncio.gather(task,return_exceptions=True)
            self.assertTrue(started.is_set(),'A rejection body must be inspected before returning')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(ledger.get(receipt.id).state,'failed')
            self.assertEqual(ledger.get(receipt.id).provider_http_status,400)
            self.assertEqual(ledger.get(receipt.id).error_code,'provider_request_rejected')
            self.assertTrue(stream.closed)
            self.assertFalse(client_module.work_busy())
        finally:
            task.cancel(); waiter.cancel()
            await asyncio.gather(task,waiter,return_exceptions=True)

    async def test_speech_and_music_http_rejections_use_redacted_diagnostics(self) -> None:
        for kind in ('speech','music'):
            with self.subTest(kind=kind):
                def reject(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(400,json={'error':{'message':'Unsupported voice private words','code':'bad_voice'}})
                client=OpenRouterClient(transport=httpx.MockTransport(reject))
                with self.assertLogs('app.openrouter_client',level='WARNING') as logged:
                    with self.assertRaises(OpenRouterError) as failure:
                        if kind=='speech':
                            speech=OpenRouterSpeechRequest(model='hexgrad/kokoro-82m',input='private words',voice='af_heart')
                            quote=catalog.quote_speech(speech); receipt=ledger.prepare(kind,quote)
                            await client.speech(receipt.id,speech,quote.id)
                        else:
                            music=OpenRouterMusicRequest(model='google/lyria-3-clip-preview',prompt='private words')
                            quote=catalog.quote_music(music); receipt=ledger.prepare(kind,quote)
                            await client.complete_music(receipt.id,music,quote.id)
                self.assertIn('Unsupported voice',str(logged.output))
                self.assertNotIn('private words',str(logged.output))
                self.assertEqual(failure.exception.code,'provider_request_rejected')
                self.assertEqual(ledger.get(receipt.id).state,'failed')
