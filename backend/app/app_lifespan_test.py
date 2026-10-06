"""Startup failures must drain recovered work as well as normal shutdown."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import main


class AppLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_codec_recovery_precedes_narration_and_final_drain_follows_workers(self) -> None:
        self.enterContext(patch.object(main, 'ensure_layout'))
        self.enterContext(patch.object(main, 'place_seed_models'))
        for module, method in [(main.yue_upload, 'start'), (main.ace_jobs, 'recover'),
                               (main.yue_jobs, 'recover'), (main.speech_clone, 'start'),
                               (main.speech_clone, 'begin_shutdown'), (main.ebook_import, 'start'),
                               (main.manager, 'start_watchdog')]:
            self.enterContext(patch.object(module, method))
        for module, method in [(main.audio_versions, 'recover'), (main.audio_exports, 'recover_exports'),
                               (main.reference_imports, 'start'), (main.video_jobs, 'recover'),
                               (main.optional_engines, 'recover'), (main.video_character_training, 'recover'), (main.video_character_comparison, 'recover'),
                               (main.audiobook_workflows, 'start'), (main.audiobook_review, 'start'), (main.speaker_review, 'start'),
                               (main.module_jobs, 'recover')]:
            self.enterContext(patch.object(module, method, new=AsyncMock()))
        recovery = self.enterContext(patch.object(main.audiobook_publish, 'start', new=AsyncMock()))
        stopped = asyncio.Event()
        async def start_narration() -> None:
            recovery.assert_awaited_once()
        async def stop_narration() -> None:
            await asyncio.sleep(0)
            stopped.set()
        async def final_codec_drain() -> None:
            self.assertTrue(stopped.is_set(), 'Encoder quarantine must drain after narration exits')
        self.enterContext(patch.object(main.audiobooks, 'start', side_effect=start_narration))
        self.enterContext(patch.object(main.audiobooks, 'shutdown', side_effect=stop_narration))
        drain = self.enterContext(patch.object(main.audiobook_publish, 'shutdown', side_effect=final_codec_drain))
        for module, method in [(main.ace_jobs, 'shutdown'), (main.yue_jobs, 'shutdown'),
                               (main.voice_build, 'shutdown'), (main.audio_exports, 'shutdown_exports'),
                               (main.voice_comparisons, 'shutdown'), (main.video_jobs, 'shutdown'),
                               (main.stems, 'shutdown'), (main.midi, 'shutdown'), (main.tagging, 'shutdown'),
                               (main.reference_imports, 'shutdown'), (main.native_yue, 'shutdown'),
                               (main.audiobook_workflows, 'shutdown'), (main.audiobook_review, 'shutdown'), (main.speaker_review, 'shutdown'),
                               (main.ebook_import, 'shutdown'), (main.module_jobs, 'shutdown'),
                               (main.speech_clone, 'shutdown'), (main.yue_upload, 'shutdown'),
                               (main.optional_engines, 'shutdown'), (main.video_character_training, 'shutdown'), (main.video_character_comparison, 'shutdown'),
                               (main.manager, 'stop_all')]:
            self.enterContext(patch.object(module, method, new=AsyncMock()))
        async with main.lifespan(main.app):
            pass
        drain.assert_awaited_once()

    async def test_recovery_failure_drains_already_recovered_workers(self) -> None:
        entered = asyncio.Event()
        exited = asyncio.Event()
        workers: list[asyncio.Task[None]] = []

        async def worker() -> None:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                exited.set()

        def recover_yue() -> None:
            workers.append(asyncio.create_task(worker()))

        async def drain_yue() -> None:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        async def fail_reference_recovery() -> None:
            await entered.wait()
            raise OSError('private recovery path')

        self.addAsyncCleanup(drain_yue)
        self.enterContext(patch.object(main, 'ensure_layout'))
        self.enterContext(patch.object(main, 'place_seed_models'))
        self.enterContext(patch.object(main.speech_clone, 'begin_shutdown'))
        self.enterContext(patch.object(main.audio_versions, 'recover', new=AsyncMock()))
        self.enterContext(patch.object(main.audio_exports, 'recover_exports', new=AsyncMock()))
        self.enterContext(patch.object(main.ace_jobs, 'recover'))
        self.enterContext(patch.object(main.yue_jobs, 'recover', side_effect=recover_yue))
        self.enterContext(patch.object(main.reference_imports, 'start', side_effect=fail_reference_recovery))
        self.enterContext(patch.object(main.video_jobs, 'recover', new=AsyncMock()))
        self.enterContext(patch.object(main.manager, 'start_watchdog'))
        shutdown_yue = self.enterContext(patch.object(main.yue_jobs, 'shutdown', side_effect=drain_yue))
        cleanup = [self.enterContext(patch.object(module, method, new=AsyncMock())) for module, method in [
            (main.ace_jobs, 'shutdown'), (main.voice_build, 'shutdown'),
            (main.audio_exports, 'shutdown_exports'), (main.voice_comparisons, 'shutdown'),
            (main.video_jobs, 'shutdown'), (main.stems, 'shutdown'),
            (main.midi, 'shutdown'), (main.tagging, 'shutdown'),
            (main.reference_imports, 'shutdown'), (main.native_yue, 'shutdown'),
            (main.audiobooks, 'shutdown'), (main.ebook_import, 'shutdown'), (main.module_jobs, 'shutdown'), (main.speech_clone, 'shutdown'),
            (main.yue_upload, 'shutdown'), (main.optional_engines, 'shutdown'), (main.video_character_training, 'shutdown'),
            (main.video_character_comparison, 'shutdown'), (main.audiobook_workflows, 'shutdown'), (main.audiobook_review, 'shutdown'), (main.speaker_review, 'shutdown'), (main.audiobook_publish, 'shutdown'),
        ]]
        stop = self.enterContext(patch.object(main.manager, 'stop_all', new=AsyncMock()))

        with self.assertRaisesRegex(OSError, 'private recovery path'):
            async with main.lifespan(main.app):
                self.fail('Failed startup must never serve requests')

        shutdown_yue.assert_awaited_once()
        self.assertTrue(exited.is_set())
        self.assertTrue(all(task.done() for task in workers))
        for operation in cleanup:
            operation.assert_awaited_once()
        stop.assert_awaited_once()
