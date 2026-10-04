"""Character training saves a real adapter only when a local command writes one."""

from __future__ import annotations

import asyncio
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.datastructures import UploadFile


def _png(path: Path, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x64", "-frames:v", "1", str(path)],
        check=True,
    )


def _upload(path: Path) -> UploadFile:
    return UploadFile(io.BytesIO(path.read_bytes()), filename=path.name)


class CharacterTrainingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import video_character_training as training
        from app import video_projects as projects

        self.training = training
        self.projects = projects
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(training, "DATA_DIR", self.root))
        self.enterContext(patch.object(projects, "DATA_DIR", self.root))
        self.enterContext(patch.dict(os.environ, {"OPENFABRIC_VIDEO_CHARACTER_TRAINER": ""}, clear=False))
        os.environ.pop("OPENFABRIC_VIDEO_CHARACTER_TRAINER", None)
        self.photos = []
        for index, color in enumerate(("red", "green", "blue")):
            path = self.root / f"photo-{index}.png"
            _png(path, color)
            self.photos.append(path)

    def _files(self) -> list[UploadFile]:
        return [_upload(path) for path in self.photos]

    async def test_unconfigured_trainer_saves_a_dry_run_and_not_an_adapter(self) -> None:
        job = await self.training.create_job(name="Ava", consent_confirmed=True, uploads=self._files())
        self.assertEqual(job.status, "mock_completed")
        self.assertTrue(job.mock)
        self.assertFalse(job.adapter_ready)
        self.assertIsNone(self.training.ready_adapter_file(job.id))
        self.assertFalse(any((self.training.job_dir(job.id)).rglob("*.safetensors")))
        self.assertFalse(self.training.trainer_status().configured)
        with self.assertRaises(self.projects.VideoProjectError) as missing:
            await self.training.create_job(name="Ava", consent_confirmed=False, uploads=self._files())
        self.assertEqual(missing.exception.code, "consent_required")

    async def test_configured_command_writes_an_adapter_for_a_reel(self) -> None:
        from app.video_contracts import ApplyVideoCharacterAdapterRequest, CreateVideoProjectRequest

        command = self.root / "train-character"
        command.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\nfrom pathlib import Path\n"
            "out = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'adapter.safetensors').write_bytes(b'lora' * 400)\n"
            "images = [sys.argv[i + 1] for i, item in enumerate(sys.argv) if item == '--image']\n"
            "if len(images) < 3:\n    raise SystemExit(2)\n",
            encoding="utf-8",
        )
        command.chmod(command.stat().st_mode | stat.S_IEXEC)
        self.training.save_trainer_command(str(command))
        self.assertTrue(self.training.trainer_status().configured)
        job = await self.training.create_job(name="Ava", consent_confirmed=True, uploads=self._files())
        done = job
        for _ in range(100):
            done = self.training.get_job(job.id)
            if done.status not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(done.status, "completed")
        self.assertTrue(done.adapter_ready)
        self.assertFalse(done.mock)
        adapter = self.training.ready_adapter_file(job.id)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertGreaterEqual(adapter.stat().st_size, 1024)

        reel = await self.projects.create(CreateVideoProjectRequest(track_id=None, preset="reel", duration_sec=8, name="Reel"))
        applied = self.projects.apply_character_adapter(
            reel.id, ApplyVideoCharacterAdapterRequest(revision=reel.revision, training_id=job.id)
        )
        self.assertEqual(applied.character_adapter_id, job.id)
        self.assertTrue(applied.character_lock)
        self.assertNotIn("character_adapter_mock", applied.warnings)
        self.assertNotIn("character_still_missing", applied.warnings)


    async def test_song_project_rejects_the_adapter_and_a_dry_run_keeps_the_still(self) -> None:
        from app.video_contracts import ApplyVideoCharacterAdapterRequest, CreateVideoProjectRequest

        audio = self.root / "song.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=5", str(audio)], check=True)
        with patch.object(self.projects.db, "get_track", return_value={"id": 1, "title": "Song", "audio_path": str(audio), "duration_ms": 5000}), \
                patch("app.video_jobs._probe_duration", return_value=5):
            song = await self.projects.create(CreateVideoProjectRequest(track_id=1))
        dry = await self.training.create_job(name="Ava", consent_confirmed=True, uploads=self._files())
        with self.assertRaises(self.projects.VideoProjectError) as rejected:
            self.projects.apply_character_adapter(
                song.id, ApplyVideoCharacterAdapterRequest(revision=song.revision, training_id=dry.id)
            )
        self.assertEqual(rejected.exception.code, "character_adapter_picture_only")
        reel = await self.projects.create(CreateVideoProjectRequest(track_id=None, preset="reel", duration_sec=8, name="Reel"))
        applied = self.projects.apply_character_adapter(
            reel.id, ApplyVideoCharacterAdapterRequest(revision=reel.revision, training_id=dry.id)
        )
        self.assertTrue(applied.character_lock)
        self.assertIn("character_adapter_mock", applied.warnings)
        self.assertIsNone(self.training.ready_adapter_file(dry.id))

    def test_engine_python_argv_uses_the_openfabric_script_and_not_a_download(self) -> None:
        python = self.root / "python"
        python.write_text("", encoding="utf-8")
        images = [self.root / "a.png", self.root / "b.png"]
        argv = self.training.training_argv(python, self.root / "out", "Ava", images, [])
        self.assertEqual(Path(argv[1]).name, "train_character_adapter.py")
        self.assertIn("--engine-dir", argv)
        self.assertNotIn("snapshot_download", Path(argv[1]).read_text(encoding="utf-8"))
        script = Path(argv[1])
        completed = subprocess.run(
            [sys.executable, str(script), "--output", str(self.root / "adapter"), "--name", "Ava",
             "--engine-dir", str(self.root / "engine"), "--model-cache", str(self.root / "empty-cache"),
             "--image", str(self.photos[0])],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("model_not_installed", completed.stderr)
        self.assertFalse((self.root / "adapter" / "adapter.safetensors").exists())


if __name__ == "__main__":
    unittest.main()
