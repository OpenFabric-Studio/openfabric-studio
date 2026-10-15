"""Metadata probes retain ownership when their backend disappears."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipIf(sys.platform == "win32", "POSIX hard-backend-death fixture")
class ProbeBackendDeathTests(unittest.TestCase):
    def test_probe_helpers_stop_workers_and_descendants_after_backend_death(self) -> None:
        operations = (
            ("voice_build", "_tool", "_probe_duration"),
            ("video_jobs", "_tool", "_probe_duration"),
            ("video_jobs", "_tool", "_video_size"),
            ("video_media", "tool", "probe_media"),
            ("voice_replacement", "voice_build._tool", "_duration"),
        )
        for module, tool, operation in operations:
            with self.subTest(operation=f"{module}.{operation}"):
                self._check_backend(module, f"module.{tool}=lambda name:str(binary)", f"module.{operation}(source)")

    def test_backend_codec_helpers_stop_workers_and_descendants_after_backend_death(self) -> None:
        media = "module.probe_media=AsyncMock(return_value=MediaInfo(96,64,24,2,2))\n"
        project = "project=root/'project';project.mkdir()\nmodule.project_dir=lambda identifier:project\nmodule.artifact=lambda identifier,relative:project/relative\n"
        normalization = "lambda source,target:[str(binary)]"
        operations = (
            ("video_media", media + "module.tool=lambda name:str(binary)", "module.validate_media(source,2,(96,64))"),
            ("voice_replacement", "module._duration=AsyncMock(return_value=2)\nmodule.voice_build._tool=lambda name:str(binary)", "module._decode(source,root/'converted.wav')"),
            ("video_jobs", "module._tool=lambda name:str(binary)", "module._energy_levels(source)"),
            ("midi", "module.FFMPEG_BIN_DIR=root", "module._to_mono_wav(source)"),
            ("optional_engines", media + "module.tool=lambda name:str(binary)", "module._validate_output(source)"),
            ("module_catalog", "", "module.probe([str(binary)],timeout=60)"),
            ("video_projects", project + media + f"from app import image_normalization\nimage_normalization.normalization_argv={normalization}", "module._upload_reference('a'*32,0,UploadFile(io.BytesIO(b'\\x89PNG\\r\\n\\x1a\\nfixture'),filename='still.png'),module._ReferenceUploadState())"),
            ("video_projects", project + "module.tool=lambda name:str(binary)", "module._upload_speech('a'*32,0,UploadFile(io.BytesIO(b'RIFF0000WAVEfixture'),filename='clip.wav'),module._ReferenceUploadState())"),
            ("video_characters", media + f"module.normalization_argv={normalization}\nmodule.require_voice=lambda identifier:SimpleNamespace(id=identifier,name='Fixture')", "module.create_character(name='Fixture',voice_profile_id='a'*32,consent_confirmed=True,upload=UploadFile(io.BytesIO(b'\\x89PNG\\r\\n\\x1a\\nfixture'),filename='still.png'))"),
            ("video_character_training", media + f"module.normalization_argv={normalization}", "module._create_job(name='Fixture',consent_confirmed=True,uploads=[UploadFile(io.BytesIO(b'\\x89PNG\\r\\n\\x1a\\nfixture'),filename='still.png')])"),
        )
        for module, setup, operation in operations:
            with self.subTest(operation=f"{module}:{operation}"):
                self._check_backend(module, setup, operation)

    def test_identity_query_helpers_stop_workers_after_backend_death(self) -> None:
        operations = (
            ("orchestrator.listener", "", "module._query_pids([str(binary)],'',False)"),
            ("video_process", "import os\n(root/'ps').symlink_to(binary)\nos.environ['PATH']=str(root)+os.pathsep+os.environ['PATH']", "module.terminate_verified(module.WorkerIdentity(pid=os.getpid(),token='a'*32,receipt=str(root/'worker.json')))"),
        )
        for module, setup, operation in operations:
            with self.subTest(operation=module):
                self._check_backend(module, setup, operation)

    def _check_backend(self, module: str, setup: str, operation: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "pids.json"
            source = root / "fixture.mp4"
            source.write_bytes(b"temporary media fixture")
            fake_probe = root / "ffmpeg"
            fake_probe.write_text(
                f"#!{sys.executable}\n"
                "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n"
                "time.sleep(60)\n"
            )
            fake_probe.chmod(0o700)
            code = (
                "import asyncio,io,importlib\nfrom pathlib import Path\nfrom unittest.mock import AsyncMock\n"
                "from types import SimpleNamespace\nfrom fastapi import UploadFile\nfrom app.video_media import MediaInfo\n"
                f"module=importlib.import_module({'app.' + module!r})\n"
                f"binary=Path({str(fake_probe)!r});source=Path({str(source)!r});root=Path({str(root)!r})\n"
                f"{setup}\nasyncio.run({operation})\n"
            )
            environment = dict(os.environ)
            environment.update(
                OPENFABRIC_CONFIG=str(root / "config.json"),
                OPENFABRIC_DATA_DIR=str(root / "library"),
                SEED_VC_DIR=str(root / "seed-vc"),
                OPENFABRIC_MODULE_ROOT=str(root / "modules"),
            )
            backend = subprocess.Popen([sys.executable, "-c", code], env=environment)
            pids: list[int] = []
            try:
                until = time.monotonic() + 5
                while not marker.is_file() and time.monotonic() < until:
                    time.sleep(0.01)
                self.assertTrue(marker.is_file(), "Fake probe did not start")
                raw: object = json.loads(marker.read_text())
                self.assertIsInstance(raw, list)
                if not isinstance(raw, list) or not all(type(pid) is int for pid in raw):
                    self.fail("Invalid fixture PID document")
                pids = [pid for pid in raw if type(pid) is int]
                backend.kill()
                backend.wait(5)
                until = time.monotonic() + 2
                while any(self._alive(pid) for pid in pids) and time.monotonic() < until:
                    time.sleep(0.02)
                self.assertFalse(
                    any(self._alive(pid) for pid in pids),
                    "Command or its descendant survived abrupt backend death",
                )
            finally:
                if backend.poll() is None:
                    backend.kill()
                    backend.wait(5)
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @staticmethod
    def _alive(pid: int) -> bool:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
        state = result.stdout.strip()
        return bool(state) and not state.startswith("Z")
