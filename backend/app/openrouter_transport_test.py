"""Provider HTTP, cancellation, replay and parser behavior with synthetic inputs."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from app import openrouter_catalog as catalog, openrouter_settings as settings, openrouter_requests as ledger, openrouter_client as client_module
from app.openrouter_client import OpenRouterClient, OpenRouterError
from app.openrouter_contracts import OpenRouterMusicRequest, OpenRouterVideoRequest, OpenRouterSpeechRequest, OpenRouterSettingsRequest, OpenRouterKeyRequest

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

    async def test_video_id_durable_before_poll_and_hostile_content_urls_rejected(self) -> None:
        def serve(request: httpx.Request) -> httpx.Response:
            if request.method=='POST': return httpx.Response(202,json={'id':'remote-1','status':'pending','polling_url':'https://openrouter.ai/api/v1/videos/remote-1'})
            self.assertEqual(ledger.get(receipt.id).remote_id,'remote-1')
            return httpx.Response(200,json={'id':'remote-1','status':'completed','unsigned_urls':['https://attacker.test/download']})
        body=OpenRouterVideoRequest(model='google/veo-3.1-fast',prompt='Sunrise',duration=4,size='1280x720'); quote=catalog.quote_video(body); receipt=ledger.prepare('video-job',quote)
        client=OpenRouterClient(transport=httpx.MockTransport(serve)); result=await client.submit_video(receipt.id,body,quote.id)
        self.assertEqual(ledger.get(receipt.id).state,'submitted')
        with self.assertRaises(OpenRouterError): await client.poll_video(result.id)

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
