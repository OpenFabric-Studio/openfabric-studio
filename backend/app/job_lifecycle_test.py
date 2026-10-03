"""One-shot task ownership and cancellation regressions, without model work."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db, midi, stems, video_jobs as video, voice_build as voice
from app.job_lifecycle import cancel_and_wait


class JobLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_tasks = asyncio.all_tasks()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.voice_id = "a" * 32
        self.voice_path = self.root / self.voice_id
        self.voice_path.mkdir()
        voice.write_meta(self.voice_path, {"id": self.voice_id, "recordings": [{"filename": "source.wav", "bytes": 6}], "status": "idle"})
        (self.voice_path / "recordings").mkdir()
        (self.voice_path / "recordings" / "source.wav").write_bytes(b"marker")
        from app import voice_preparation as preparation
        from app.voice_contracts import PrepareVoiceRequest, VoicePreparationResponse, VoiceReferenceCandidate, VoiceSegment
        preparation.save(self.voice_path, preparation.PreparationDocument(
            response=VoicePreparationResponse(revision="reviewed", status="done",
                options=PrepareVoiceRequest(singer_confirmed=True), selected_segment_ids=["clip"], reference_id="ref",
                segments=[VoiceSegment(id="clip", source_filename="source.wav", start_sec=0, end_sec=3,
                    duration_sec=3, score=.8, periodicity=.8, level_db=-18, peak=.4, clipped_fraction=0,
                    accepted=True, reasons=[])],
                references=[VoiceReferenceCandidate(id="ref", segment_id="clip", source_filename="source.wav",
                    start_sec=0, end_sec=3, duration_sec=3, score=.8)], accepted_seconds=3),
            fingerprints=preparation.fingerprints(self.voice_path),
            samples={"clip": preparation.SampleFiles(original="recordings/source.wav")},
            references={"ref": preparation.SampleFiles(original="recordings/source.wav")}))
        for name, value in [("_db", None), ("DATA_DIR", self.root / "library"), ("DB_PATH", self.root / "library/catalog.db"), ("FILES_DIR", self.root / "library/files")]:
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        self.addCleanup(self.connection.close)
        self.audio = db.model_dir('upload') / "audio.wav"
        self.audio.write_bytes(b"marker")
        self.track_id = db.insert_track(model='upload', title='Fixture', lyrics='', seed=None, duration_ms=4000,
            wall_ms=None, params={}, audio_path=self.audio, abc_path=None)
        self.enterContext(patch.object(voice, 'SEED_VC_DIR', self.root / 'engine'))
        self.enterContext(patch.object(voice, "VOICES_DIR", self.root))
        self.enterContext(patch.object(video, "DATA_DIR", self.root))
        self.enterContext(patch.dict(voice._builds, {}, clear=True))
        self.enterContext(patch.dict(voice._applies, {}, clear=True))
        self.enterContext(patch.dict(stems._jobs, {}, clear=True))
        self.enterContext(patch.dict(midi._jobs, {}, clear=True))
        self.enterContext(patch.object(video, "_current", None))
        self.addAsyncCleanup(self._drain_test_tasks)

    async def _drain_test_tasks(self) -> None:
        pending = asyncio.all_tasks() - self.original_tasks - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _start_video(self) -> video.VideoJob:
        binary = self.root / "engine"
        binary.write_bytes(b"marker")
        with (
            patch.object(video.db, "get_track", return_value={"id": 1, "audio_path": str(self.audio), "duration_ms": 5000, "title": "Track"}),
            patch.object(video, "engine_binary", return_value=binary),
            patch.object(video, "_tool", return_value=sys.executable),
        ):
            await video.start_video(1, "a singer", 4, 0)
        job = video._current
        if job is None:
            raise AssertionError("Video job not registered")
        return job

    async def test_stem_cancel_finishes_a_job_waiting_for_the_gpu(self) -> None:
        lock = asyncio.Lock()
        with patch.object(stems, "_gpu_lock", lock):
            async with lock:
                job = await stems.start(1)
                await asyncio.sleep(0)
                result = await stems.cancel(1)
                self.assertEqual(result["status"], "cancelled")
                self.assertFalse(stems.work_busy())
                self.assertIsNone(job.proc)
            await asyncio.sleep(0)

    async def test_video_does_not_register_work_during_module_setup(self) -> None:
        with patch('app.module_jobs.work_busy', return_value=True):
            with self.assertRaises(video.VideoJobError) as caught:
                await self._start_video()
        self.assertEqual(caught.exception.code, 'busy')
        self.assertIsNone(video._current)

    async def test_midi_cancel_finishes_a_task_before_it_first_runs(self) -> None:
        job = await midi.start(1, "full")
        result = await midi.cancel(1, "full")
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(job.task)
        if job.task is not None:
            self.assertTrue(job.task.done())

    async def test_cancel_build_awaits_queued_task_without_starting_model_work(self) -> None:
        with patch.object(voice, "_build_inner", new=AsyncMock()) as inner:
            voice.start_build(self.voice_id, preparation_revision="reviewed")
            result = await voice.cancel_build(self.voice_id)
            self.assertEqual(result["status"], "cancelled")
            self.assertNotIn(self.voice_id, voice._builds)
            inner.assert_not_awaited()

    async def test_release_voice_drains_both_build_and_apply_tasks(self) -> None:
        with (
            patch.object(voice, "_build_inner", new=AsyncMock()) as build,
            patch.object(voice, "_apply_inner", new=AsyncMock()) as apply,
            patch.object(voice, "_artifact_flags", return_value=(True, True)),
        ):
            voice.start_build(self.voice_id, preparation_revision="reviewed")
            voice.start_apply(self.voice_id, 1)
            await voice.release_voice(self.voice_id)
            self.assertFalse(voice.work_busy())
            self.assertFalse(voice._builds)
            self.assertFalse(voice._applies)
            build.assert_not_awaited()
            apply.assert_not_awaited()
            self.assertEqual(voice.apply_status(1)["status"], "cancelled")

    async def test_cancel_video_awaits_a_task_before_it_first_runs(self) -> None:
        with patch.object(video, "_run", new=AsyncMock()) as run:
            job = await self._start_video()
            result = await video.cancel_video(job.id)
            self.assertEqual(result["status"], "cancelled")
            self.assertIsNotNone(job.task)
            if job.task is not None:
                self.assertTrue(job.task.done())
            self.assertIsNone(video._current)
            run.assert_not_awaited()

    async def test_delete_video_cancels_before_removing_its_files(self) -> None:
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        cancelled = asyncio.Event()

        async def run(job: video.VideoJob) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await asyncio.sleep(0)
                self.assertTrue(video._video_dir(job.id).is_dir())
                cleaned.set()
                raise

        with patch.object(video, "_run", side_effect=run):
            job = await self._start_video()
            await entered.wait()
            deletion = asyncio.create_task(video.delete_video(job.id))
            try:
                await asyncio.wait({deletion}, timeout=0.3)
                self.assertTrue(cancelled.is_set(), "Deletion did not cancel its job")
                await deletion
                self.assertTrue(cleaned.is_set())
                self.assertFalse(video._video_dir(job.id).exists())
            finally:
                deletion.cancel()
                await asyncio.gather(deletion, return_exceptions=True)

    async def test_shutdown_drains_stem_tasks(self) -> None:
        lock = asyncio.Lock()
        with patch.object(stems, "_gpu_lock", lock):
            async with lock:
                await stems.start(1)
                await stems.shutdown()
                self.assertFalse(stems.work_busy())

    async def test_shutdown_drains_midi_tasks_and_closes_client(self) -> None:
        with patch.object(midi, "_client", new=AsyncMock()) as client:
            await midi.start(1, "full")
            await midi.shutdown()
            self.assertFalse(midi.work_busy())
            client.aclose.assert_awaited_once()

    async def test_shutdown_drains_voice_tasks(self) -> None:
        with patch.object(voice, "_build_inner", new=AsyncMock()) as inner:
            voice.start_build(self.voice_id, preparation_revision="reviewed")
            await voice.shutdown()
            self.assertFalse(voice.work_busy())
            inner.assert_not_awaited()

    async def test_shutdown_drains_video_tasks(self) -> None:
        with patch.object(video, "_run", new=AsyncMock()) as run:
            job = await self._start_video()
            await video.shutdown()
            self.assertIsNone(video._current)
            if job.task is not None:
                self.assertTrue(job.task.done())
            run.assert_not_awaited()

    async def test_cancelling_the_cancel_request_still_awaits_job_cleanup(self) -> None:
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleaned = asyncio.Event()

        async def worker() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await finish_cleanup.wait()
                cleaned.set()
                raise

        job = asyncio.create_task(worker())
        await entered.wait()
        cancellation = asyncio.create_task(cancel_and_wait(job))
        await cleaning.wait()
        cancellation.cancel()
        await asyncio.sleep(0)
        self.assertFalse(cancellation.done(), "Cancellation returned before resource cleanup")
        finish_cleanup.set()
        await asyncio.gather(cancellation, return_exceptions=True)
        self.assertTrue(cleaned.is_set())

    async def test_internal_errors_are_logged_but_not_returned_in_status(self) -> None:
        secret = "/private/library/token-secret"
        stem = stems.StemJob(status="queued")
        stems._jobs[1] = stem
        with patch.object(stems.db, "get_track", side_effect=OSError(secret)), self.assertLogs("app.stems", level="ERROR") as logs:
            await stems._run(1)
        self.assertNotIn(secret, stem.error or "")
        self.assertIn(secret, " ".join(logs.output))

        transcription = midi.MidiJob(status="queued")
        midi._jobs[(1, "full")] = transcription
        with patch.object(midi.db, "get_track", side_effect=OSError(secret)), self.assertLogs("app.midi", level="ERROR"):
            await midi._run(1, "full")
        self.assertNotIn(secret, transcription.error or "")

        build = voice.BuildJob(voice_id=self.voice_id)
        with patch.object(voice, "_build_inner", new=AsyncMock(side_effect=OSError(secret))), self.assertLogs("app.voice_build", level="ERROR"):
            await voice._run_build(build)
        self.assertEqual(build.error_code, "unknown")
        self.assertNotIn(secret, build.error)

        with patch.object(video, "_run", new=AsyncMock()):
            job = await self._start_video()
        with patch.object(video.db, "get_track", side_effect=OSError(secret)), self.assertLogs("app.video_jobs", level="ERROR"):
            await video._run(job)
        self.assertEqual(job.error_code, "unknown")
        self.assertNotIn(secret, job.error)

    async def test_record_apply_failure_persists_a_public_status(self) -> None:
        voice.record_apply_failure(self.voice_id, 1, "not_ready")
        result = voice.apply_status(1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "not_ready")
        self.assertEqual(result["voice_id"], self.voice_id)

    async def test_cancelled_voice_apply_cleans_only_owned_temporary_outputs(self) -> None:
        work = self.root / "conversion-work"
        work.mkdir()
        (work / "stem.wav").write_bytes(b"temporary stem")
        partial = self.root / "track.voiced.partial.wav"
        partial.write_bytes(b"partial")
        original = self.root / "track.wav"
        original.write_bytes(b"original")
        job = voice.ApplyJob(voice_id=self.voice_id, track_id=1)
        job.work_dir = work
        job.partial_outputs = {partial}
        with patch.object(voice, "_apply_inner", new=AsyncMock(side_effect=voice.BuildCancelled)):
            await voice._run_apply(job)
        self.assertFalse(work.exists())
        self.assertFalse(partial.exists())
        self.assertEqual(original.read_bytes(), b"original")

    async def test_cancelled_voice_build_cleans_work_but_preserves_its_recordings(self) -> None:
        work = self.voice_path / "work"
        work.mkdir()
        (work / "partial.wav").write_bytes(b"partial")
        recordings = self.voice_path / "recordings"
        recordings.mkdir(exist_ok=True)
        original = recordings / "source.wav"
        original.write_bytes(b"original")
        job = voice.BuildJob(voice_id=self.voice_id)
        job.work_dir = work
        with patch.object(voice, "_build_inner", new=AsyncMock(side_effect=voice.BuildCancelled)):
            await voice._run_build(job)
        self.assertFalse(work.exists())
        self.assertEqual(original.read_bytes(), b"original")

    async def test_cancelled_voice_release_drains_every_associated_job(self) -> None:
        build_running = asyncio.Event()
        apply_running = asyncio.Event()
        build_cleaning = asyncio.Event()
        apply_cleaning = asyncio.Event()
        finish_build = asyncio.Event()
        finish_apply = asyncio.Event()

        async def build_inner(job: voice.BuildJob) -> None:
            build_running.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                build_cleaning.set()
                await finish_build.wait()
                raise

        async def apply_inner(job: voice.ApplyJob) -> None:
            apply_running.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                apply_cleaning.set()
                await finish_apply.wait()
                raise

        with (
            patch.object(voice, "_build_inner", side_effect=build_inner),
            patch.object(voice, "_apply_inner", side_effect=apply_inner),
            patch.object(voice, "_artifact_flags", return_value=(True, True)),
        ):
            voice.start_build(self.voice_id, preparation_revision="reviewed")
            voice.start_apply(self.voice_id, 1)
            await build_running.wait()
            await apply_running.wait()
            release = asyncio.create_task(voice.release_voice(self.voice_id))
            await build_cleaning.wait()
            await apply_cleaning.wait()
            release.cancel()
            finish_build.set()
            try:
                await asyncio.wait({release}, timeout=0.02)
                self.assertFalse(release.done(), "Release returned while an associated conversion was still cleaning up")
            finally:
                finish_apply.set()
                await asyncio.gather(release, return_exceptions=True)
            self.assertFalse(voice._builds)
            self.assertFalse(voice._applies)


@unittest.skipIf(sys.platform == "win32", "POSIX descendant cancellation")
class SubprocessOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_killing_a_process_drains_full_pipes_before_awaiting_exit(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(b'x'*1048576);sys.stdout.flush();time.sleep(60)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True, limit=1024,
        )
        killing: asyncio.Task[None] | None = None
        try:
            await asyncio.sleep(0.05)
            killing = asyncio.create_task(stems._kill_tree(proc))
            await asyncio.wait({killing}, timeout=0.3)
            self.assertTrue(killing.done(), "Process exit was blocked by an undrained pipe")
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            if killing is not None:
                killing.cancel()
                await asyncio.gather(killing, return_exceptions=True)

    async def _cancel_process(self, operation: Callable[[Path], Awaitable[object]], current_process: Callable[[], asyncio.subprocess.Process | None]) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child.pid"

            async def invoke() -> None:
                await operation(marker)

            task = asyncio.create_task(invoke())
            pid: int | None = None
            process: asyncio.subprocess.Process | None = None
            try:
                async with asyncio.timeout(5):
                    while not marker.is_file():
                        await asyncio.sleep(0.01)
                pid = int(marker.read_text())
                process = current_process()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                inspector = await asyncio.create_subprocess_exec("ps", "-o", "stat=", "-p", str(pid), stdout=asyncio.subprocess.PIPE)
                output, _ = await inspector.communicate()
                self.assertTrue(not output.strip() or output.strip().startswith(b"Z"), output.decode())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if process is not None:
                    await stems._kill_tree(process)
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @staticmethod
    def _command(marker: Path) -> list[str]:
        child = "import time; time.sleep(60)"
        wrapper = (
            "import subprocess,sys,time,pathlib;"
            f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid));"
            "time.sleep(60)"
        )
        return [sys.executable, "-c", wrapper]

    async def test_voice_subprocess_cancellation_awaits_the_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slot = voice.ProcSlot()

            async def operation(marker: Path) -> int:
                return await voice._spawn(self._command(marker), cwd=root, log_name="test", slot=slot)

            with patch.object(voice, "LOG_DIR", root), patch.object(voice, "_child_env", return_value=os.environ.copy()):
                await self._cancel_process(operation, lambda: slot.proc)
            self.assertIsNone(slot.proc)

    async def test_video_subprocess_cancellation_awaits_the_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slot = video.ProcSlot()

            async def operation(marker: Path) -> int:
                return await video._spawn(self._command(marker), cwd=root, log_name="test", slot=slot)

            with patch.object(video, "LOG_DIR", root), patch.object(video, "_child_env", return_value=os.environ.copy()):
                await self._cancel_process(operation, lambda: slot.proc)
            self.assertIsNone(slot.proc)
