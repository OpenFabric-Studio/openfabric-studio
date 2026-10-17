"""Native dataset workers retain accelerator ownership after async submission."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app import work_busy, support_controls, resource_admission
from app.contracts import JsonObject
from app.orchestrator.state import ModelStatus


class MusicAnalysisWorkAccountingTests(unittest.TestCase):
    def test_music_cleanup_and_beats_block_library_setup_until_drained(self) -> None:
        for name in ('app.music_artifacts.work_busy', 'app.beat_jobs.work_busy', 'app.replacement_jobs.work_busy', 'app.beat_song_mix.work_busy', 'app.mashups.work_busy', 'app.regional_datasets.work_busy', 'app.queued_music_sources.work_busy', 'app.song_sources.work_busy'):
            with self.subTest(owner=name), patch(name, return_value=True):
                self.assertTrue(work_busy.local_work_busy())

    def test_analysis_blocks_idle_accounting_until_its_owner_is_drained(self) -> None:
        with patch('app.music_analysis.work_busy', return_value=False) as analysis_busy:
            self.assertFalse(work_busy.local_work_busy())
            analysis_busy.return_value = True
            self.assertTrue(work_busy.local_work_busy())
            analysis_busy.return_value = False
            self.assertFalse(work_busy.local_work_busy())


class NativeWorkAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.payloads: dict[str, JsonObject] = {
            '/v1/stats': {'jobs': {'queued': 0, 'running': 0}, 'queue_size': 0},
            '/v1/training/status': {'is_training': False},
            '/v1/dataset/auto_label_status': {'status': 'idle'},
            '/v1/dataset/preprocess_status': {'status': 'idle'},
        }
        self.failure_path = ''
        self.failure_kind = ''
        self.seen: set[str] = set()
        client_class = httpx.AsyncClient
        def client(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
            return client_class(transport=httpx.MockTransport(self.respond), trust_env=False)
        self.enterContext(patch.object(work_busy.httpx, 'AsyncClient', side_effect=client))
        self.enterContext(patch.object(work_busy.manager.state.models['ace_step'], 'status', ModelStatus.RUNNING))
        self.enterContext(patch.object(resource_admission, 'native_work_inflight', return_value=False))
        self.enterContext(patch.object(work_busy, 'local_work_busy', return_value=False))
        self.enterContext(patch('app.video_jobs.work_busy', return_value=False))
        self.enterContext(patch('app.ace_jobs.work_busy', return_value=False))
        self.enterContext(patch('app.speaker_review.work_busy', return_value=False))

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.add(path)
        if path == self.failure_path:
            if self.failure_kind == 'transport':
                raise httpx.ConnectError('Unavailable', request=request)
            if self.failure_kind == 'json':
                return httpx.Response(200, text='{partial')
            return httpx.Response(503, json={'error': 'unavailable'})
        return httpx.Response(200, json={'code': 200, 'data': self.payloads[path]})

    async def test_labeling_and_preprocessing_stay_busy_after_submission_lease_is_released(self) -> None:
        for endpoint in ('/v1/dataset/auto_label_status', '/v1/dataset/preprocess_status'):
            with self.subTest(endpoint=endpoint):
                self.payloads[endpoint] = {'status': 'running'}
                self.assertTrue(await work_busy.ace_work_busy())
                self.assertEqual(await support_controls._idle_state(), 'busy')
                self.payloads[endpoint] = {'status': 'completed'}

    async def test_all_documented_task_statuses_must_confirm_idle_before_admission(self) -> None:
        self.assertFalse(await work_busy.ace_work_busy())
        self.assertEqual(await support_controls._idle_state(), 'idle')
        self.assertEqual(self.seen, set(self.payloads))

    async def test_running_engine_accounting_failures_block_work_and_stopping_until_reconnected(self) -> None:
        for path in self.payloads:
            for kind in ('transport', 'json', 'http'):
                with self.subTest(path=path, kind=kind):
                    self.failure_path, self.failure_kind = path, kind
                    self.assertTrue(await work_busy.ace_work_busy())
                    self.assertEqual(await support_controls._idle_state(), 'unknown')
        self.failure_path = ''
        self.assertFalse(await work_busy.ace_work_busy())
        self.assertEqual(await support_controls._idle_state(), 'idle')

    async def test_malformed_native_task_accounting_never_authorizes_work_or_stop(self) -> None:
        for endpoint, payload in (
            ('/v1/stats', {'jobs': {'queued': False, 'running': 0}}),
            ('/v1/training/status', {'is_training': 'false'}),
            ('/v1/dataset/auto_label_status', {}),
            ('/v1/dataset/preprocess_status', {'status': 'unknown'}),
        ):
            with self.subTest(endpoint=endpoint):
                original = self.payloads[endpoint]
                self.payloads[endpoint] = payload
                self.assertTrue(await work_busy.ace_work_busy())
                self.assertEqual(await support_controls._idle_state(), 'unknown')
                self.payloads[endpoint] = original

    async def test_inactive_engine_does_not_probe_native_endpoints(self) -> None:
        with patch.object(work_busy.manager.state.models['ace_step'], 'status', ModelStatus.STOPPED):
            self.assertFalse(await work_busy.ace_work_busy())
            self.assertEqual(await support_controls._idle_state(), 'idle')
        self.assertEqual(self.seen, set())
