"""Security, metadata and subprocess regressions, without model downloads."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx


class SpaTests(unittest.IsolatedAsyncioTestCase):
    async def test_serving_rejects_encoded_absolute_and_symlink_escapes(self) -> None:
        from app import config, main

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("spa", encoding="utf-8")
            (dist / "asset.js").write_text("asset", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("private marker", encoding="utf-8")
            (dist / "escape.txt").symlink_to(outside)
            try:
                with patch.object(config, "FRONTEND_DIST_DIR", dist):
                    importlib.reload(main)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test",
                ) as client:
                    self.assertEqual((await client.get("/asset.js")).text, "asset")
                    self.assertEqual((await client.get("/editor/1")).text, "spa")
                    for url in ("/..%2Foutside.txt", "/%2F" + str(outside).lstrip("/"), "/escape.txt"):
                        with self.subTest(url=url):
                            response = await client.get(url)
                            self.assertIn(response.status_code, (400, 404))
                            self.assertNotIn("private marker", response.text)
            finally:
                importlib.reload(main)


class MetadataTests(unittest.TestCase):
    def test_failed_metadata_write_keeps_last_published_document(self) -> None:
        from app.voice_build import read_meta, write_meta

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = {"name": "saved", "recordings": []}
            write_meta(directory, original)
            write_text = Path.write_text

            def interrupted(path: Path, data: str, **kwargs: object) -> int:
                write_text(path, "{", encoding="utf-8")
                raise OSError("simulated partial write")

            with patch.object(Path, "write_text", interrupted):
                with self.assertRaises(OSError):
                    write_meta(directory, {"name": "new", "recordings": []})
            self.assertEqual(read_meta(directory), original)


class VoiceCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversion_loads_the_voices_trained_checkpoint_and_config(self) -> None:
        from app import voice_build as voice

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "voice"
            directory.mkdir()
            checkpoint, config, reference, audio, base = [
                root / name for name in ("ft_model.pth", "custom.yml", "reference.wav", "song.wav", "base.pth")
            ]
            for path in (checkpoint, config, reference, audio, base):
                path.write_bytes(b"marker")
            preset = root / voice.SINGING_CONFIG
            preset.parent.mkdir(parents=True, exist_ok=True)
            preset.write_text("marker", encoding="utf-8")
            command: list[str] = []

            class Captured(Exception):
                pass

            async def capture(argv: list[str], **kwargs: object) -> int:
                command.extend(argv)
                raise Captured()

            with (
                patch.object(voice, "voice_dir", return_value=directory),
                patch.object(voice, "read_meta", return_value={}),
                patch.object(voice, "_contained", side_effect=[checkpoint, config, reference]),
                patch.object(voice.db, "get_track", return_value={
                    "audio_path": str(audio), "params_json": "{}",
                }),
                patch.object(voice, "_engine_python", return_value=Path(sys.executable)),
                patch.object(voice, "ensure_whisper_float32_off_cuda"),
                patch("app.seed_vc_compat.ensure_compatibility"),
                patch.object(voice, "_write_apply"),
                patch.object(voice, "voices_root", return_value=root),
                patch.object(voice, "separate_file", new=AsyncMock(return_value={
                    name: audio for name in ("vocals", "drums", "bass", "other")
                })),
                patch.object(voice, "ensure_target_reference", new=AsyncMock(return_value=reference)),
                patch.object(voice, "phase_safe_mono", new=AsyncMock()),
                patch.object(voice, "pretrained_singing_checkpoint", return_value=base),
                patch.object(voice, "SEED_VC_DIR", root),
                patch.object(voice, "_spawn", side_effect=capture),
            ):
                with self.assertRaises(Captured):
                    await voice._apply_inner(voice.ApplyJob(voice_id="a" * 32, track_id=1))
            self.assertEqual(command[command.index("--checkpoint") + 1], str(checkpoint))
            self.assertEqual(command[command.index("--config") + 1], str(config))


@unittest.skipIf(sys.platform == "win32", "POSIX descendant cancellation")
class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_demucs_cancellation_terminates_descendants(self) -> None:
        from app.stems import _kill_tree

        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child.pid"
            child = "import time; time.sleep(60)"
            wrapper = (
                "import subprocess,sys,time,pathlib;"
                f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                f"pathlib.Path({str(marker)!r}).write_text(str(p.pid));"
                "time.sleep(60)"
            )
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", wrapper, start_new_session=True,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            child_pid: int | None = None
            try:
                async with asyncio.timeout(5):
                    while not marker.exists():
                        await asyncio.sleep(0.01)
                child_pid = int(marker.read_text())
                await _kill_tree(proc)
                await asyncio.wait_for(proc.wait(), 5)
                inspector = await asyncio.create_subprocess_exec(
                    "ps", "-o", "stat=", "-p", str(child_pid), stdout=asyncio.subprocess.PIPE,
                )
                output, _ = await inspector.communicate()
                self.assertTrue(not output.strip() or output.strip().startswith(b"Z"), output.decode())
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
