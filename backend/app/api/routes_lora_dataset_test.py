"""Dataset uploads stay inside the temporary engine and preserve originals."""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from typing import BinaryIO
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, UploadFile
import httpx

from app.api import routes_lora_dataset as routes


class DatasetUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine = self.root / 'engine'
        self.datasets = self.engine / 'datasets'
        self.datasets.mkdir(parents=True)
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        self.enterContext(patch.object(routes, 'ACE_STEP_DIR', self.engine))

    def upload(self, filename: str, data: bytes = b'complete audio fixture') -> UploadFile:
        stream = io.BytesIO(data)
        self.addCleanup(stream.close)
        return UploadFile(filename=filename, file=stream)

    async def test_rejects_dataset_directory_and_root_symlink_escapes(self) -> None:
        for escaped in ('dataset', 'root'):
            with self.subTest(escaped=escaped):
                if escaped == 'dataset':
                    linked = self.datasets / 'linked'
                    linked.symlink_to(self.outside, target_is_directory=True)
                else:
                    linked = self.datasets
                    linked.rmdir()
                    linked.symlink_to(self.outside, target_is_directory=True)
                try:
                    with self.assertRaises(HTTPException) as caught:
                        await routes.upload_dataset_files('linked', [self.upload('clip.wav')])
                    self.assertEqual(caught.exception.detail, 'dataset_path_unsafe')
                    self.assertEqual(list(self.outside.iterdir()), [])
                finally:
                    linked.unlink()

    async def test_dangling_filename_symlink_is_preserved_and_upload_uses_next_name(self) -> None:
        target = self.datasets / 'my_dataset'
        target.mkdir()
        linked = target / 'clip.wav'
        private = self.outside / 'private.wav'
        linked.symlink_to(private)
        response = await routes.upload_dataset_files('my_dataset', [self.upload('clip.wav')])
        self.assertEqual(response.saved, ['clip_1.wav'])
        self.assertTrue(linked.is_symlink())
        self.assertFalse(private.exists())
        self.assertEqual((target / 'clip_1.wav').read_bytes(), b'complete audio fixture')

    async def test_symlinked_dataset_root_inside_the_engine_remains_supported(self) -> None:
        backing = self.engine / 'dataset-storage'
        backing.mkdir()
        self.datasets.rmdir()
        self.datasets.symlink_to(backing, target_is_directory=True)
        response = await routes.upload_dataset_files('my_dataset', [self.upload('clip.wav')])
        self.assertEqual(response.audio_dir, 'datasets/my_dataset')
        self.assertEqual((backing / 'my_dataset' / 'clip.wav').read_bytes(), b'complete audio fixture')

    async def test_failed_copy_removes_only_the_new_partial_upload(self) -> None:
        target = self.datasets / 'my_dataset'
        target.mkdir()
        original = target / 'clip.wav'
        original.write_bytes(b'original recording')
        def fail_copy(_source: BinaryIO, destination: BinaryIO) -> None:
            destination.write(b'partial')
            raise OSError('injected interrupted upload')
        with patch.object(routes.shutil, 'copyfileobj', side_effect=fail_copy):
            with self.assertRaises(OSError):
                await routes.upload_dataset_files('my_dataset', [self.upload('clip.wav')])
        self.assertEqual(original.read_bytes(), b'original recording')
        self.assertEqual(list(target.iterdir()), [original])

    async def test_http_upload_preserves_sanitization_duplicates_and_extension_skips(self) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            response = await client.post('/api/lora-dataset/upload', data={'dataset_name': 'My set/../'}, files=[
                ('files', ('clip.wav', b'first', 'audio/wav')),
                ('files', ('clip.wav', b'second', 'audio/wav')),
                ('files', ('notes.txt', b'unsupported', 'text/plain')),
            ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'audio_dir': 'datasets/My_set', 'saved': ['clip.wav', 'clip_1.wav'], 'skipped': ['notes.txt']})
        self.assertEqual((self.datasets / 'My_set' / 'clip.wav').read_bytes(), b'first')
        self.assertEqual((self.datasets / 'My_set' / 'clip_1.wav').read_bytes(), b'second')
