"""Two reviewed paid stages with temporary CPU media and mocked OpenRouter."""
from __future__ import annotations
import asyncio
import base64
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import UploadFile
from PIL import Image
from app.character_swap_contracts import CharacterSwapJob, CharacterSwapRevisionRequest, RenameCharacterSwapRequest, ReviewCharacterSwapRequest, SubmitCharacterSwapRequest
from app import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings, openrouter_credentials as credentials, openrouter_client as client_module
from app.openrouter_contracts import OpenRouterSettingsRequest, OpenRouterKeyRequest
from app.openrouter_client import OpenRouterClient
from app.openrouter_editing_test import image_metadata, video_metadata
from app.clip_host_contracts import HostedClip, ClipHostStatus

class CharacterSwapRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_exposes_character_swap_readiness(self) -> None:
        self.enterContext(patch.object(credentials, 'native_backend', return_value=None))
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            response = await client.get('/api/videos/character-swaps/readiness')
        self.assertEqual(response.status_code, 200)
        self.assertIn('configured', response.json())
        self.assertIn('ffmpeg_ready', response.json())

    async def test_cross_origin_scene_handoff_and_paid_submission_are_rejected(self) -> None:
        from app.main import app
        from app import character_swaps
        with patch.object(character_swaps, 'create_from_scene') as prepare, patch.object(character_swaps, 'submit') as submit, \
                patch.object(character_swaps, 'review') as review:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
                response = await client.post('/api/videos/character-swaps/from-scene',
                    headers={'Origin': 'https://outside.example'}, data={'scene_id': 'a'*32, 'cut_id': 'b'*32},
                    files={'image': ('character.png', b'private', 'image/png')})
                self.assertEqual(response.status_code, 403)
                response = await client.post('/api/videos/character-swaps/'+'a'*32+'/submit',
                    headers={'Origin': 'https://outside.example'}, json={'revision': 1, 'quote_id': 'b'*32,
                        'transfers_confirmed': True, 'consent_confirmed': True})
                self.assertEqual(response.status_code, 403)
                response = await client.post('/api/videos/character-swaps/'+'a'*32+'/review',
                    headers={'Origin': 'https://outside.example'}, json={'revision': 1, 'review_fingerprint': 'b'*64})
                self.assertEqual(response.status_code, 403)
        prepare.assert_not_called()
        submit.assert_not_called()
        review.assert_not_called()

    async def test_multipart_overflow_is_rejected_before_handoff_and_closes_partial_spool(self) -> None:
        from app.main import app
        from app import character_swaps
        files: list[tempfile.SpooledTemporaryFile[bytes]] = []
        def spool(*, max_size: int = 0) -> tempfile.SpooledTemporaryFile[bytes]:
            file = tempfile.SpooledTemporaryFile(max_size=max_size)
            files.append(file)
            return file
        async def chunks() -> AsyncIterator[bytes]:
            yield b'--swap\r\nContent-Disposition: form-data; name="image"; filename="character.png"\r\nContent-Type: image/png\r\n\r\n'+b'a'*100
            yield b'b'*66000+b'\r\n--swap--\r\n'
        with patch.object(character_swaps, 'MAX_IMAGE_BYTES', 1), patch.object(character_swaps, 'MAX_VIDEO_BYTES', 1), \
                patch.object(character_swaps, 'create_from_scene') as prepare, \
                patch('starlette.formparsers.SpooledTemporaryFile', side_effect=spool):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
                for suffix in ('/from-scene', ''):
                    response = await client.post('/api/videos/character-swaps'+suffix, content=chunks(),
                        headers={'Content-Type': 'multipart/form-data; boundary=swap'})
                    self.assertEqual(response.status_code, 413)
        prepare.assert_not_called()
        self.assertEqual(len(files), 2)
        self.assertTrue(all(file.closed for file in files))

class CharacterSwapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import character_swaps
        self.swap = character_swaps
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(self.swap, 'DATA_DIR', self.root))
        self.enterContext(patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}))
        self.enterContext(patch.object(catalog, 'CATALOG_PATH', self.root/'catalog.json'))
        self.enterContext(patch.object(catalog, '_QUOTES', {}))
        self.enterContext(patch.object(ledger, 'REQUESTS_ROOT', self.root/'requests'))
        self.enterContext(patch.object(settings, 'SETTINGS_PATH', self.root/'settings.json'))
        self.enterContext(patch.object(credentials, 'native_backend', return_value=None))
        self.enterContext(patch.object(client_module, '_STOPPING', False))
        credentials.clear_session()
        settings.save(OpenRouterSettingsRequest(enabled=True, estimate_limit_usd=5))
        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48, persist=False))
        catalog.publish([catalog.normalize_image_edit(image_metadata()).model_copy(update={'supported_aspect_ratios':['16:9','3:2']}), *catalog.normalize_video(video_metadata(ratios=['16:9','3:2']))])
        await self.swap.recover()
        self.source = self.root/'fixture.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=96x64:r=24:d=4',
            '-f','lavfi','-i','sine=frequency=440:duration=4','-c:v','libx264','-c:a','aac',
            '-pix_fmt','yuv420p',str(self.source)],check=True)
        self.image=io.BytesIO()
        Image.new('RGB',(96,64),'blue').save(self.image,format='PNG')
        self.calls: list[str]=[]
        self.shared: list[str] = []
        self.released: list[str] = []
        self.enterContext(patch.object(self.swap.clip_hosting, 'status', return_value=ClipHostStatus(
            enabled=True, configured=True, binary_available=True)))
        self.enterContext(patch.object(self.swap.clip_hosting, 'ensure_ready', return_value=None))
        async def share(receipt_id: str, path: Path, expected_sha256: str) -> HostedClip:
            from app.video_io import hash_file
            self.assertEqual(path, self.swap.artifact(self.current_id, 'input.mp4'))
            self.assertEqual(await hash_file(path), expected_sha256)
            self.assertEqual(ledger.get(receipt_id).state, 'intent')
            self.assertEqual(self.swap.get(self.current_id).phase, 'sharing_clip')
            self.shared.append(receipt_id)
            return HostedClip(receipt_id=receipt_id, source_sha256=expected_sha256,
                url='https://approved-clip.trycloudflare.com/clip/'+'a'*64+'.mp4',
                expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        async def release(receipt_id: str) -> None:
            self.released.append(receipt_id)
        self.enterContext(patch.object(self.swap.clip_hosting, 'stage_clip', side_effect=share))
        self.enterContext(patch.object(self.swap.clip_hosting, 'release', side_effect=release))
        self.gate: asyncio.Event | None=None
        self.started=asyncio.Event()
        self.unknown=False
        async def serve(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.method+' '+request.url.path)
            if request.method=='POST':
                self.started.set()
                if self.gate is not None: await self.gate.wait()
                if self.unknown: raise httpx.ReadTimeout('mock lost response')
                if request.url.path.endswith('/images'):
                    frame=self.swap.artifact(self.current_id,'source-frame.png').read_bytes()
                    return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(frame).decode()}]})
                from app.contracts import JsonObject
                from pydantic import TypeAdapter
                wire = TypeAdapter(JsonObject).validate_json(request.content)
                self.assertEqual(wire['input_references'], [{'type':'video_url','video_url':{'url':
                    'https://approved-clip.trycloudflare.com/clip/'+'a'*64+'.mp4'}}])
                return httpx.Response(200,json={'id':'remote-request-123','status':'completed'})
            if request.url.path.endswith('/content'):
                return httpx.Response(200,content=self.swap.artifact(self.current_id,'input.mp4').read_bytes(),headers={'content-type':'video/mp4'})
            return httpx.Response(200,json={'id':'remote-request-123','status':'completed'})
        self.client=OpenRouterClient(transport=httpx.MockTransport(serve))
        self.enterContext(patch.object(self.swap,'OpenRouterClient',return_value=self.client))
        self.enterContext(patch.object(self.client,'refresh_catalog',return_value=catalog.load()))
        self.current_id=''

    async def asyncTearDown(self) -> None:
        await self.swap.shutdown()
        credentials.clear_session()

    async def prepare(self) -> CharacterSwapJob:
        job=await self.swap.create(UploadFile(io.BytesIO(self.source.read_bytes()),filename='source.mp4'),
            UploadFile(io.BytesIO(self.image.getvalue()),filename='character.png'),
            name='Character test',start_sec=1,seconds=2,seed=42)
        self.current_id=job.id
        return job

    async def reviewed(self, job: CharacterSwapJob) -> CharacterSwapJob:
        return await self.swap.review(job.id, ReviewCharacterSwapRequest(
            revision=job.revision, review_fingerprint=self.swap.get(job.id).review_fingerprint))

    async def quoted(self, job: CharacterSwapJob, *, reviewed: bool = True) -> CharacterSwapJob:
        if reviewed and job.provider == 'openrouter' and not job.preparation_reviewed:
            job = await self.reviewed(job)
        return await self.swap.quote(job.id,CharacterSwapRevisionRequest(revision=job.revision))

    def body(self, job: CharacterSwapJob, **changes: bool) -> SubmitCharacterSwapRequest:
        assert job.quote is not None
        return SubmitCharacterSwapRequest(revision=job.revision,quote_id=job.quote.id,
            transfers_confirmed=changes.get('transfers_confirmed',True),consent_confirmed=changes.get('consent_confirmed',True))

    async def finish(self) -> None:
        await asyncio.wait_for(asyncio.gather(*self.swap._tasks.values()),30)

    async def frame(self) -> CharacterSwapJob:
        job=await self.quoted(await self.prepare())
        await self.swap.submit(job.id,self.body(job))
        await self.finish()
        return self.swap.get(job.id)

    async def test_paid_submission_requires_saved_input_review(self) -> None:
        job = await self.quoted(await self.prepare(), reviewed=False)
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.swap.submit(job.id, self.body(job))
        self.assertEqual(failure.exception.code, 'preparation_review_required')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.shared, [])
        self.assertEqual(ledger.list_receipts(), [])

    async def test_preparation_retains_five_changing_frames_and_exact_prompt(self) -> None:
        job = await self.prepare()
        response = job.model_dump()
        self.assertEqual(response.get('filmstrip_url'), f'/api/videos/character-swaps/{job.id}/filmstrip')
        with Image.open(self.swap.directory(job.id) / 'filmstrip.png') as strip:
            self.assertEqual(strip.size, (800, 90))
            self.assertNotEqual(strip.crop((0, 0, 160, 90)).tobytes(), strip.crop((640, 0, 800, 90)).tobytes())
        self.assertEqual(response.get('prompt'), self.swap._request(self.swap.load(job.id)).prompt)
        self.assertFalse(response.get('preparation_reviewed'))

    async def test_local_review_route_is_available_without_provider_setup(self) -> None:
        from app.main import app
        job = await self.prepare()
        settings.save(OpenRouterSettingsRequest(enabled=False))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            response = await client.post(f'/api/videos/character-swaps/{job.id}/review',
                json={'revision': job.revision, 'review_fingerprint': job.review_fingerprint})
            strip = await client.get(job.filmstrip_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['preparation_reviewed'])
        self.assertEqual((strip.status_code, strip.headers['content-type']), (200, 'image/png'))
        self.assertEqual(strip.headers['cache-control'], 'no-store')
        self.assertEqual(self.calls, [])

    async def test_saved_input_review_survives_restart_and_price_refresh(self) -> None:
        job = await self.reviewed(await self.prepare())
        self.assertTrue(job.preparation_reviewed)
        self.assertEqual(self.swap.load(job.id).reviewed_fingerprint, job.review_fingerprint)
        await self.swap.shutdown()
        await self.swap.recover()
        restored = self.swap.get(job.id)
        self.assertTrue(restored.preparation_reviewed)
        quoted = await self.quoted(restored, reviewed=False)
        self.assertTrue(quoted.preparation_reviewed)
        self.assertEqual(quoted.review_fingerprint, job.review_fingerprint)
        self.assertEqual(self.calls, [])

    async def test_review_rejects_wrong_fingerprint_and_stale_revision(self) -> None:
        job = await self.prepare()
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.swap.review(job.id, ReviewCharacterSwapRequest(revision=job.revision, review_fingerprint='f'*64))
        self.assertEqual(failure.exception.code, 'review_mismatch')
        self.assertFalse(self.swap.get(job.id).preparation_reviewed)
        accepted = await self.reviewed(job)
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.reviewed(job)
        self.assertEqual(failure.exception.code, 'revision_conflict')
        self.assertEqual(self.swap.get(job.id).revision, accepted.revision)

    async def test_prompt_change_invalidates_saved_review_before_spending(self) -> None:
        job = await self.quoted(await self.prepare())
        with patch.object(self.swap, '_FRAME_PROMPT', self.swap._FRAME_PROMPT + ' Keep the hat.'):
            latest = self.swap.get(job.id)
            self.assertFalse(latest.preparation_reviewed)
            self.assertNotEqual(latest.review_fingerprint, job.review_fingerprint)
            self.assertEqual(latest.prompt, self.swap._request(self.swap.load(job.id)).prompt)
            with self.assertRaises(self.swap.CharacterSwapError) as failure:
                await self.swap.review(job.id, ReviewCharacterSwapRequest(revision=job.revision,
                    review_fingerprint=job.review_fingerprint))
            self.assertEqual(failure.exception.code, 'review_mismatch')
            with self.assertRaises(self.swap.CharacterSwapError) as failure:
                await self.swap.submit(job.id, self.body(job))
            self.assertEqual(failure.exception.code, 'preparation_review_required')
        self.assertEqual(self.calls, [])
        self.assertEqual(ledger.list_receipts(), [])

    async def test_video_stage_requires_review_of_its_edited_frame_and_prompt(self) -> None:
        job = await self.frame()
        self.assertFalse(job.preparation_reviewed)
        self.assertEqual(job.prompt, self.swap._request(self.swap.load(job.id)).prompt)
        job = await self.quoted(job, reviewed=False)
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.swap.submit(job.id, self.body(job))
        self.assertEqual(failure.exception.code, 'preparation_review_required')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.shared, [])

    async def test_changed_filmstrip_blocks_review_and_paid_submission(self) -> None:
        job = await self.quoted(await self.prepare())
        self.swap.artifact(job.id, 'filmstrip.png').write_bytes(b'changed preview')
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.reviewed(job)
        self.assertEqual(failure.exception.code, 'source_changed')
        with self.assertRaises(self.swap.CharacterSwapError) as failure:
            await self.swap.submit(job.id, self.body(job))
        self.assertEqual(failure.exception.code, 'source_changed')
        self.assertEqual(self.calls, [])
        self.assertEqual(ledger.list_receipts(), [])

    async def test_interrupted_review_write_preserves_previous_metadata(self) -> None:
        job = await self.prepare()
        path = self.swap.artifact(job.id, 'job.json')
        before = path.read_bytes()
        with patch('app.atomic_files.os.replace', side_effect=OSError('simulated interrupted write')):
            with self.assertRaises(OSError):
                await self.reviewed(job)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.swap.get(job.id).preparation_reviewed)
        self.assertEqual(list(path.parent.glob('.job.json.*.tmp')), [])

    async def test_competing_reviews_only_accept_one_current_revision(self) -> None:
        job = await self.prepare()
        # Contention binds an asyncio lock to this test's isolated event loop.
        with patch.object(self.swap, 'admission_lock', asyncio.Lock()):
            results = await asyncio.gather(self.reviewed(job), self.reviewed(job), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, CharacterSwapJob) for result in results), 1)
        errors = [result for result in results if isinstance(result, self.swap.CharacterSwapError)]
        self.assertEqual([error.code for error in errors], ['revision_conflict'])
        self.assertEqual(self.swap.get(job.id).revision, job.revision + 1)

    async def test_saved_job_without_new_review_fields_remains_reviewable(self) -> None:
        from app.atomic_files import read_object, write_object
        job = await self.prepare()
        path = self.swap.artifact(job.id, 'job.json')
        document = read_object(path)
        metadata = document['job']
        assert isinstance(metadata, dict)
        for field in ('filmstrip_url', 'prompt', 'review_fingerprint', 'preparation_reviewed'):
            metadata.pop(field, None)
        document.pop('filmstrip_sha256')
        document.pop('reviewed_fingerprint')
        write_object(path, document)
        self.swap.artifact(job.id, 'filmstrip.png').unlink()
        restored = self.swap.get(job.id)
        self.assertFalse(restored.preparation_reviewed)
        self.assertEqual(restored.filmstrip_url, '')
        reviewed = await self.reviewed(restored)
        self.assertTrue(reviewed.preparation_reviewed)
        self.assertTrue(self.swap.artifact(job.id, 'source.video').is_file())

    async def test_http_submission_cannot_bypass_saved_input_review(self) -> None:
        from app.main import app
        job = await self.quoted(await self.prepare(), reviewed=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            response = await client.post(f'/api/videos/character-swaps/{job.id}/submit', json=self.body(job).model_dump())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'preparation_review_required'})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.shared, [])
        self.assertEqual(ledger.list_receipts(), [])

    async def test_cloud_generation_is_unavailable_without_private_clip_hosting(self) -> None:
        self.assertTrue(settings.status().credential_configured)
        with patch.object(self.swap.clip_hosting, 'status', return_value=ClipHostStatus()):
            self.assertFalse(self.swap.readiness().configured)
            job = await self.prepare()
            with self.assertRaises(self.swap.CharacterSwapError) as failure:
                await self.quoted(job)
        self.assertEqual(failure.exception.code, 'clip_host_missing')
        self.assertEqual(self.calls, [])
        self.assertEqual(ledger.list_receipts(), [])

    async def test_only_an_approved_video_step_shares_and_then_releases_the_silent_clip(self) -> None:
        job = await self.frame()
        self.assertEqual(self.shared, [])
        job = await self.quoted(job)
        self.assertEqual(self.shared, [])
        self.assertIsNotNone(job.quote)
        with self.assertRaises(self.swap.CharacterSwapError):
            await self.swap.submit(job.id, self.body(job, transfers_confirmed=False))
        self.assertEqual(self.shared, [])
        await self.swap.submit(job.id, self.body(job))
        await self.finish()
        result = self.swap.get(job.id)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(self.shared, [result.video_receipt_id])
        self.assertEqual(self.released, self.shared)

    async def test_sharing_failure_is_not_billed_and_reuses_the_reviewed_frame_with_fresh_approval(self) -> None:
        from app.clip_hosting import ClipHostError
        job = await self.quoted(await self.frame())
        edited = self.swap.artifact(job.id, 'edited-frame.png').read_bytes()
        with patch.object(self.swap.clip_hosting, 'stage_clip', side_effect=ClipHostError('clip_host_connection_failed')):
            await self.swap.submit(job.id, self.body(job))
            await self.finish()
        failed = self.swap.get(job.id)
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.error_code, 'clip_host_connection_failed')
        self.assertFalse(failed.resume_available)
        self.assertTrue(failed.requote_available)
        receipt = ledger.get(failed.video_receipt_id or '')
        self.assertEqual(receipt.state, 'intent')
        self.assertNotIn('POST /api/v1/videos', self.calls)
        self.assertIn(receipt.id, self.released)
        fresh = await self.quoted(failed)
        self.assertEqual(self.swap.artifact(job.id, 'edited-frame.png').read_bytes(), edited)
        self.assertNotEqual(fresh.quote.id if fresh.quote else '', job.quote.id if job.quote else '')
        self.assertEqual(self.calls.count('POST /api/v1/images'), 1)

    async def test_stop_during_sharing_is_safe_to_requote_and_releases_the_grant(self) -> None:
        job = await self.quoted(await self.frame())
        started = asyncio.Event()
        async def share(receipt_id: str, path: Path, expected_sha256: str) -> HostedClip:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError('cancelled share must not resume')
        with patch.object(self.swap.clip_hosting, 'stage_clip', side_effect=share):
            await self.swap.submit(job.id, self.body(job))
            await asyncio.wait_for(started.wait(), 2)
            stopping = self.swap.get(job.id)
            stopped = await self.swap.stop(job.id, CharacterSwapRevisionRequest(revision=stopping.revision))
        self.assertEqual(stopped.status, 'stopped')
        self.assertTrue(stopped.requote_available)
        self.assertFalse(stopped.resume_available)
        self.assertEqual(ledger.get(stopped.video_receipt_id or '').state, 'intent')
        self.assertIn(stopped.video_receipt_id, self.released)
        self.assertNotIn('POST /api/v1/videos', self.calls)

    async def test_unknown_video_submission_keeps_the_share_when_tracking_stops(self) -> None:
        job = await self.quoted(await self.frame())
        self.unknown = True
        await self.swap.submit(job.id, self.body(job))
        await self.finish()
        unknown = self.swap.get(job.id)
        self.assertEqual(unknown.status, 'submission_unknown')
        self.assertNotIn(unknown.video_receipt_id, self.released)
        await self.swap.stop(job.id, CharacterSwapRevisionRequest(revision=unknown.revision))
        self.assertNotIn(unknown.video_receipt_id, self.released)

    async def test_restart_before_the_paid_intent_does_not_share_or_report_unknown_billing(self) -> None:
        job = await self.quoted(await self.frame())
        assert job.quote is not None
        receipt = ledger.prepare('character-video-'+job.id, job.quote)
        document = self.swap.load(job.id)
        document.job.video_receipt_id = receipt.id
        document.job.status = 'submitting'
        document.job.phase = 'sharing_clip'
        self.swap.save(document)
        await self.swap.recover()
        await self.finish()
        recovered = self.swap.get(job.id)
        self.assertEqual(recovered.status, 'stopped')
        self.assertEqual(recovered.error_code, 'submission_not_sent')
        self.assertTrue(recovered.requote_available)
        self.assertFalse(recovered.resume_available)
        self.assertEqual(ledger.get(receipt.id).state, 'intent')
        self.assertEqual(self.shared, [])
        self.assertNotIn('POST /api/v1/videos', self.calls)

    async def test_local_preparation_retains_original_full_character_and_silent_input(self) -> None:
        from app.video_media import probe_media
        job=await self.prepare()
        self.assertEqual(job.stage,'frame')
        self.assertIsNone(job.quote)
        self.assertTrue(job.source_frame_url)
        self.assertFalse(self.calls)
        self.assertEqual(self.swap.artifact(job.id,'source.video').read_bytes(),self.source.read_bytes())
        self.assertEqual((await probe_media(self.swap.artifact(job.id,'input.mp4'))).audio_duration,0)
        with Image.open(self.swap.artifact(job.id,'reference.png')) as image:
            self.assertEqual(image.size,(96,64))

    async def test_retained_scene_handoff_prepares_without_browser_reupload(self) -> None:
        from app.video_scene_contracts import VideoSceneCut
        from app.video_io import hash_file
        cut = VideoSceneCut(id='b'*32, source_id='a'*32, name='Movie scene', start_sec=10,
            end_sec=14, seconds=4, status='ready', width=96, height=64, has_audio=True,
            created_at=self.swap.now(), updated_at=self.swap.now(),
            output_sha256=await hash_file(self.source), bytes=self.source.stat().st_size)
        image = UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png')
        with patch('app.video_scenes.resolve_cut', return_value=(self.source, cut)):
            job = await self.swap.create_from_scene(cut.source_id, cut.id, image,
                name='Scene swap', start_sec=1, seconds=2)
        self.assertEqual(self.swap.artifact(job.id, 'source.video').read_bytes(), self.source.read_bytes())
        self.assertTrue(job.has_audio)
        self.assertTrue(image.file.closed)
        self.assertFalse(self.calls)

    async def test_scene_handoff_rejects_interval_or_changed_bytes_and_closes_image(self) -> None:
        from app.video_scene_contracts import VideoSceneCut
        from app.video_io import hash_file
        cut = VideoSceneCut(id='b'*32, source_id='a'*32, name='Movie scene', start_sec=10,
            end_sec=14, seconds=4, status='ready', width=96, height=64, has_audio=True,
            created_at=self.swap.now(), updated_at=self.swap.now(),
            output_sha256=await hash_file(self.source), bytes=self.source.stat().st_size)
        for start_sec, expected_hash in [(3, cut.output_sha256), (1, '0'*64)]:
            with self.subTest(start_sec=start_sec):
                image = UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png')
                changed = cut.model_copy(update={'output_sha256': expected_hash})
                with patch('app.video_scenes.resolve_cut', return_value=(self.source, changed)):
                    with self.assertRaises(self.swap.CharacterSwapError):
                        await self.swap.create_from_scene(cut.source_id, cut.id, image,
                            name='Scene swap', start_sec=start_sec, seconds=2)
                self.assertTrue(image.file.closed)
        self.assertFalse(self.calls)
        self.assertEqual(self.swap.list_jobs().jobs, [])

    async def test_scene_cut_api_handoff_retains_audio_and_needs_no_cloud_key(self) -> None:
        from app import video_scenes
        from app.main import app
        from app.video_scene_contracts import VideoSceneCutRequest
        self.enterContext(patch.object(video_scenes, 'DATA_DIR', self.root))
        await video_scenes.recover()
        self.addAsyncCleanup(video_scenes.shutdown)
        source = await video_scenes.create(UploadFile(io.BytesIO(self.source.read_bytes()), filename='movie.mp4'))
        cut = await video_scenes.create_cut(source.id, VideoSceneCutRequest(name='Scene', start_sec=1, end_sec=4))
        await asyncio.wait_for(asyncio.gather(*video_scenes._cuts.values()), 15)
        path, ready = await video_scenes.resolve_cut(source.id, cut.id)
        self.assertEqual(ready.status, 'ready')
        credentials.clear_session()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
            response = await client.post('/api/videos/character-swaps/from-scene',
                data={'scene_id': source.id, 'cut_id': cut.id, 'name': 'Scene swap', 'start_sec': '0', 'seconds': '2'},
                files={'image': ('character.png', self.image.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        job = CharacterSwapJob.model_validate(response.json())
        self.assertEqual(self.swap.artifact(job.id, 'source.video').read_bytes(), path.read_bytes())
        self.assertTrue(job.has_audio)
        self.assertFalse(self.calls)
        retained = self.swap.artifact(job.id, 'source.video').read_bytes()
        await video_scenes.delete_cut(source.id, cut.id)
        self.assertEqual(self.swap.artifact(job.id, 'source.video').read_bytes(), retained)
        self.assertEqual(self.swap.get(job.id).status, 'prepared')
        self.assertEqual(video_scenes.get_source(source.id), source)
        self.assertFalse(self.calls)

    async def test_near_widescreen_scene_is_padded_and_valid_for_both_model_stages(self) -> None:
        from app import video_scenes
        from app.main import app
        from app.video_media import probe_media
        from app.video_scene_contracts import VideoSceneCutRequest
        movie = self.root/'near-widescreen.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=white:s=1280x718:r=24:d=4',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264', '-c:a', 'aac', str(movie)], check=True)
        self.enterContext(patch.object(video_scenes, 'DATA_DIR', self.root))
        await video_scenes.recover()
        self.addAsyncCleanup(video_scenes.shutdown)
        source = await video_scenes.create(UploadFile(io.BytesIO(movie.read_bytes()), filename=movie.name))
        cut = await video_scenes.create_cut(source.id, VideoSceneCutRequest(name='Near widescreen', start_sec=1, end_sec=4))
        await asyncio.wait_for(asyncio.gather(*video_scenes._cuts.values()), 15)
        path, ready = await video_scenes.resolve_cut(source.id, cut.id)
        self.assertEqual(ready.status, 'ready')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
            response = await client.post('/api/videos/character-swaps/from-scene',
                data={'scene_id': source.id, 'cut_id': cut.id, 'name': 'Widescreen swap', 'start_sec': '0', 'seconds': '2'},
                files={'image': ('character.png', self.image.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        job = CharacterSwapJob.model_validate(response.json())
        self.assertEqual((job.width, job.height), (854, 480))
        self.assertEqual(self.swap.artifact(job.id, 'source.video').read_bytes(), path.read_bytes())
        self.assertAlmostEqual((await probe_media(self.swap.artifact(job.id, 'selected.mp4'))).audio_duration, 2, delta=.1)
        with Image.open(self.swap.artifact(job.id, 'source-frame.png')) as frame:
            self.assertLess(min(frame.convert('L').getpixel((427, 0)), frame.convert('L').getpixel((427, 479))), 32)
            self.assertGreater(frame.convert('L').getpixel((427, 240)), 220)
        self.assertIsNotNone((await self.quoted(job)).quote)
        self.assertFalse(self.calls)

    async def test_anamorphic_source_prepares_its_display_aspect(self) -> None:
        movie = self.root/'anamorphic.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-vf', 'setsar=32/27',
            '-c:v', 'libx264', '-c:a', 'copy', str(movie)], check=True)
        job = await self.swap.create(UploadFile(io.BytesIO(movie.read_bytes()), filename=movie.name),
            UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png'), name='Anamorphic swap', start_sec=0, seconds=2)
        self.assertEqual((job.width, job.height), (854, 480))
        self.assertIsNotNone((await self.quoted(job)).quote)
        self.assertFalse(self.calls)

    async def test_unsupported_source_aspect_is_rejected_without_publishing(self) -> None:
        movie = self.root/'unsupported.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-vf', 'scale=128:64,setsar=1',
            '-c:v', 'libx264', '-c:a', 'copy', str(movie)], check=True)
        with self.assertRaises(self.swap.CharacterSwapError) as error:
            await self.swap.create(UploadFile(io.BytesIO(movie.read_bytes()), filename=movie.name),
                UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png'), name='Unsupported swap', start_sec=0, seconds=2)
        self.assertEqual(error.exception.code, 'edit_parameters_unsupported')
        self.assertEqual(self.swap.list_jobs().jobs, [])
        self.assertFalse(self.calls)

    async def test_invalid_scene_identifier_closes_upload_before_filesystem_resolution(self) -> None:
        image = UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png')
        with patch('app.video_scenes.resolve_cut') as resolve:
            with self.assertRaises(self.swap.CharacterSwapError) as error:
                await self.swap.create_from_scene('../private', 'b'*32, image,
                    name='Scene swap', start_sec=0, seconds=2)
        self.assertEqual(error.exception.code, 'bad_settings')
        self.assertTrue(image.file.closed)
        resolve.assert_not_called()

    async def test_two_separate_reviews_and_paid_posts_preserve_audio(self) -> None:
        job=await self.quoted(await self.prepare())
        assert job.quote is not None
        self.assertAlmostEqual(job.quote.estimated_usd,.04)
        old=self.body(job)
        await self.swap.submit(job.id,old)
        await self.finish()
        edited=self.swap.get(job.id)
        self.assertEqual((edited.status,edited.stage),('prepared','video'),edited.error_code)
        self.assertIsNone(edited.quote)
        self.assertTrue(edited.edited_frame_url)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(self.swap.CharacterSwapError): await self.swap.submit(job.id,old)
        video=await self.quoted(edited)
        assert video.quote is not None
        self.assertAlmostEqual(video.quote.estimated_usd,.56)
        await self.swap.submit(job.id,self.body(video))
        await self.finish()
        result=self.swap.get(job.id)
        self.assertEqual(result.status,'ready',result.error_code)
        self.assertEqual(sum(call.startswith('POST') for call in self.calls),2)
        def pcm(path: Path) -> bytes:
            return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-'],check=True,capture_output=True).stdout
        self.assertEqual(pcm(self.swap.artifact(job.id,'selected.mp4')),pcm(self.swap.artifact(job.id,'output.mp4')))
        self.assertEqual(ledger.get(result.frame_receipt_id or '').state,'completed')
        self.assertEqual(ledger.get(result.video_receipt_id or '').state,'completed')

    async def test_confirmations_and_changed_inputs_prevent_paid_requests(self) -> None:
        job=await self.quoted(await self.prepare())
        for field in ('consent_confirmed','transfers_confirmed'):
            with self.assertRaises(self.swap.CharacterSwapError): await self.swap.submit(job.id,self.body(job,**{field:False}))
        self.swap.artifact(job.id,'reference.png').write_bytes(b'changed')
        with self.assertRaises(self.swap.CharacterSwapError): await self.swap.submit(job.id,self.body(job))
        self.assertFalse(self.calls)

    async def test_quote_renewal_and_model_changes_invalidate_review(self) -> None:
        job=await self.quoted(await self.prepare())
        body=self.body(job)
        renewed=await self.quoted(job)
        self.assertNotEqual(renewed.quote,job.quote)
        with self.assertRaises(self.swap.CharacterSwapError): await self.swap.submit(job.id,body)
        catalog.publish([catalog.normalize_image_edit(image_metadata(.05)).model_copy(update={'supported_aspect_ratios':['16:9','3:2']}),*catalog.normalize_video(video_metadata(ratios=['16:9','3:2']))])
        with self.assertRaises(self.swap.CharacterSwapError): await self.swap.submit(job.id,self.body(renewed))
        self.assertFalse(self.calls)

    async def test_uncertain_paid_post_never_retries_after_reload(self) -> None:
        job=await self.quoted(await self.prepare())
        self.unknown=True
        await self.swap.submit(job.id,self.body(job)); await self.finish()
        self.assertEqual(self.swap.get(job.id).status,'submission_unknown')
        await self.swap.recover(); await self.finish()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(ledger.get(self.swap.get(job.id).frame_receipt_id or '').state,'submission_unknown')

    async def test_stop_during_paid_post_keeps_uncertain_outcome(self) -> None:
        job=await self.quoted(await self.prepare())
        self.gate=asyncio.Event()
        submitted=await self.swap.submit(job.id,self.body(job))
        await asyncio.wait_for(self.started.wait(),5)
        result=await self.swap.stop(job.id,CharacterSwapRevisionRequest(revision=submitted.revision))
        self.assertEqual(result.status,'submission_unknown')
        await self.swap.recover(); await self.finish()
        self.assertEqual(len(self.calls),1)

    async def test_completed_raw_frame_recovers_without_key_or_new_request(self) -> None:
        from app.video_io import hash_file
        job=await self.quoted(await self.prepare())
        await self.swap.submit(job.id,self.body(job)); await self.finish()
        document=self.swap.load(job.id)
        document.job.stage='frame'; document.job.status='running'; document.job.edited_frame_url=''
        document.job.model_id='bytedance-seed/seedream-4.5'
        document.raw_frame_sha256=await hash_file(self.swap.artifact(job.id,'generated-frame.original'))
        self.swap.save(document)
        credentials.clear_session()
        self.calls.clear()
        await self.swap.recover(); await self.finish()
        self.assertEqual((self.swap.get(job.id).stage,self.swap.get(job.id).status),('video','prepared'))
        self.assertFalse(self.calls)

    async def test_known_video_receipt_recovers_get_only_after_persist_gap(self) -> None:
        job=await self.quoted(await self.frame())
        receipt=ledger.prepare('character-video-'+job.id,job.quote)
        ledger.begin_submit(receipt.id,receipt.quote_id,receipt.request_fingerprint)
        ledger.update(receipt.id,state='submitted',remote_id='remote-request-123')
        document=self.swap.load(job.id)
        document.job.video_receipt_id=receipt.id; document.job.status='submission_unknown'
        self.swap.save(document)
        self.calls.clear()
        await self.swap.recover(); await self.finish()
        self.assertEqual(self.swap.get(job.id).status,'ready')
        self.assertTrue(self.calls)
        self.assertFalse(any(call.startswith('POST') for call in self.calls))

    async def test_missing_or_changed_diagnostic_strip_does_not_abandon_paid_video(self) -> None:
        for damage in ('missing', 'changed'):
            with self.subTest(damage=damage):
                job = await self.quoted(await self.frame())
                assert job.quote is not None
                receipt = ledger.prepare('character-video-'+job.id, job.quote)
                ledger.begin_submit(receipt.id, receipt.quote_id, receipt.request_fingerprint)
                ledger.update(receipt.id, state='submitted', remote_id='remote-request-123')
                document = self.swap.load(job.id)
                document.job.video_receipt_id = receipt.id
                document.job.status = 'submission_unknown'
                self.swap.save(document)
                strip = self.swap.artifact(job.id, 'filmstrip.png')
                if damage == 'missing':
                    strip.unlink()
                else:
                    strip.write_bytes(b'changed diagnostic')
                self.calls.clear()
                await self.swap.recover()
                await self.finish()
                result = self.swap.get(job.id)
                self.assertEqual(result.status, 'ready', result.error_code)
                self.assertTrue(self.calls)
                self.assertTrue(all(call.startswith('GET') for call in self.calls))
                self.assertEqual(self.shared, [])

    async def test_receipt_from_different_job_cannot_be_followed(self) -> None:
        job=await self.quoted(await self.frame())
        receipt=ledger.prepare('different-owner',job.quote)
        ledger.begin_submit(receipt.id,receipt.quote_id,receipt.request_fingerprint)
        ledger.update(receipt.id,state='submitted',remote_id='remote-request-123')
        document=self.swap.load(job.id)
        document.job.video_receipt_id=receipt.id; document.job.status='running'
        self.swap.save(document)
        self.calls.clear()
        await self.swap.recover(); await self.finish()
        self.assertFalse(self.calls)
        self.assertNotEqual(self.swap.get(job.id).status,'ready')

    async def test_legacy_fal_record_remains_readonly(self) -> None:
        job=await self.prepare()
        document=self.swap.load(job.id)
        document.job.provider='fal';document.job.model_id='fal-ai/wan/v2.2-14b/animate/replace'
        document.job.status='running';document.job.remote_id='old-fal-id'
        self.swap.save(document)
        await self.swap.recover();await self.finish()
        with self.assertRaises(self.swap.CharacterSwapError): await self.quoted(self.swap.get(job.id))
        with self.assertRaises(self.swap.CharacterSwapError): await self.swap.resume(job.id,CharacterSwapRevisionRequest(revision=job.revision))
        self.assertFalse(self.calls)
        self.assertTrue(self.swap.artifact(job.id,'source.video').exists())

    async def test_expired_quote_does_not_spend_and_can_be_renewed(self) -> None:
        import time
        job=await self.quoted(await self.prepare())
        assert job.quote is not None
        with patch.object(catalog.time,'time',return_value=time.time()+600):
            with self.assertRaises(self.swap.CharacterSwapError) as error:
                await self.swap.submit(job.id,self.body(job))
            self.assertEqual(error.exception.code,'quote_stale')
        renewed=await self.quoted(self.swap.get(job.id))
        self.assertIsNotNone(renewed.quote)
        self.assertFalse(self.calls)

    async def test_validated_pending_frame_and_video_recover_without_credentials(self) -> None:
        from app.video_io import hash_file
        job=await self.quoted(await self.prepare())
        assert job.quote is not None
        receipt=ledger.prepare('character-frame-'+job.id,job.quote)
        ledger.begin_submit(receipt.id,receipt.quote_id,receipt.request_fingerprint)
        ledger.update(receipt.id,state='completed')
        pending=self.swap.artifact(job.id,'frame.pending.png')
        shutil.copyfile(self.swap.artifact(job.id,'source-frame.png'),pending)
        document=self.swap.load(job.id)
        document.pending_frame_sha256=await hash_file(pending)
        document.job.frame_receipt_id=receipt.id;document.job.status='running'
        self.swap.save(document)
        credentials.clear_session()
        await self.swap.recover();await self.finish()
        self.assertEqual(self.swap.get(job.id).stage,'video')
        settings.set_credential(OpenRouterKeyRequest(api_key='sk-or-v1-'+'x'*48,persist=False))
        job=await self.quoted(self.swap.get(job.id))
        assert job.quote is not None
        receipt=ledger.prepare('character-video-'+job.id,job.quote)
        ledger.begin_submit(receipt.id,receipt.quote_id,receipt.request_fingerprint)
        ledger.update(receipt.id,state='completed',remote_id='remote-request-123')
        output=self.swap.artifact(job.id,'output.pending.mp4')
        shutil.copyfile(self.swap.artifact(job.id,'selected.mp4'),output)
        document=self.swap.load(job.id)
        document.pending_sha256=await hash_file(output)
        document.job.video_receipt_id=receipt.id;document.job.remote_id=receipt.remote_id
        document.job.status='running'
        self.swap.save(document)
        credentials.clear_session()
        await self.swap.recover();await self.finish()
        self.assertEqual(self.swap.get(job.id).status,'ready')
        self.assertFalse(self.calls)

    async def test_quote_shutdown_drains_request_and_prevents_stale_metadata(self) -> None:
        job=await self.prepare()
        started=asyncio.Event();finished=asyncio.Event()
        async def refresh() -> object:
            started.set()
            try: await asyncio.Event().wait()
            finally: finished.set()
        with patch.object(self.client,'refresh_catalog',side_effect=refresh):
            task=asyncio.create_task(self.quoted(job))
            await asyncio.wait_for(started.wait(),5)
            self.assertTrue(self.swap.work_busy())
            await self.swap.shutdown()
            await asyncio.gather(task,return_exceptions=True)
        self.assertTrue(finished.is_set())
        self.assertFalse(self.swap.work_busy())
        self.assertIsNone(self.swap.get(job.id).quote)

    async def test_expired_provider_video_is_terminal_and_not_resumable(self) -> None:
        from app.openrouter_contracts import OpenRouterVideoJob
        job=await self.quoted(await self.frame())
        with patch.object(self.client,'poll_video',return_value=OpenRouterVideoJob(id='remote-request-123',status='expired')):
            await self.swap.submit(job.id,self.body(job));await self.finish()
        result=self.swap.get(job.id)
        self.assertEqual((result.status,result.error_code),('failed','provider_video_expired'))
        self.assertFalse(result.resume_available)
        self.assertEqual(ledger.get(result.video_receipt_id or '').state,'failed')

    async def test_rejected_video_submission_is_failed_without_false_resume(self) -> None:
        job = await self.quoted(await self.frame())
        def reject(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.method + ' ' + request.url.path)
            return httpx.Response(400, json={'error': {'message': 'private provider details'}})
        with patch.object(self.client, 'transport', httpx.MockTransport(reject)):
            await self.swap.submit(job.id, self.body(job))
            await self.finish()
        result = self.swap.get(job.id)
        self.assertEqual((result.status, result.error_code), ('failed', 'provider_request_rejected'))
        self.assertFalse(result.resume_available)
        receipt = ledger.get(result.video_receipt_id or '')
        self.assertEqual((receipt.state, receipt.provider_http_status), ('failed', 400))
        self.assertEqual(self.calls.count('POST /api/v1/videos'), 1)
        with self.assertRaises(self.swap.CharacterSwapError) as error:
            await self.swap.resume(job.id, CharacterSwapRevisionRequest(revision=result.revision))
        self.assertEqual(error.exception.code, 'resume_unavailable')
        self.assertEqual(self.calls.count('POST /api/v1/videos'), 1)

    async def test_confirmed_rejection_gets_new_video_quote_without_repeating_frame(self) -> None:
        job = await self.quoted(await self.frame())
        edited = self.swap.artifact(job.id, 'edited-frame.png').read_bytes()
        with patch.object(self.client, 'transport', httpx.MockTransport(lambda request: httpx.Response(400))):
            await self.swap.submit(job.id, self.body(job))
            await self.finish()
        failed = self.swap.get(job.id)
        self.assertTrue(failed.requote_available)
        previous = ledger.get(failed.video_receipt_id or '')
        retained = (ledger.REQUESTS_ROOT / (previous.id + '.json')).read_bytes()
        before = list(self.calls)
        fresh = await self.quoted(failed)
        self.assertEqual((fresh.status, fresh.stage, fresh.error_code), ('prepared', 'video', ''))
        self.assertFalse(fresh.requote_available)
        self.assertGreater(fresh.revision, failed.revision)
        self.assertIsNotNone(fresh.quote)
        self.assertNotEqual(fresh.quote.id if fresh.quote else '', previous.quote_id)
        self.assertEqual(self.swap.artifact(job.id, 'edited-frame.png').read_bytes(), edited)
        self.assertEqual(self.calls, before)
        await self.swap.recover()
        self.assertEqual(self.swap.get(job.id).status, 'prepared')
        self.assertFalse(self.swap.work_busy())
        with self.assertRaises(self.swap.CharacterSwapError):
            await self.swap.submit(job.id, self.body(job))
        await self.swap.submit(job.id, self.body(fresh))
        await self.finish()
        ready = self.swap.get(job.id)
        self.assertEqual(ready.status, 'ready')
        self.assertNotEqual(ready.video_receipt_id, previous.id)
        self.assertEqual((ledger.REQUESTS_ROOT / (previous.id + '.json')).read_bytes(), retained)
        self.assertEqual(self.calls.count('POST /api/v1/images'), 1)

    async def test_requote_refuses_uncertain_foreign_missing_or_accepted_receipts(self) -> None:
        job = await self.quoted(await self.frame())
        with patch.object(self.client, 'transport', httpx.MockTransport(lambda request: httpx.Response(400))):
            await self.swap.submit(job.id, self.body(job))
            await self.finish()
        failed = self.swap.load(job.id)
        receipt = ledger.get(failed.job.video_receipt_id or '')
        for changes in ({'state': 'submission_unknown'}, {'provider_http_status': None},
                        {'provider_http_status': 500}, {'remote_id': 'already-accepted'},
                        {'owner_id': 'foreign-job'}, {'request_fingerprint': 'a' * 64}):
            with self.subTest(changes=changes):
                ledger._save(receipt.model_copy(update=changes))
                self.assertFalse(self.swap.get(job.id).requote_available)
                with self.assertRaises(self.swap.CharacterSwapError):
                    await self.quoted(failed.job)
                self.assertEqual(self.swap.load(job.id).job.status, 'failed')
        ledger._save(receipt)
        (ledger.REQUESTS_ROOT / (receipt.id + '.json')).write_text('{broken')
        self.assertFalse(self.swap.get(job.id).requote_available)
        with self.assertRaises(self.swap.CharacterSwapError):
            await self.quoted(failed.job)

    async def test_rejected_video_requote_requires_current_revision_and_intact_frame(self) -> None:
        job = await self.quoted(await self.frame())
        with patch.object(self.client, 'transport', httpx.MockTransport(lambda request: httpx.Response(400))):
            await self.swap.submit(job.id, self.body(job))
            await self.finish()
        failed = self.swap.get(job.id)
        count = len(catalog._QUOTES)
        with self.assertRaises(self.swap.CharacterSwapError) as stale:
            await self.quoted(job)
        self.assertEqual(stale.exception.code, 'revision_conflict')
        self.swap.artifact(job.id, 'edited-frame.png').write_bytes(b'changed frame')
        self.assertFalse(self.swap.get(job.id).requote_available)
        with self.assertRaises(self.swap.CharacterSwapError) as changed:
            await self.quoted(failed)
        self.assertEqual(changed.exception.code, 'already_submitted')
        self.assertEqual(len(catalog._QUOTES), count)
        self.assertEqual(self.swap.get(job.id).status, 'failed')
        self.assertEqual(self.calls.count('POST /api/v1/images'), 1)

    async def test_cancel_after_rejected_video_headers_keeps_confirmed_failure(self) -> None:
        job = await self.quoted(await self.frame())
        reading = asyncio.Event()
        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                reading.set()
                await asyncio.Event().wait()
                yield b'never'
        with patch.object(self.client, 'transport', httpx.MockTransport(lambda request: httpx.Response(400, stream=Waiting()))):
            await self.swap.submit(job.id, self.body(job))
            await asyncio.wait_for(reading.wait(), 5)
            stopped = await self.swap.stop(job.id, CharacterSwapRevisionRequest(revision=self.swap.get(job.id).revision))
        self.assertEqual((stopped.status, stopped.error_code), ('failed', 'provider_request_rejected'))
        self.assertTrue(stopped.requote_available)
        self.assertFalse(stopped.resume_available)
        receipt = ledger.get(stopped.video_receipt_id or '')
        self.assertEqual((receipt.state, receipt.provider_http_status), ('failed', 400))
        self.assertFalse(self.swap.work_busy())

    async def test_rename_invalidates_quote_and_preserves_reviewed_frame(self) -> None:
        job = await self.quoted(await self.frame())
        edited = self.swap.artifact(job.id, 'edited-frame.png').read_bytes()
        result = await self.swap.rename(job.id, RenameCharacterSwapRequest(revision=job.revision, name='New title'))
        self.assertEqual((result.name, result.stage, result.revision), ('New title', 'video', job.revision + 1))
        self.assertIsNone(result.quote)
        self.assertEqual(result.quote_expires_at, '')
        self.assertIsNone(result.estimated_usd)
        self.assertEqual(result.frame_receipt_id, job.frame_receipt_id)
        self.assertEqual(self.swap.artifact(job.id, 'edited-frame.png').read_bytes(), edited)
        with self.assertRaises(self.swap.CharacterSwapError) as error:
            await self.swap.submit(job.id, self.body(job))
        self.assertEqual(error.exception.code, 'revision_conflict')

    async def test_frame_charge_is_blocked_when_video_stage_cannot_run(self) -> None:
        job=await self.prepare()
        catalog.publish([catalog.normalize_image_edit(image_metadata()).model_copy(update={'supported_aspect_ratios':['16:9','3:2']}),*catalog.normalize_video(video_metadata(ratios=['16:9']))])
        with self.assertRaises(self.swap.CharacterSwapError) as error: await self.quoted(job)
        self.assertEqual(error.exception.code,'edit_parameters_unsupported')
        self.assertFalse(self.calls)
        self.assertIsNone(self.swap.get(job.id).quote)

    async def test_legacy_stop_is_also_readonly(self) -> None:
        job=await self.prepare()
        document=self.swap.load(job.id)
        document.job.provider='fal';document.job.model_id='fal-ai/wan/v2.2-14b/animate/replace'
        document.job.status='running';self.swap.save(document)
        with self.assertRaises(self.swap.CharacterSwapError):
            await self.swap.stop(job.id,CharacterSwapRevisionRequest(revision=job.revision))
        self.assertEqual(self.swap.get(job.id).status,'running')

    async def test_missing_retained_input_returns_safe_error_without_spending(self) -> None:
        job=await self.quoted(await self.prepare())
        self.swap.artifact(job.id,'source-frame.png').unlink()
        with self.assertRaises(self.swap.CharacterSwapError) as error:
            await self.swap.submit(job.id,self.body(job))
        self.assertEqual(error.exception.code,'source_changed')
        self.assertFalse(self.calls)

    async def test_http_quote_and_frame_routes_use_owned_job_contract(self) -> None:
        from app.main import app
        job=await self.prepare()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:9000') as client:
            quoted=await client.post('/api/videos/character-swaps/'+job.id+'/quote',json={'revision':job.revision})
            self.assertEqual(quoted.status_code,200)
            parsed=CharacterSwapJob.model_validate_json(quoted.content)
            self.assertIsNotNone(parsed.quote)
            self.assertGreater(parsed.revision,job.revision)
            frame=await client.get(parsed.source_frame_url)
            self.assertEqual(frame.status_code,200)
            self.assertEqual(frame.headers['content-type'],'image/png')
            unavailable=await client.get('/api/videos/character-swaps/'+job.id+'/frame-edited')
            self.assertEqual(unavailable.status_code,404)
            invalid=await client.post('/api/videos/character-swaps/'+job.id+'/submit',json={
                'revision':parsed.revision,'quote_id':parsed.quote.id if parsed.quote else '',
                'transfers_confirmed':'true','consent_confirmed':True})
            self.assertEqual(invalid.status_code,422)
        self.assertFalse(self.calls)

    async def test_uploaded_mkv_prepares_locally_with_audio(self) -> None:
        from app.video_media import probe_media
        movie = self.root/'movie.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-c', 'copy', str(movie)], check=True)
        original = movie.read_bytes()
        job = await self.swap.create(UploadFile(io.BytesIO(original), filename=movie.name),
            UploadFile(io.BytesIO(self.image.getvalue()), filename='character.png'),
            name='MKV swap', start_sec=1, seconds=2)
        self.assertEqual(job.source_name, 'movie.mkv')
        self.assertEqual(self.swap.artifact(job.id, 'source.video').read_bytes(), original)
        self.assertAlmostEqual((await probe_media(self.swap.artifact(job.id, 'selected.mp4'))).audio_duration, 2, delta=.1)
        self.assertFalse(self.calls)

    async def test_uploaded_avi_prepares_browser_clip_with_audio(self) -> None:
        from app.video_media import probe_media
        avi=self.root/'movie.avi'
        subprocess.run(['ffmpeg','-v','error','-i',str(self.source),'-c:v','mpeg4','-c:a','pcm_s16le',str(avi)],check=True)
        original=avi.read_bytes()
        job=await self.swap.create(UploadFile(io.BytesIO(original),filename='movie.avi'),
            UploadFile(io.BytesIO(self.image.getvalue()),filename='character.png'),
            name='AVI scene',start_sec=1,seconds=2)
        self.assertEqual(job.source_name,'movie.avi')
        self.assertEqual(self.swap.artifact(job.id,'source.video').read_bytes(),original)
        info=await probe_media(self.swap.artifact(job.id,'selected.mp4'))
        self.assertAlmostEqual(info.video_duration,2,places=1)
        self.assertAlmostEqual(info.audio_duration,2,places=1)
        self.assertFalse(self.calls)

    async def test_progress_does_not_invalidate_stop_revision(self) -> None:
        from app.character_swap_contracts import CharacterSwapRevisionRequest
        job = await self.prepare()
        self.swap._change(job.id, status='queued', phase='queued')
        self.swap._change(job.id, status='running', phase='generating')
        result = await self.swap.stop(job.id, CharacterSwapRevisionRequest(revision=job.revision))
        self.assertEqual(result.status, 'stopped')

    async def test_one_corrupt_retained_job_does_not_prevent_backend_recovery(self) -> None:
        path = self.swap.directory('0' * 32)
        path.mkdir(parents=True)
        (path / 'job.json').write_bytes(b'broken metadata')
        await self.swap.recover()
        self.assertEqual((path / 'job.json').read_bytes(), b'broken metadata')

    async def test_retained_metadata_cannot_redirect_media_outside_its_job(self) -> None:
        job = await self.prepare()
        document = self.swap.load(job.id)
        for value in ('javascript:alert(1)', 'https://external.example/video.mp4',
                      '/api/videos/character-swaps/' + '0' * 32 + '/source'):
            with self.subTest(value=value):
                document.job.source_url = value
                self.swap._write(self.swap.artifact(job.id, 'job.json'), document)
                with self.assertRaises(self.swap.CharacterSwapError) as error:
                    self.swap.get(job.id)
                self.assertEqual(error.exception.code, 'storage_unavailable')

    async def test_unresolved_preparation_remains_busy_and_shutdown_retries_its_drain(self) -> None:
        from app.atomic_files import write_object
        from app.video_process import WorkerIdentity
        identifier = 'a' * 32
        path = self.swap.root() / ('.' + identifier + '.preparing')
        path.mkdir(parents=True)
        write_object(path / 'preparation.json', {'id': identifier, 'kind': 'character_swap_preparation'})
        self.swap._write(path / 'worker.json', WorkerIdentity(pid=999999, token='b' * 32, receipt='worker-receipt.json'))
        with patch.object(self.swap, 'terminate_verified', return_value=False) as terminate:
            await self.swap.recover()
            self.assertTrue(self.swap.work_busy())
            with self.assertRaises(self.swap.CharacterSwapError):
                await self.swap.shutdown()
            self.assertEqual(terminate.await_count, 2)
        await self.swap.recover()
        self.assertFalse(self.swap.work_busy())

    async def test_copied_interrupted_preparation_recovers_from_local_receipt(self) -> None:
        from app.atomic_files import write_object
        from app.video_process import WorkerIdentity, WorkerReceipt
        identifier = 'c' * 32
        original = self.root / 'previous-library' / ('.' + identifier + '.preparing')
        original.mkdir(parents=True)
        write_object(original / 'preparation.json', {'id': identifier, 'kind': 'character_swap_preparation'})
        identity = WorkerIdentity(pid=999999, token='d' * 32, receipt='worker-receipt.json')
        self.swap._write(original / 'worker.json', identity)
        receipt = WorkerReceipt(pid=identity.pid, token=identity.token, returncode=0)
        (original / 'worker-receipt.json').write_text(receipt.model_dump_json())
        destination = self.swap.root() / original.name
        destination.parent.mkdir(parents=True)
        shutil.copytree(original, destination)

        async def terminate(local: WorkerIdentity) -> bool:
            self.assertEqual(Path(local.receipt), destination / 'worker-receipt.json')
            self.assertEqual(WorkerReceipt.model_validate_json(Path(local.receipt).read_bytes()), receipt)
            return True

        with patch.object(self.swap, 'terminate_verified', side_effect=terminate) as worker:
            await self.swap.recover()
            worker.assert_awaited_once()
        self.assertFalse(destination.exists())
        self.assertTrue(original.exists())
        self.assertFalse(self.swap.work_busy())

    async def test_worker_recovery_rejects_receipt_symlink_before_reading_it(self) -> None:
        from app.video_process import WorkerIdentity
        path = self.swap.directory('e' * 32)
        path.mkdir(parents=True)
        self.swap._write(path / 'worker.json', WorkerIdentity(pid=999999, token='f' * 32, receipt='worker-receipt.json'))
        outside = self.root / 'private-receipt.json'
        outside.write_text('private')
        (path / 'worker-receipt.json').symlink_to(outside)
        with patch.object(self.swap, 'terminate_verified', return_value=True) as worker:
            with self.assertRaises(self.swap.CharacterSwapError) as error:
                await self.swap._recover_worker(path)
            self.assertEqual(error.exception.code, 'worker_unresolved')
            worker.assert_not_awaited()
        self.assertEqual(outside.read_text(), 'private')

    async def test_cancelled_preparation_drains_owned_process_and_closes_uploads(self) -> None:
        import sys
        ready = asyncio.Event()
        video = UploadFile(io.BytesIO(self.source.read_bytes()), filename='source.mp4')
        image = UploadFile(io.BytesIO(self.image.getvalue()), filename='image.png')
        original = self.swap._command
        process_done = asyncio.Event()
        async def command(path: Path, argv: list[str]) -> None:
            ready.set()
            try:
                await original(path, [sys.executable, '-c', 'import time; time.sleep(60)'])
            finally:
                process_done.set()
        with patch.object(self.swap, '_command', side_effect=command):
            preparation = asyncio.create_task(self.swap.create(video, image, name='Cancel test', start_sec=0, seconds=2))
            await asyncio.wait_for(ready.wait(), 5)
            await self.swap.shutdown()
            await asyncio.gather(preparation, return_exceptions=True)
        self.assertTrue(process_done.is_set())
        self.assertTrue(video.file.closed)
        self.assertTrue(image.file.closed)
        self.assertFalse(self.swap.work_busy())
        self.assertEqual(list(self.swap.root().iterdir()), [])

    async def test_cloud_input_drops_original_container_metadata(self) -> None:
        tagged = self.root / 'tagged.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-c', 'copy',
                        '-metadata', 'comment=private-source-marker', str(tagged)], check=True)
        self.source = tagged
        job = await self.prepare()
        def metadata(path: Path) -> bytes:
            return subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags:stream_tags',
                                   '-of', 'json', str(path)], check=True, capture_output=True).stdout
        self.assertIn(b'private-source-marker', metadata(self.swap.artifact(job.id, 'source.video')))
        self.assertNotIn(b'private-source-marker', metadata(self.swap.artifact(job.id, 'input.mp4')))

    async def test_relabelled_playlist_is_rejected_as_video(self) -> None:
        video = UploadFile(io.BytesIO(b'#EXTM3U\nhttp://127.0.0.1/private\n'), filename='source.mp4')
        image = UploadFile(io.BytesIO(self.image.getvalue()), filename='image.png')
        from app.video_media import VideoMediaError
        with self.assertRaises(VideoMediaError):
            await self.swap.create(video, image, name='Invalid video', start_sec=0, seconds=2)
        self.assertTrue(video.file.closed)
        self.assertEqual(self.swap.list_jobs().jobs, [])
