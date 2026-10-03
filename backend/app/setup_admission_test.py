"""Setup excludes queued local work before it touches model/tool files."""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import midi, stems, video_render, voice_build, voice_comparisons, voice_preparation
from app.api import routes_ace_jobs, routes_orchestrator
from app.client_contracts import SwitchRequest
from app.voice_contracts import AnalyzeVoiceCoverageRequest, PrepareVoiceRequest, VoiceComparisonRequest
from app.video_contracts import VideoRenderRequest


class SetupAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_rejects_queued_jobs_without_running_workers(self) -> None:
        with patch('app.module_jobs.work_busy', return_value=True), patch.dict(stems._jobs, {}, clear=True), patch.dict(midi._jobs, {}, clear=True):
            for operation in (stems.start(1), midi.start(1, 'full')):
                with self.assertRaises(HTTPException) as caught:
                    await operation
                self.assertEqual(caught.exception.detail, 'module_setup_busy')
            self.assertEqual(stems._jobs, {})
            self.assertEqual(midi._jobs, {})

    async def test_setup_rejects_voice_changes_before_loading_private_artifacts(self) -> None:
        root = Path('/nonexistent/setup-admission-fixture')
        with patch('app.module_jobs.work_busy', return_value=True):
            operations = (
                lambda: voice_build.start_build('a' * 32),
                lambda: voice_build.start_apply('a' * 32, 1),
                lambda: voice_preparation.start(root, PrepareVoiceRequest()),
                lambda: voice_preparation.start_coverage(root, AnalyzeVoiceCoverageRequest(revision='reviewed')),
                lambda: voice_comparisons.start_comparison('a' * 32, VoiceComparisonRequest(source_id='b' * 32, model_ids=['test'])),
            )
            for operation in operations:
                with self.assertRaises(HTTPException) as caught:
                    operation()
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail, 'module_setup_busy')

    async def test_setup_rejects_media_render_before_project_mutation(self) -> None:
        with patch('app.module_jobs.work_busy', return_value=True), patch.object(video_render.store, 'load') as load:
            with self.assertRaises(HTTPException) as caught:
                await video_render.start('a' * 32, VideoRenderRequest(revision=1))
        self.assertEqual(caught.exception.detail, 'module_setup_busy')
        load.assert_not_called()

    async def test_setup_rejects_engine_switch_and_generation_with_correct_error(self) -> None:
        with patch('app.module_jobs.work_busy', return_value=True), patch.object(routes_orchestrator.manager, 'switch_to', new=AsyncMock()) as switch, patch.object(routes_ace_jobs.ace_jobs, 'submit', new=AsyncMock()) as submit:
            with self.assertRaises(HTTPException) as switching:
                await routes_orchestrator.switch(SwitchRequest(model='ace_step'))
            self.assertEqual(switching.exception.detail, 'module_setup_busy')
            with self.assertRaises(HTTPException) as generating:
                await routes_ace_jobs.submit(params='{}', title='Test', voice_id=None, ctx_audio=None, settings=None)
            self.assertEqual(generating.exception.detail, 'module_setup_busy')
            switch.assert_not_awaited()
            submit.assert_not_awaited()
