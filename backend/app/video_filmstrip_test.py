"""Filmstrips sample distinct frames through the existing owned FFmpeg worker."""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import video_projects as store, video_render as render
from app.video_contracts import CreateVideoProjectRequest, VideoProjectJob


class VideoFilmstripTests(unittest.IsolatedAsyncioTestCase):
    async def test_strip_samples_changing_frames_instead_of_repeating_poster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(store,'DATA_DIR',root), patch.object(render,'LOG_DIR',root/'logs'):
                project=await store.create(CreateVideoProjectRequest(duration_sec=2))
                document=store.load(project.id)
                document.project.job=VideoProjectJob(id='a'*32,operation='preview',status='running')
                store.save(document)
                clip=root/'colors.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:size=64x64:duration=1',
                    '-f','lavfi','-i','color=blue:size=64x64:duration=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0',str(clip)],check=True)
                strip=root/'strip.png'
                await render._filmstrip(project.id,clip,strip,2)
                with Image.open(strip) as image:
                    self.assertEqual(image.size,(800,90))
                    first=image.getpixel((80,45));last=image.getpixel((720,45))
                    self.assertNotEqual(first,last)
                self.assertIsNone(store.load(project.id).worker)
