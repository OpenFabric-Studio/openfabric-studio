"""Legacy review regressions before integrating the saved-project runner."""

from __future__ import annotations
import asyncio, json, shutil, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from app import video_jobs as v


class VideoRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_polling_never_reconciles_or_writes_running_metadata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(v, "DATA_DIR", Path(tmp)),
            patch.object(v, "_current", None),
        ):
            job = v.VideoJob(
                id="a" * 32,
                track_id=1,
                title="Song",
                prompt="a singer",
                seconds=4,
                start_sec=0,
                frames=97,
                seed=1,
                created_at="",
                status="running",
            )
            v._write(job)
            path = v._video_dir(job.id) / "video.json"
            before = path.read_bytes()
            self.assertEqual(v.get_video(job.id)["status"], "running")
            self.assertEqual(v.list_videos()[0]["status"], "running")
            self.assertEqual(path.read_bytes(), before)

    def test_small_overlaps_are_rejected(self) -> None:
        with self.assertRaises(v.VideoJobError):
            v.validate_shots(
                [
                    {"start_sec": 0, "seconds": 4, "prompt": "a singer"},
                    {"start_sec": 3.75, "seconds": 4, "prompt": "a singer"},
                ],
                10000,
            )

    async def test_generated_short_video_is_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(v, "DATA_DIR", Path(tmp)),
            patch.object(v, "LOG_DIR", Path(tmp)),
        ):
            root = Path(tmp)
            short = root / "short.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=704x448:r=24:d=0.5",
                    "-c:v",
                    "libx264",
                    str(short),
                ],
                check=True,
            )

            async def generate(argv: list[str], **kwargs: object) -> int:
                shutil.copyfile(short, argv[argv.index("--output") + 1])
                return 0

            job = v.VideoJob(
                id="a" * 32,
                track_id=1,
                title="Song",
                prompt="a singer",
                seconds=4,
                start_sec=0,
                frames=97,
                seed=1,
                created_at="",
            )

            def command(_engine, _cache, settings):
                return ["mock", "--output", str(settings.output)]

            with (
                patch.object(v, "_spawn", side_effect=generate),
                patch(
                    "app.video_engine.verified_cached_fingerprint",
                    return_value="verified",
                ),
                patch("app.video_engine.render_argv", side_effect=command),
                self.assertRaises(v.VideoJobError) as error,
            ):
                await v._generate_clip(
                    job,
                    Path("/mock-engine"),
                    Path("/mock-audio"),
                    "a singer",
                    97,
                    root / "output.mp4",
                    [],
                    1,
                )
            self.assertEqual(error.exception.code, "invalid_media")

    async def test_legacy_unverified_worker_remains_blocked_on_second_restart(
        self,
    ) -> None:
        from app.video_process import WorkerIdentity
        from unittest.mock import AsyncMock

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(v, "DATA_DIR", Path(tmp)),
        ):
            job = v.VideoJob(
                id="a" * 32,
                track_id=1,
                title="Song",
                prompt="a singer",
                seconds=4,
                start_sec=0,
                frames=97,
                seed=1,
                created_at="",
                status="running",
                worker=WorkerIdentity(
                    pid=123, token="e" * 32, receipt=str(Path(tmp) / "worker.json")
                ),
            )
            v._write(job)
            try:
                with (
                    patch.object(
                        v, "terminate_verified", new=AsyncMock(return_value=False)
                    ) as verify,
                    patch("app.video_render.recover", new=AsyncMock()),
                ):
                    await v.recover()
                    v._unverified.clear()
                    await v.recover()
                    self.assertEqual(verify.await_count, 2)
                    self.assertTrue(v.work_busy())
                    with self.assertRaises(v.VideoJobError):
                        await v.delete_video(job.id)
            finally:
                v._unverified.discard(job.id)
