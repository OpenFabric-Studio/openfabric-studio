from __future__ import annotations

import asyncio
import tempfile
import unittest
import wave
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app.video_process import spawn_owned
from app.resource_admission import reserve_native
from app import audiobooks, audiobook_review as qa
from app.audiobook_review_contracts import AsrCapability, AsrReviewRequest


class AsrReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.enterContext(patch.object(audiobooks, "BOOKS_ROOT", Path(self.temporary.name) / "books"))
        self.audio = audiobooks.book_dir("a" * 32) / "fixture.wav"
        with wave.open(str(self.audio), "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 32000)
        self.snapshot = qa.PassageSnapshot(book_id="a" * 32, chapter_index=0, passage_id="b" * 32,
            revision=1, render_identity="c" * 64, audio_path=self.audio, text="one two three", language="en")
        self.enterContext(patch.object(qa, "_snapshot", return_value=self.snapshot))
        await qa.start()
        self.addAsyncCleanup(qa.shutdown)

    async def settle(self, identifier: str) -> None:
        async with asyncio.timeout(5):
            while qa.get(identifier).state in {"queued", "running"}:
                await asyncio.sleep(0.01)

    async def create(self):
        return await qa.create("a" * 32, 0, "b" * 32, AsrReviewRequest(revision=1, render_identity="c" * 64))

    def test_comparison_flags_are_review_hints_and_never_rerender(self) -> None:
        flags = qa.compare_transcript("one two three", "one one three", 2000)
        self.assertIn("omission", [flag.code for flag in flags])
        self.assertIn("repeat", [flag.code for flag in flags])
        self.assertEqual(qa.compare_transcript("Hello, world!", "hello world", 2000), [])
        self.assertIn("duration", [flag.code for flag in qa.compare_transcript("word " * 50, "word " * 50, 300)])

    async def test_unavailable_is_persisted_without_installing_or_launching(self) -> None:
        with patch.object(qa, "capability", return_value=AsrCapability(available=False, reason="whisper_missing")), \
             patch.object(qa, "spawn_owned") as spawn:
            result = await self.create()
        self.assertEqual(result.state, "unavailable")
        self.assertEqual(qa.get(result.id), result)
        self.assertEqual(qa.list_reviews("a" * 32).reviews, [result])
        spawn.assert_not_called()

    async def test_flags_and_target_provenance_persist_after_reload(self) -> None:
        async def transcribe(identifier: str, snapshot: qa.PassageSnapshot, workspace: Path) -> str:
            return "one one three"
        with patch.object(qa, "capability", return_value=AsrCapability(available=True)), \
             patch.object(qa, "_transcribe", side_effect=transcribe):
            result = await self.create()
            await self.settle(result.id)
        finished = qa.get(result.id)
        self.assertEqual(finished.state, "completed")
        self.assertEqual(len(finished.audio_sha256), 64)
        self.assertEqual(finished.revision, 1)
        self.assertTrue(finished.flags)
        await qa.shutdown(); await qa.start()
        self.assertEqual(qa.get(result.id), finished)

    async def test_revision_changes_skip_publication_of_stale_flags(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        async def transcribe(identifier: str, snapshot: qa.PassageSnapshot, workspace: Path) -> str:
            entered.set(); await release.wait(); return "one one three"
        with patch.object(qa, "capability", return_value=AsrCapability(available=True)), \
             patch.object(qa, "_transcribe", side_effect=transcribe):
            result = await self.create(); await entered.wait()
            replacement = qa.PassageSnapshot(book_id="a" * 32, chapter_index=0, passage_id="b" * 32,
                revision=2, render_identity="d" * 64, audio_path=self.audio, text="edited", language="en")
            with patch.object(qa, "_snapshot", return_value=replacement):
                release.set(); await self.settle(result.id)
        self.assertEqual(qa.get(result.id).state, "skipped")
        self.assertEqual(qa.get(result.id).reason, "passage_changed")
        self.assertEqual(qa.get(result.id).flags, [])

    async def test_cancel_waits_for_owned_work_and_persists_cancelled_state(self) -> None:
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def transcribe(identifier: str, snapshot: qa.PassageSnapshot, workspace: Path) -> str:
            try:
                entered.set(); await asyncio.Future[None](); return "unused"
            finally:
                cleaned.set()
        with patch.object(qa, "capability", return_value=AsrCapability(available=True)), \
             patch.object(qa, "_transcribe", side_effect=transcribe):
            result = await self.create(); await entered.wait()
            self.assertTrue(qa.work_busy())
            canceled = await qa.cancel(result.id)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(canceled.state, "canceled")
        self.assertFalse(qa.work_busy())
        self.assertFalse(qa.workspace_path(result.id).exists())

    async def test_cancel_before_worker_start_and_restart_recovery_are_explicit(self) -> None:
        with patch.object(qa, "capability", return_value=AsrCapability(available=True)):
            result = await self.create()
            canceled = await qa.cancel(result.id)
        self.assertEqual(canceled.state, "canceled")
        qa._state(result.id, "running")
        await qa.shutdown(); await qa.start()
        self.assertEqual(qa.get(result.id).state, "canceled")
        self.assertEqual(qa.get(result.id).reason, "interrupted_by_restart")

    async def test_native_mutation_and_setup_exclude_new_qa_without_launch(self) -> None:
        lease = await reserve_native(lambda: False)
        try:
            with self.assertRaises(qa.AudiobookReviewError) as failure:
                await self.create()
            self.assertEqual(failure.exception.code, "asr_native_work_busy")
        finally:
            await lease.release()
        from fastapi import HTTPException
        with patch("app.module_jobs.work_busy", return_value=True):
            with self.assertRaises(HTTPException):
                await self.create()

    async def test_real_owned_fake_cli_uses_temp_pcm_and_target_language(self) -> None:
        from app import reference_imports
        binary = Path(self.temporary.name) / "whisper-cli"
        model = Path(self.temporary.name) / "fixture.bin"
        model.write_bytes(b"synthetic model boundary")
        binary.write_text(f"#!{sys.executable}\nimport sys,wave\nfrom pathlib import Path\n"
            "assert '-otxt' in sys.argv and sys.argv[sys.argv.index('-l')+1] == 'en'\n"
            "with wave.open(sys.argv[sys.argv.index('-f')+1], 'rb') as audio:\n assert audio.getframerate()==16000 and audio.getnchannels()==1 and audio.getsampwidth()==2\n"
            "Path(sys.argv[sys.argv.index('-of')+1]+'.txt').write_text('one one three')\n")
        binary.chmod(0o700)
        with patch.object(reference_imports, "_whisper", return_value=(str(binary), model)):
            result = await self.create(); await self.settle(result.id)
        self.assertEqual(qa.get(result.id).state, "completed")
        self.assertTrue(qa.get(result.id).flags)
        self.assertIsNone(qa._read(result.id).worker)
        self.assertFalse(qa.workspace_path(result.id).exists())

    async def test_failed_owned_drain_keeps_files_and_admission_until_shutdown_retry(self) -> None:
        workers: list[asyncio.subprocess.Process] = []
        async def transcribe(identifier: str, snapshot: qa.PassageSnapshot, workspace: Path) -> str:
            worker = await spawn_owned([sys.executable, "-c", "import time;time.sleep(60)"],
                receipt_path=workspace / "fixture-worker.json", stdout=asyncio.subprocess.PIPE)
            workers.append(worker)
            qa._PROCESSES[identifier] = worker
            await qa._drain(identifier, worker)
            return "unused"
        with patch.object(qa, "capability", return_value=AsrCapability(available=True)), \
             patch.object(qa, "_transcribe", side_effect=transcribe), \
             patch.object(qa, "kill_process_tree", new=AsyncMock(side_effect=RuntimeError("unverified drain"))):
            result = await self.create(); await self.settle(result.id)
        self.assertTrue(qa.work_busy())
        self.assertTrue(qa.workspace_path(result.id).exists())
        self.assertIsNone(workers[0].returncode)
        await qa.shutdown()
        self.assertIsNotNone(workers[0].returncode)
        self.assertFalse(qa.work_busy())
        self.assertFalse(qa.workspace_path(result.id).exists())
