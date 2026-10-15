"""Startup failures must drain recovered work as well as normal shutdown."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import (
    ace_jobs, audio_exports, audio_versions, audiobooks, audiobook_publish,
    audiobook_workflows, audiobook_review, character_swaps, clip_hosting,
    cloud_music, ebook_import, local_music, main, midi, module_jobs, music_queue,
    native_yue, openrouter_client, optional_engines, reading_media, reference_imports,
    speaker_review, speech_clone, stable_lora, stems, tagging,
    video_character_training, video_character_comparison, video_jobs, video_scenes,
    voice_build, voice_comparisons, yue_jobs, yue_upload,
)
from app.orchestrator.manager import manager


class AppLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(openrouter_client, 'start'))
        self.enterContext(patch('app.production_render.recover'))
        self.enterContext(patch('app.voice_retention.reconcile_deletions'))
        for module, method in [(cloud_music, 'recover'), (cloud_music, 'shutdown'),
                               (local_music, 'recover'), (local_music, 'shutdown'),
                               (stable_lora, 'recover'), (stable_lora, 'shutdown'),
                               (openrouter_client, 'shutdown'),
                               (reading_media, 'start'), (reading_media, 'shutdown'),
                               (music_queue, 'recover'), (music_queue, 'shutdown')]:
            self.enterContext(patch.object(module, method, new=AsyncMock()))

    async def test_codec_recovery_precedes_narration_and_final_drain_follows_workers(self) -> None:
        self.enterContext(patch.object(main, 'ensure_layout'))
        self.enterContext(patch.object(main, 'place_seed_models'))
        for module, method in [(yue_upload, 'start'), (speech_clone, 'start'),
                               (speech_clone, 'begin_shutdown'), (ebook_import, 'start'),
                               (manager, 'start_watchdog')]:
            self.enterContext(patch.object(module, method))
        for module, method in [(audio_versions, 'recover'), (audio_exports, 'recover_exports'),
                               (reference_imports, 'start'), (video_jobs, 'recover'),
                               (optional_engines, 'recover'), (video_character_training, 'recover'), (video_character_comparison, 'recover'),
                               (audiobook_workflows, 'start'), (audiobook_review, 'start'), (speaker_review, 'start'),
                               (module_jobs, 'recover')]:
            self.enterContext(patch.object(module, method, new=AsyncMock()))
        ace_recovery = self.enterContext(patch.object(ace_jobs, 'recover'))
        yue_recovery = self.enterContext(patch.object(yue_jobs, 'recover'))
        async def recover_queue() -> None:
            ace_recovery.assert_called_once_with()
            yue_recovery.assert_called_once_with()
        queue_recovery = self.enterContext(patch.object(music_queue, 'recover', side_effect=recover_queue))
        queue_drain = self.enterContext(patch.object(music_queue, 'shutdown', new=AsyncMock()))
        training_recovery = self.enterContext(patch.object(stable_lora, 'recover', new=AsyncMock()))
        training_drain = self.enterContext(patch.object(stable_lora, 'shutdown', new=AsyncMock()))
        recovery = self.enterContext(patch.object(audiobook_publish, 'start', new=AsyncMock()))
        scene_recovery = self.enterContext(patch.object(video_scenes, 'recover', new=AsyncMock()))
        scene_drain = self.enterContext(patch.object(video_scenes, 'shutdown', new=AsyncMock()))
        clip_recovery = self.enterContext(patch.object(clip_hosting, 'recover', new=AsyncMock()))
        clips_stopped = asyncio.Event()
        async def recover_swaps() -> None:
            clip_recovery.assert_awaited_once()
        async def stop_swaps() -> None:
            await asyncio.sleep(0)
            clips_stopped.set()
        async def drain_clip_relay() -> None:
            self.assertTrue(clips_stopped.is_set(), 'Clip relay must drain after swap workers exit')
        self.enterContext(patch.object(character_swaps, 'recover', side_effect=recover_swaps))
        self.enterContext(patch.object(character_swaps, 'shutdown', side_effect=stop_swaps))
        clip_drain = self.enterContext(patch.object(clip_hosting, 'shutdown', side_effect=drain_clip_relay))
        stopped = asyncio.Event()
        async def start_narration() -> None:
            recovery.assert_awaited_once()
        async def stop_narration() -> None:
            await asyncio.sleep(0)
            stopped.set()
        async def final_codec_drain() -> None:
            self.assertTrue(stopped.is_set(), 'Encoder quarantine must drain after narration exits')
        self.enterContext(patch.object(audiobooks, 'start', side_effect=start_narration))
        self.enterContext(patch.object(audiobooks, 'shutdown', side_effect=stop_narration))
        drain = self.enterContext(patch.object(audiobook_publish, 'shutdown', side_effect=final_codec_drain))
        for module, method in [(ace_jobs, 'shutdown'), (yue_jobs, 'shutdown'),
                               (voice_build, 'shutdown'), (audio_exports, 'shutdown_exports'),
                               (voice_comparisons, 'shutdown'), (video_jobs, 'shutdown'),
                               (stems, 'shutdown'), (midi, 'shutdown'), (tagging, 'shutdown'),
                               (reference_imports, 'shutdown'), (native_yue, 'shutdown'),
                               (audiobook_workflows, 'shutdown'), (audiobook_review, 'shutdown'), (speaker_review, 'shutdown'),
                               (ebook_import, 'shutdown'), (module_jobs, 'shutdown'),
                               (speech_clone, 'shutdown'), (yue_upload, 'shutdown'),
                               (optional_engines, 'shutdown'), (video_character_training, 'shutdown'), (video_character_comparison, 'shutdown'),
                               (manager, 'stop_all')]:
            self.enterContext(patch.object(module, method, new=AsyncMock()))
        async def drain_engine_jobs() -> None:
            queue_drain.assert_awaited_once()
        self.enterContext(patch.object(ace_jobs, 'shutdown', side_effect=drain_engine_jobs))
        self.enterContext(patch.object(yue_jobs, 'shutdown', side_effect=drain_engine_jobs))
        async with main.lifespan(main.app):
            queue_recovery.assert_awaited_once()
            queue_drain.assert_not_awaited()
            training_recovery.assert_awaited_once()
            training_drain.assert_not_awaited()
            scene_recovery.assert_awaited_once()
            scene_drain.assert_not_awaited()
        training_drain.assert_awaited_once()
        drain.assert_awaited_once()
        scene_drain.assert_awaited_once()
        clip_drain.assert_awaited_once()
        queue_drain.assert_awaited_once()

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
        self.enterContext(patch.object(speech_clone, 'begin_shutdown'))
        self.enterContext(patch.object(audio_versions, 'recover', new=AsyncMock()))
        self.enterContext(patch.object(audio_exports, 'recover_exports', new=AsyncMock()))
        self.enterContext(patch.object(ace_jobs, 'recover'))
        self.enterContext(patch.object(yue_jobs, 'recover', side_effect=recover_yue))
        self.enterContext(patch.object(reference_imports, 'start', side_effect=fail_reference_recovery))
        self.enterContext(patch.object(video_jobs, 'recover', new=AsyncMock()))
        self.enterContext(patch.object(manager, 'start_watchdog'))
        queue_recovery = self.enterContext(patch.object(music_queue, 'recover', new=AsyncMock()))
        queue_drain = self.enterContext(patch.object(music_queue, 'shutdown', new=AsyncMock()))
        shutdown_yue = self.enterContext(patch.object(yue_jobs, 'shutdown', side_effect=drain_yue))
        cleanup = [self.enterContext(patch.object(module, method, new=AsyncMock())) for module, method in [
            (ace_jobs, 'shutdown'), (voice_build, 'shutdown'),
            (audio_exports, 'shutdown_exports'), (voice_comparisons, 'shutdown'),
            (video_jobs, 'shutdown'), (stems, 'shutdown'),
            (video_scenes, 'shutdown'),
            (clip_hosting, 'shutdown'),
            (midi, 'shutdown'), (tagging, 'shutdown'),
            (reference_imports, 'shutdown'), (native_yue, 'shutdown'),
            (audiobooks, 'shutdown'), (ebook_import, 'shutdown'), (module_jobs, 'shutdown'), (speech_clone, 'shutdown'),
            (yue_upload, 'shutdown'), (optional_engines, 'shutdown'), (video_character_training, 'shutdown'),
            (video_character_comparison, 'shutdown'), (audiobook_workflows, 'shutdown'), (audiobook_review, 'shutdown'), (speaker_review, 'shutdown'), (audiobook_publish, 'shutdown'),
        ]]
        stop = self.enterContext(patch.object(manager, 'stop_all', new=AsyncMock()))

        with self.assertRaisesRegex(OSError, 'private recovery path'):
            async with main.lifespan(main.app):
                self.fail('Failed startup must never serve requests')

        shutdown_yue.assert_awaited_once()
        self.assertTrue(exited.is_set())
        self.assertTrue(all(task.done() for task in workers))
        for operation in cleanup:
            operation.assert_awaited_once()
        stop.assert_awaited_once()
        queue_recovery.assert_awaited_once()
        queue_drain.assert_awaited_once()

    async def test_queue_shutdown_failure_still_drains_engine_jobs_and_later_cleanup(self) -> None:
        self.enterContext(patch.object(main, 'ensure_layout', side_effect=RuntimeError('startup interrupted')))
        self.enterContext(patch.object(speech_clone, 'begin_shutdown'))
        events: list[str] = []
        async def fail_queue() -> None:
            events.append('queue')
            raise OSError('queue drain failure')
        async def drain_yue() -> None:
            self.assertEqual(events, ['queue'])
            events.append('yue')
        async def drain_ace() -> None:
            self.assertEqual(events, ['queue', 'yue'])
            events.append('ace')
        self.enterContext(patch.object(music_queue, 'shutdown', side_effect=fail_queue))
        self.enterContext(patch.object(yue_jobs, 'shutdown', side_effect=drain_yue))
        self.enterContext(patch.object(ace_jobs, 'shutdown', side_effect=drain_ace))
        cleanup = [self.enterContext(patch.object(module, method, new=AsyncMock())) for module, method in [
            (cloud_music, 'shutdown'), (local_music, 'shutdown'), (stable_lora, 'shutdown'),
            (openrouter_client, 'shutdown'), (voice_build, 'shutdown'),
            (audio_exports, 'shutdown_exports'), (voice_comparisons, 'shutdown'),
            (video_jobs, 'shutdown'), (character_swaps, 'shutdown'), (video_scenes, 'shutdown'),
            (stems, 'shutdown'), (midi, 'shutdown'), (tagging, 'shutdown'),
            (reference_imports, 'shutdown'), (audiobooks, 'shutdown'),
            (audiobook_workflows, 'shutdown'), (audiobook_review, 'shutdown'),
            (speaker_review, 'shutdown'), (reading_media, 'shutdown'),
            (ebook_import, 'shutdown'), (module_jobs, 'shutdown'), (speech_clone, 'shutdown'),
            (optional_engines, 'shutdown'), (video_character_training, 'shutdown'),
            (video_character_comparison, 'shutdown'), (yue_upload, 'shutdown'),
            (clip_hosting, 'shutdown'), (audiobook_publish, 'shutdown'), (native_yue, 'shutdown'),
        ]]
        async def stop_native_engines() -> None:
            self.assertEqual(events, ['queue', 'yue', 'ace'])
            events.append('engines')
        stop = self.enterContext(patch.object(manager, 'stop_all', side_effect=stop_native_engines))
        with self.assertRaisesRegex(OSError, 'queue drain failure'):
            async with main.lifespan(main.app):
                self.fail('Interrupted startup must not serve requests')
        for operation in cleanup:
            operation.assert_awaited_once()
        stop.assert_awaited_once()
        self.assertEqual(events, ['queue', 'yue', 'ace', 'engines'])
