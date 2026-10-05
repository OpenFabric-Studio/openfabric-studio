"""Cloud pictures conform only after checking native provider timing/geometry."""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import video_projects as store
from app.video_contracts import CreateVideoProjectRequest, VideoProjectJob
from app.video_media import probe_media


class CloudVideoMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store,'DATA_DIR',self.root))
        self.project=await store.create(CreateVideoProjectRequest(duration_sec=12))
        document=store.load(self.project.id)
        document.project.job=VideoProjectJob(id='c'*32,operation='preview',status='running')
        store.save(document)
        self.raw=self.root/'provider.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=1280x720:r=30:d=4','-f','lavfi','-i','sine=duration=4','-c:v','libx264','-c:a','aac','-shortest',str(self.raw)],check=True)
        self.output=store.artifact(self.project.id,'cloud/conformed.mp4');self.output.parent.mkdir()

    async def test_explicit_trim_keeps_provider_source_duration_and_removes_remote_audio(self) -> None:
        from app.video_cloud_media import conform
        source=await conform(self.project.id,self.raw,self.output,source_seconds=4,target_seconds=2,size=(1280,720),trim_confirmed=True)
        result=await probe_media(self.output)
        self.assertAlmostEqual(source.video_duration,4,places=1)
        self.assertAlmostEqual(result.video_duration,2,places=1)
        self.assertAlmostEqual(result.fps,24,places=1)
        self.assertEqual(result.audio_duration,0)
        self.assertTrue(self.raw.exists())
        self.assertIsNone(store.load(self.project.id).worker)

    async def test_short_dialogue_slot_never_implicitly_trims_a_paid_longer_clip(self) -> None:
        from app.video_cloud_media import conform
        with self.assertRaisesRegex(store.VideoProjectError,'cloud_trim_confirmation_required'):
            await conform(self.project.id,self.raw,self.output,source_seconds=4,target_seconds=2,size=(1280,720),trim_confirmed=False)
        self.assertFalse(self.output.exists())

    async def test_provider_timing_or_geometry_mismatch_cannot_be_published_as_the_requested_clip(self) -> None:
        from app.video_cloud_media import conform
        for expected_seconds,size in ((2,(1280,720)),(4,(720,1280))):
            with self.assertRaisesRegex(store.VideoProjectError,'cloud_output_mismatch'):
                await conform(self.project.id,self.raw,self.output,source_seconds=expected_seconds,target_seconds=2,size=size,trim_confirmed=True)
        self.assertFalse(self.output.exists())
