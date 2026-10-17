"""Cancellation waits for owned disk IO before cleanup can remove its files."""

from __future__ import annotations
import asyncio, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch


class VideoIoTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_hash_and_copy_stop_when_source_grows_after_preflight(self) -> None:
        from app.video_io import copy_verified, hash_file
        from app.video_projects import VideoProjectError
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            dest = Path(tmp) / 'dest'
            real = Path.open

            class GrowingReader:
                def __init__(self, handle):
                    self.handle = handle
                    self.grew = False

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.handle.close()

                def read(self, size):
                    value = self.handle.read(size)
                    if not self.grew:
                        self.grew = True
                        with real(source, 'ab') as append:
                            append.write(b'added after preflight' * 10000)
                    return value

            def opening(path, *args, **kwargs):
                handle = real(path, *args, **kwargs)
                return GrowingReader(handle) if path == source and args and args[0] == 'rb' else handle

            for copying in (False, True):
                source.write_bytes(b'original')
                with self.subTest(copying=copying), patch.object(Path, 'open', new=opening):
                    with self.assertRaises(VideoProjectError):
                        if copying:
                            await copy_verified(source, dest, hashlib.sha256(b'original').hexdigest(), max_bytes=8)
                        else:
                            await hash_file(source, max_bytes=8)
                self.assertFalse(dest.exists())

    async def test_copy_refuses_to_overwrite_or_delete_existing_destination(
        self,
    ) -> None:
        from app.video_io import copy_verified
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.write_bytes(b"original")
            dest = Path(tmp) / "dest"
            dest.write_bytes(b"published")
            with self.assertRaises(FileExistsError):
                await copy_verified(
                    source, dest, hashlib.sha256(b"original").hexdigest()
                )
            self.assertEqual(dest.read_bytes(), b"published")

    async def test_copy_cancellation_drains_slow_read(self) -> None:
        from app.video_io import copy_verified
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.write_bytes(b"original")
            dest = Path(tmp) / "dest"
            entered = threading.Event()
            release = threading.Event()
            real = Path.open

            class SlowReader:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.handle.close()

                def read(self, size):
                    entered.set()
                    release.wait(5)
                    return self.handle.read(size)

            def opening(path, *args, **kwargs):
                handle = real(path, *args, **kwargs)
                return (
                    SlowReader(handle)
                    if path == source and args and args[0] == "rb"
                    else handle
                )

            with patch.object(Path, "open", new=opening):
                task = asyncio.create_task(
                    copy_verified(source, dest, hashlib.sha256(b"original").hexdigest())
                )
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                release.set()
                await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(dest.exists())
            self.assertEqual(source.read_bytes(), b"original")
