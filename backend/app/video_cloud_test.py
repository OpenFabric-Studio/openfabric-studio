"""Cloud jobs own persisted intent and resume GETs without repeating paid POSTs."""
from __future__ import annotations
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import openrouter_catalog as catalog, openrouter_requests as ledger, video_projects as store, video_render as render
from app.openrouter_contracts import OpenRouterVideoJob, OpenRouterVideoRequest
from app.openrouter_errors import OpenRouterError
from app.video_contracts import CreateVideoProjectRequest, UpdateVideoProjectRequest, OpenRouterVideoProviderConfig, VideoCloudQuoteRequest, VideoCloudSubmitRequest, VideoCloudResumeRequest, VideoShotDraft, VideoRevisionRequest


class CloudJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store,'DATA_DIR',self.root/'library'))
        self.enterContext(patch.object(catalog,'CATALOG_PATH',self.root/'catalog.json'))
        self.enterContext(patch.object(catalog,'_QUOTES',{}))
        self.enterContext(patch.object(ledger,'REQUESTS_ROOT',self.root/'requests'))
        from app import video_cloud, openrouter_settings as settings
        from app.openrouter_client import start
        self.enterContext(patch.object(video_cloud,'_QUOTES',{}))
        self.enterContext(patch.object(settings,'SETTINGS_PATH',self.root/'settings.json'))
        start()
        catalog.publish(catalog.normalize_video({'data':[{'id':'google/veo-3.1-fast','name':'Veo',
            'supported_durations':[4,6,8],'supported_sizes':['1280x720'],'supported_resolutions':['720p'],
            'supported_aspect_ratios':['16:9'],'supported_frame_images':['first_frame'],
            'generate_audio':True,'seed':True,'pricing_skus':{'duration_seconds_without_audio':'0.10'}}]}))
        original=await store.create(CreateVideoProjectRequest(duration_sec=12))
        self.project=store.update(original.id,UpdateVideoProjectRequest(revision=original.revision,
            provider_config=OpenRouterVideoProviderConfig(model_id='google/veo-3.1-fast',size='1280x720'),shots=[VideoShotDraft(id='c'*32,start_sec=0,seconds=4,prompt='A quiet meadow')]))
        self.shot=self.project.shots[0]

    async def asyncTearDown(self) -> None:
        for task in tuple(render._tasks.values()): task.cancel()
        await asyncio.gather(*tuple(render._tasks.values()),return_exceptions=True)
        render._tasks.clear();render._cloud_projects.clear()

    async def test_quote_blocks_unsupported_duration_and_preserves_project(self) -> None:
        from app import video_cloud
        with self.assertRaises(OpenRouterError) as error:
            video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=5))
        self.assertEqual(error.exception.code,'video_parameters_unsupported')
        self.assertIsNone(store.get(self.project.id).job)

    async def test_domain_intent_precedes_post_and_remote_id_precedes_poll(self) -> None:
        from app import video_cloud
        seen=asyncio.Event(); hold=asyncio.Event(); calls=[]
        project_id=self.project.id
        class Client:
            async def submit_video(inner,receipt_id: str,body: OpenRouterVideoRequest,quote_id: str) -> OpenRouterVideoJob:
                current=store.get(project_id).shots[0].variants[-1]
                self.assertIsNotNone(current.cloud)
                if current.cloud is None: raise AssertionError('missing durable cloud intent')
                self.assertEqual(current.cloud.receipt.id,receipt_id)
                ledger.begin_submit(receipt_id,quote_id,current.cloud.receipt.request_fingerprint)
                ledger.update(receipt_id,state='submitted',remote_id='remote-job-1')
                calls.append('POST')
                return OpenRouterVideoJob(id='remote-job-1',status='pending')
            async def poll_video(inner,remote_id: str) -> OpenRouterVideoJob:
                current=store.get(project_id).shots[0].variants[-1]
                self.assertIsNotNone(current.cloud)
                if current.cloud is None: raise AssertionError('missing remote id')
                self.assertEqual(current.cloud.receipt.remote_id,remote_id)
                calls.append('GET');seen.set();await hold.wait()
                return OpenRouterVideoJob(id=remote_id,status='pending')
        quote=video_cloud.quote(project_id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        with patch.object(video_cloud,'OpenRouterClient',return_value=Client()):
            started=await video_cloud.submit(project_id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
            await asyncio.wait_for(seen.wait(),2)
            stopped=await render.cancel(project_id)
        self.assertEqual(calls,['POST','GET'])
        self.assertIsNotNone(stopped.job)
        self.assertEqual(stopped.shots[0].variants[-1].cloud.receipt.state,'canceled_tracking')
        self.assertNotIn(project_id,render._tasks)
        self.assertEqual(started.shots[0].variants[-1].cloud.receipt.state,'intent')

    async def test_ambiguous_submit_is_not_reposted_by_recovery_or_resume(self) -> None:
        from app import video_cloud
        calls=[]
        class Client:
            async def submit_video(inner,receipt_id: str,body: OpenRouterVideoRequest,quote_id: str) -> OpenRouterVideoJob:
                calls.append('POST'); record=ledger.get(receipt_id)
                ledger.begin_submit(receipt_id,quote_id,record.request_fingerprint)
                ledger.update(receipt_id,state='submission_unknown',error_code='submission_unknown')
                raise OpenRouterError('submission_unknown')
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        with patch.object(video_cloud,'OpenRouterClient',return_value=Client()):
            await video_cloud.submit(self.project.id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
            await asyncio.gather(*tuple(render._tasks.values()))
            await render.recover()
            current=store.get(self.project.id)
            with self.assertRaisesRegex(store.VideoProjectError,'cloud_submission_unknown'):
                await video_cloud.resume(self.project.id,VideoCloudResumeRequest(revision=current.revision,variant_id=current.shots[0].variants[-1].id))
        self.assertEqual(calls,['POST'])
        self.assertEqual(current.shots[0].variants[-1].cloud.receipt.state,'submission_unknown')

    async def test_completed_cloud_picture_uses_native_geometry_and_exports_original_cast_pcm_without_ltx(self) -> None:
        import hashlib
        import math
        import subprocess
        import wave
        from array import array
        import httpx
        from app import video_cloud, openrouter_settings as settings
        from app.openrouter_client import OpenRouterClient
        from app.openrouter_contracts import OpenRouterSettingsRequest, OpenRouterKeyRequest
        from app.video_contracts import VideoSpeechClip, VideoExportRequest, VideoExportSettings, ApproveVideoVariantRequest
        from app.video_media import probe_media
        provider_file=self.root/'remote.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=30:d=4','-f','lavfi','-i','sine=frequency=900:duration=4','-c:v','libx264','-c:a','aac','-shortest',str(provider_file)],check=True,timeout=20)
        document=store.load(self.project.id);document.project.duration_sec=4
        speech=store.artifact(self.project.id,'speech/accepted.wav');speech.parent.mkdir(parents=True)
        original=array('h',(int(math.sin(2*math.pi*330*index/16000)*10000) for index in range(64000)))
        with wave.open(str(speech),'wb') as handle:
            handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000);handle.writeframes(original.tobytes())
        digest=hashlib.sha256(speech.read_bytes()).hexdigest()
        document.speech_path='speech/accepted.wav'
        document.project.speech_clip=VideoSpeechClip(id='f'*32,name='Original cast',bytes=speech.stat().st_size,duration_sec=4,sha256=digest)
        document.project.export_settings.attach_speech=True;store.save(document)
        calls=[]
        def transport(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            if request.method=='POST':
                self.assertNotIn(b'reference_audio',request.content)
                self.assertIn(b'"generate_audio":false',request.content)
                return httpx.Response(202,json={'id':'real-mock-job','status':'pending'})
            if request.url.path.endswith('/content'):
                return httpx.Response(200,headers={'content-type':'video/mp4'},content=provider_file.read_bytes())
            return httpx.Response(200,json={'id':'real-mock-job','status':'completed','usage':{'cost':.41}})
        self.enterContext(patch.object(settings,'SETTINGS_PATH',self.root/'settings.json'))
        self.enterContext(patch.object(settings.credentials,'native_backend',return_value=None))
        settings.credentials.clear_session();self.addCleanup(settings.credentials.clear_session)
        settings.save(OpenRouterSettingsRequest(enabled=True,estimate_limit_usd=1))
        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48,persist=False))
        client=OpenRouterClient(transport=httpx.MockTransport(transport))
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        with patch.object(video_cloud,'OpenRouterClient',return_value=client):
            await video_cloud.submit(self.project.id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
            await asyncio.gather(*tuple(render._tasks.values()))
        completed=store.get(self.project.id);variant=completed.shots[0].variants[-1]
        self.assertEqual(variant.status,'ready');self.assertIsNotNone(variant.cloud)
        if variant.cloud is None: raise AssertionError('missing provenance')
        self.assertEqual(variant.cloud.receipt.state,'completed');self.assertEqual(variant.cloud.receipt.actual_cost_usd,.41)
        self.assertAlmostEqual(variant.cloud.source_duration_sec or 0,4,places=1)
        picture=await probe_media(render.variant_path(self.project.id,self.shot.id,variant.id))
        self.assertEqual((picture.width,picture.height),(1280,720));self.assertEqual(picture.audio_duration,0)
        approved=await render.approve(self.project.id,self.shot.id,ApproveVideoVariantRequest(revision=completed.revision,variant_id=variant.id))
        await render.export(self.project.id,VideoExportRequest(revision=approved.revision,settings=VideoExportSettings(attach_speech=True)))
        await asyncio.gather(*tuple(render._tasks.values()))
        finished=store.get(self.project.id);self.assertEqual(finished.job.status,'ready')
        output=render.output_file(self.project.id);info=await probe_media(output)
        self.assertAlmostEqual(info.video_duration,4,places=1);self.assertGreater(info.audio_duration,3.9)
        decoded=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-map','0:a:0','-ar','16000','-ac','1','-f','s16le','-'],check=True,capture_output=True,timeout=20).stdout
        recovered=array('h');recovered.frombytes(decoded[:len(original)*2])
        count=min(len(original),len(recovered));dot=sum(original[index]*recovered[index] for index in range(count))
        norm=math.sqrt(sum(value*value for value in original[:count])*sum(value*value for value in recovered[:count]))
        self.assertGreater(dot/norm,.98,'export must preserve the cast, not provider-generated audio')
        self.assertEqual(hashlib.sha256(speech.read_bytes()).hexdigest(),digest)
        self.assertEqual(calls,['POST','GET','GET'])
        self.assertNotIn(self.project.id,render._tasks);self.assertIsNone(store.load(self.project.id).worker)
        invalid=store.load(self.project.id)
        invalid_cloud=invalid.project.shots[0].variants[-1].cloud
        if invalid_cloud is None: raise AssertionError('missing receipt')
        invalid_cloud.receipt.state='intent';store.save(invalid)
        with self.assertRaisesRegex(store.VideoProjectError,'stale_variant'):
            await render.approve(self.project.id,self.shot.id,ApproveVideoVariantRequest(revision=finished.revision,variant_id=variant.id))

    async def test_cancel_before_worker_starts_releases_cloud_admission_and_never_posts(self) -> None:
        from app import video_cloud
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        await video_cloud.submit(self.project.id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
        stopped=await render.cancel(self.project.id)
        self.assertNotIn(self.project.id,render._cloud_projects)
        self.assertNotIn(self.project.id,render._tasks)
        self.assertEqual(stopped.shots[0].variants[-1].status,'cancelled')
        self.assertEqual(stopped.shots[0].variants[-1].cloud.receipt.state,'intent')

    async def test_shutdown_and_startup_follow_saved_remote_id_without_paid_resubmission(self) -> None:
        from app import video_cloud
        first=asyncio.Event();second=asyncio.Event();hold=asyncio.Event();calls=[]
        class Client:
            async def submit_video(inner,receipt_id: str,body: OpenRouterVideoRequest,quote_id: str) -> OpenRouterVideoJob:
                calls.append('POST');record=ledger.get(receipt_id)
                ledger.begin_submit(receipt_id,quote_id,record.request_fingerprint)
                ledger.update(receipt_id,state='submitted',remote_id='saved-remote-job')
                return OpenRouterVideoJob(id='saved-remote-job',status='pending')
            async def poll_video(inner,remote_id: str) -> OpenRouterVideoJob:
                self.assertEqual(remote_id,'saved-remote-job');calls.append('GET')
                (first if len(calls)==2 else second).set();await hold.wait()
                return OpenRouterVideoJob(id=remote_id,status='pending')
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        with patch.object(video_cloud,'OpenRouterClient',return_value=Client()):
            await video_cloud.submit(self.project.id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
            await asyncio.wait_for(first.wait(),2);await render.shutdown()
            self.assertEqual(store.get(self.project.id).shots[0].variants[-1].cloud.receipt.state,'submitted')
            await render.recover();await asyncio.wait_for(second.wait(),2);await render.cancel(self.project.id)
        self.assertEqual(calls,['POST','GET','GET'])

    async def test_another_project_cannot_spend_a_quote_for_identical_provider_inputs(self) -> None:
        from app import video_cloud
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        duplicate=store.duplicate(self.project.id,VideoRevisionRequest(revision=self.project.revision))
        with self.assertRaises(OpenRouterError) as error:
            await video_cloud.submit(duplicate.id,VideoCloudSubmitRequest(revision=duplicate.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
        self.assertEqual(error.exception.code,'quote_mismatch')
        self.assertIsNone(store.get(duplicate.id).job)
        self.assertEqual(ledger.list_receipts(),[])

    async def test_restart_adopts_a_verified_cloud_clip_if_poster_work_was_interrupted(self) -> None:
        import hashlib
        import subprocess
        from app import video_cloud
        from app.openrouter_client import VideoDownload
        raw=self.root/'recoverable.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=1280x720:r=24:d=4','-c:v','libx264',str(raw)],check=True,timeout=20)
        calls=[]
        class Client:
            async def submit_video(inner,receipt_id: str,body: OpenRouterVideoRequest,quote_id: str) -> OpenRouterVideoJob:
                calls.append('POST');record=ledger.get(receipt_id);ledger.begin_submit(receipt_id,quote_id,record.request_fingerprint)
                ledger.update(receipt_id,state='submitted',remote_id='saved-output')
                return OpenRouterVideoJob(id='saved-output',status='pending')
            async def poll_video(inner,remote_id: str) -> OpenRouterVideoJob:
                calls.append('GET');return OpenRouterVideoJob(id=remote_id,status='completed')
            async def download_video(inner,remote_id: str,dest: Path) -> VideoDownload:
                calls.append('DOWNLOAD');data=raw.read_bytes();dest.write_bytes(data)
                return VideoDownload(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        quote=video_cloud.quote(self.project.id,VideoCloudQuoteRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4))
        with patch.object(video_cloud,'OpenRouterClient',return_value=Client()):
            with patch.object(render,'_poster',side_effect=asyncio.CancelledError):
                await video_cloud.submit(self.project.id,VideoCloudSubmitRequest(revision=self.project.revision,shot_id=self.shot.id,remote_duration_sec=4,quote_id=quote.quote.id,transfers_confirmed=True))
                await asyncio.gather(*tuple(render._tasks.values()))
            before=list(calls);await render.recover()
            self.assertEqual(store.get(self.project.id).shots[0].variants[-1].status,'ready')
            self.assertEqual(calls,before,'a checked local cloud clip must not require the remote service again')
            self.assertEqual(store.get(self.project.id).shots[0].variants[-1].cloud.receipt.state,'completed')
