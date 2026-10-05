"""Mixed local/cloud dry PCM becomes owned reel copies without changing sources."""
from __future__ import annotations
import hashlib
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from app import video_dialogue as dialogue, video_projects as store, audiobooks, audiobook_workflows as source
from app.audiobook_contracts import AudiobookPassage, AudiobookPassagesResponse
from app.video_contracts import CreateDialogueReelRequest, DialogueReelSelection


class MixedDialogueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store,'DATA_DIR',self.root))
        self.enterContext(patch.object(audiobooks,'BOOKS_ROOT',self.root/'books'))
        self.enterContext(patch.object(dialogue,'require_voice'))
        self.paths: dict[str,Path]={}
        self.pcm: dict[str,bytes]={}
        for identifier,rate,frequency in (('b'*32,16000,330),('e'*32,24000,660)):
            path=self.root/f'{identifier}.wav'
            pcm=b''.join(struct.pack('<h',int(math.sin(2*math.pi*frequency*index/rate)*10000)) for index in range(rate))
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate);output.writeframes(pcm)
            self.paths[identifier]=path;self.pcm[identifier]=pcm
        self.snapshots={identifier:path.read_bytes() for identifier,path in self.paths.items()}
        self.passages=AudiobookPassagesResponse(book_id='a'*32,chapter_index=0,revision=2,passages=[
            AudiobookPassage(id=identifier,section_index=index,text=f'Actor {index}',profile_id='c'*32,speaker=f'Actor {index}',start_ms=index*1000,end_ms=(index+1)*1000,status='done',render_identity=str(index+1)*64)
            for index,identifier in enumerate(self.paths)])
        self.enterContext(patch.object(source,'get_passages',return_value=self.passages))
        self.enterContext(patch.object(source,'passage_audio_path',side_effect=self.resolve))

    def resolve(self, book_id: str, passage_id: str, revision: int | None=None) -> Path:
        return self.paths[passage_id]

    def body(self) -> CreateDialogueReelRequest:
        return CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=2,selections=[DialogueReelSelection(passage_id=identifier) for identifier in self.paths])

    def assert_tone(self, pcm: bytes, rate: int, frequency: int) -> None:
        samples=struct.unpack('<'+'h'*(len(pcm)//2),pcm)
        expected=[math.sin(2*math.pi*frequency*index/rate) for index in range(len(samples))]
        norm=math.sqrt(sum(value*value for value in samples)*sum(value*value for value in expected))
        self.assertGreater(norm,0,'normalization must preserve a non-silent line')
        self.assertGreater(sum(actual*source for actual,source in zip(samples,expected))/norm,.98)

    async def test_mixed_rates_join_owned_copies_with_original_source_hashes_and_timing(self) -> None:
        project=await dialogue.create(self.body())
        with wave.open(str(store.speech_file(project.id)),'rb') as output:
            self.assertEqual(output.getframerate(),24000);self.assertEqual(output.getnchannels(),1)
            self.assertEqual(output.getnframes(),4*24000)
            joined=output.readframes(output.getnframes())
        for cue in project.dialogue_cues:
            self.assertEqual(cue.audio_sha256,hashlib.sha256(self.pcm[cue.passage_id]).hexdigest())
            self.assertEqual(cue.end_sec-cue.start_sec,1)
            self.assertEqual(self.paths[cue.passage_id].read_bytes(),self.snapshots[cue.passage_id])
        self.assertEqual(joined[2*24000*2:3*24000*2],self.pcm['e'*32])
        self.assert_tone(joined[:24000*2],24000,330)
        self.assertEqual(joined[24000*2:2*24000*2],bytes(24000*2))
        self.assertIsNone(store.load(project.id).worker)
        self.assertEqual(store.list_projects()[0].id,project.id)

    async def test_real_cpu_export_preserves_both_mixed_rate_cast_lines_and_dry_sources(self) -> None:
        import asyncio
        import uuid
        from PIL import Image
        from app import video_render as render
        from app.video_contracts import VideoReference, VideoVariant, VideoExportRequest, VideoExportSettings
        from app.video_media import probe_media
        project=await dialogue.create(self.body())
        document=store.load(project.id)
        # CPU-generated fixture pictures exercise the actual approved-variant
        # assembler, not inference quality or a paid provider request.
        document.project.mode='cover'
        reference=store.artifact(project.id,'references/fixture.png')
        reference.parent.mkdir(parents=True);Image.new('RGB',(64,64),'blue').save(reference)
        document.reference_paths['f'*32]='references/fixture.png'
        document.project.references=[VideoReference(id='f'*32,name='CPU fixture',bytes=reference.stat().st_size,width=64,height=64,url='')]
        store.save(document)
        engine=await render._engine_fingerprint(document.project)
        fixture=self.root/'picture.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=704x1280:r=24:d=2','-an','-c:v','libx264',str(fixture)],check=True,timeout=20)
        for shot in document.project.shots:
            identifier=uuid.uuid4().hex
            expected=render.fingerprint(document,shot,shot.seed,engine)
            path=render.variant_path(project.id,shot.id,identifier);path.parent.mkdir(parents=True)
            path.write_bytes(fixture.read_bytes())
            receipt=render.VariantReceipt(variant_id=identifier,shot_id=shot.id,fingerprint=expected,output_sha256=store.file_hash(path),seconds=shot.seconds,width=704,height=1280)
            store.atomic_text(render._receipt_path(path),receipt.model_dump_json())
            shot.variants=[VideoVariant(id=identifier,seed=shot.seed,status='ready',fingerprint=expected,created_at=store.now(),mode='cover',settings=document.project.settings,engine_fingerprint=engine)]
            shot.approved_variant_id=identifier
        store.save(document)
        await render.export(project.id,VideoExportRequest(revision=project.revision,settings=VideoExportSettings(aspect='portrait',attach_speech=True,include_overlays=False)))
        await asyncio.gather(*tuple(render._tasks.values()))
        finished=store.get(project.id)
        self.assertIsNotNone(finished.job)
        if finished.job is None: raise AssertionError('missing export job')
        self.assertEqual(finished.job.status,'ready',finished.job.error_code)
        output=render.output_file(project.id)
        info=await probe_media(output);self.assertAlmostEqual(info.video_duration,4,places=1)
        decoded=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-map','0:a:0','-ar','24000','-ac','1','-f','s16le','-'],check=True,capture_output=True,timeout=20).stdout
        self.assert_tone(decoded[:24000*2],24000,330)
        self.assert_tone(decoded[2*24000*2:3*24000*2],24000,660)
        self.assertIsNone(store.load(project.id).worker)
        for identifier,path in self.paths.items():self.assertEqual(path.read_bytes(),self.snapshots[identifier])

    async def test_refresh_resamples_a_new_take_to_existing_reel_format_and_preserves_other_cue(self) -> None:
        from app.video_contracts import RefreshDialogueCueRequest
        project=await dialogue.create(self.body())
        original=store.speech_file(project.id)
        original_bytes=original.read_bytes()
        replacement=self.root/'new-dry-take.wav'
        changed=b''.join(struct.pack('<h',int(math.sin(2*math.pi*440*index/16000)*10000)) for index in range(16000))
        with wave.open(str(replacement),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(changed)
        replacement_bytes=replacement.read_bytes()
        self.paths['b'*32]=replacement
        self.passages.revision=3;self.passages.passages[0].render_identity='f'*64
        document=store.load(project.id)
        document.project.shots[0].approved_variant_id='1'*32;document.project.shots[1].approved_variant_id='2'*32
        store.save(document)
        refreshed=await dialogue.refresh(project.id,project.shots[0].id,RefreshDialogueCueRequest(revision=project.revision,
            source=CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=3,selections=[DialogueReelSelection(passage_id='b'*32)])))
        with wave.open(str(store.speech_file(project.id)),'rb') as output:
            self.assertEqual(output.getframerate(),24000);self.assertEqual(output.getnframes(),96000)
            pcm=output.readframes(output.getnframes())
        self.assert_tone(pcm[:24000*2],24000,440)
        self.assertEqual(pcm[2*24000*2:3*24000*2],self.pcm['e'*32])
        self.assertIsNone(refreshed.shots[0].approved_variant_id);self.assertEqual(refreshed.shots[1].approved_variant_id,'2'*32)
        self.assertEqual(refreshed.dialogue_cues[1],project.dialogue_cues[1])
        self.assertEqual(refreshed.dialogue_cues[0].audio_sha256,hashlib.sha256(changed).hexdigest())
        self.assertEqual(original.read_bytes(),original_bytes);self.assertEqual(replacement.read_bytes(),replacement_bytes)

    async def test_preparing_project_stays_hidden_and_shutdown_drains_normalization(self) -> None:
        import asyncio
        from app import video_render
        seen=asyncio.Event()
        async def paused(project_id: str,argv: list[str],phase: str,*,timeout: float) -> None:
            self.assertTrue(store.load(project_id).preparing)
            with self.assertRaises(store.VideoProjectError): store.get(project_id)
            self.assertEqual(store.list_projects(),[])
            seen.set();await asyncio.Event().wait()
        with patch.object(video_render,'_command',side_effect=paused):
            caller=asyncio.create_task(dialogue.create(self.body()))
            await asyncio.wait_for(seen.wait(),2)
            self.assertTrue(video_render.work_busy())
            caller.cancel();await asyncio.sleep(0);caller.cancel();await asyncio.sleep(0)
            self.assertFalse(caller.done(),'browser cancellation must not detach an owned operation')
            await video_render.shutdown()
            await asyncio.gather(caller,return_exceptions=True)
        self.assertFalse(dialogue.work_busy());self.assertEqual(store.list_projects(),[])
        self.assertEqual(list(store.projects_root().iterdir()),[])
        for identifier,path in self.paths.items():self.assertEqual(path.read_bytes(),self.snapshots[identifier])

    async def test_restart_keeps_unverified_staging_hidden_until_owned_worker_is_drained(self) -> None:
        import uuid
        from app import video_process, video_render
        stage_id=uuid.uuid4().hex;store.project_dir(stage_id).mkdir(parents=True)
        await dialogue._prepare(self.body(),stage_id)
        staged=store.load(stage_id)
        from app.video_contracts import VideoRevisionRequest
        with self.assertRaisesRegex(store.VideoProjectError,'not_found'):
            store.duplicate(stage_id,VideoRevisionRequest(revision=staged.project.revision))
        staged.worker=video_process.WorkerIdentity(pid=2147483647,token='f'*32,receipt='runs/interrupted/worker.json')
        store.save(staged)
        with patch.object(video_process,'terminate_verified',return_value=False):
            await dialogue.recover()
        self.assertTrue(store.project_dir(stage_id).exists());self.assertEqual(store.list_projects(),[])
        self.assertIn(stage_id,video_render._unverified)
        with patch.object(video_process,'terminate_verified',return_value=True):
            await dialogue.recover()
        self.assertFalse(store.project_dir(stage_id).exists());self.assertNotIn(stage_id,video_render._unverified)
        for identifier,path in self.paths.items():self.assertEqual(path.read_bytes(),self.snapshots[identifier])
