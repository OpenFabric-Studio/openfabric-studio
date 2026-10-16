from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from app import training_caption_runtime as runtime
from app.orchestrator.state import ModelStatus


class TrainingCaptionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(runtime.manager, 'owned_instance', return_value='a' * 32))
        self.enterContext(patch.object(runtime.manager, 'stop_owned', new=AsyncMock()))
        self.session = runtime.CaptionSession()
        self.session.instance = 'a' * 32
        self.real_prepare_excerpt = runtime.prepare_excerpt
        self.enterContext(patch.object(runtime.manager.state.models['ace_step'], 'status', ModelStatus.RUNNING))
        self.enterContext(patch.object(runtime, 'prepare_excerpt', new=AsyncMock(return_value=None)))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    async def test_exact_analysis_protocol_and_bounded_caption(self) -> None:
        post = self.enterContext(patch.object(runtime, 'request', new=AsyncMock(side_effect=[
            {'task_id': 'native-1'}, [{'task_id': 'native-1', 'status': 1, 'result': json.dumps([
                {'status_message': 'Full Hardware Analysis Success', 'prompt': 'Warm strings.', 'lyrics': 'Never apply lyrics.'}])}]])))
        result = await self.session.analyze(Path(self.temp.name) / 'source.mp3', 12)
        self.assertEqual(result, 'Warm strings.')
        self.assertEqual(post.call_args_list[0].args[:2], ('POST', '/release_task'))
        self.assertTrue(post.call_args_list[0].args[2]['full_analysis_only'])
        self.assertFalse(self.session.inflight)

    async def test_initialization_explicitly_enables_existing_lm(self) -> None:
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=AsyncMock(side_effect=[
                {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': False},
                {'message': 'Model initialization completed', 'slot': 1, 'llm_initialized': True}])) as request:
            await self.session.start()
        self.assertEqual(request.call_args_list[1].args[:2], ('POST', '/v1/init'))
        self.assertTrue(request.call_args_list[1].args[2]['init_llm'])
        self.assertFalse(self.session.inflight)

    async def test_close_preserves_preexisting_fully_ready_engine(self) -> None:
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=AsyncMock(return_value={
                'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': True})):
            await self.session.start()
        await self.session.close()
        runtime.manager.stop_owned.assert_not_awaited()

    async def test_close_stops_engine_started_for_captioning_once(self) -> None:
        async def switch(_model: str) -> None:
            runtime.manager.state.models['ace_step'].status = ModelStatus.RUNNING
        runtime.manager.state.models['ace_step'].status = ModelStatus.STOPPED
        with patch.object(runtime, 'require_installed'), patch.object(runtime.manager, 'switch_to', new=switch), \
             patch.object(runtime, 'request', new=AsyncMock(return_value={
                'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': True})):
            await self.session.start()
        await self.session.close()
        await self.session.close()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)

    async def test_close_stops_analyzer_models_initialized_in_existing_engine(self) -> None:
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=AsyncMock(side_effect=[
                {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': False},
                {'message': 'Model initialization completed', 'slot': 1, 'llm_initialized': True}])):
            await self.session.start()
        await self.session.close()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)

    async def test_cancellation_during_initialization_still_closes_owned_models(self) -> None:
        entered = asyncio.Event()
        async def request(method: str, _endpoint: str, _body: object = None) -> dict[str, object]:
            if method == 'GET':
                return {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': False}
            entered.set()
            await asyncio.Event().wait()
            return {}
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=request):
            pending = asyncio.create_task(self.session.start())
            await entered.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        await self.session.close()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)

    async def test_close_never_stops_replacement_after_initializing_models(self) -> None:
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=AsyncMock(side_effect=[
                {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': False},
                {'message': 'Model initialization completed', 'slot': 1, 'llm_initialized': True}])):
            await self.session.start()
        with patch.object(runtime.manager, 'owned_instance', return_value='b' * 32):
            with self.assertRaises(runtime.CaptionError):
                await self.session.close()
        runtime.manager.stop_owned.assert_not_awaited()
        await self.session.close()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)

    async def test_failed_close_keeps_cleanup_ownership_for_verified_retry(self) -> None:
        with patch.object(runtime, 'require_installed'), patch.object(runtime, 'request', new=AsyncMock(side_effect=[
                {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': True, 'llm_initialized': False},
                {'message': 'Model initialization completed', 'slot': 1, 'llm_initialized': True}])):
            await self.session.start()
        with patch.object(runtime.manager, 'stop_owned', new=AsyncMock(side_effect=RuntimeError('stop failed'))):
            with self.assertRaises(runtime.CaptionError):
                await self.session.close()
        await self.session.close()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)

    async def test_rejects_empty_or_overlong_or_wrong_analysis_result(self) -> None:
        for result in ({'status_message': 'Full Hardware Analysis Success', 'prompt': ''},
                       {'status_message': 'Full Hardware Analysis Success', 'prompt': 'a' * 4001},
                       {'status_message': 'Success', 'prompt': 'Do not accept generation.'}):
            self.session.inflight = False
            with self.subTest(result=result), patch.object(runtime, 'request', new=AsyncMock(side_effect=[
                    {'task_id': 'native-1'}, [{'task_id': 'native-1', 'status': 1, 'result': json.dumps([result])}]])):
                with self.assertRaises(runtime.CaptionError):
                    await self.session.analyze(Path(self.temp.name) / 'source.wav', 12)
            self.assertFalse(self.session.inflight)

    async def test_cooperative_cancel_waits_for_terminal_before_release(self) -> None:
        self.session.inflight, self.session.task_id = True, 'native-1'
        with patch.object(runtime, 'request', new=AsyncMock(side_effect=[{'status': 'cancelling'},
                [{'task_id': 'native-1', 'status': 2, 'result': '[]'}]])) as request:
            await self.session.drain()
        self.assertEqual(request.await_count, 2)
        runtime.manager.stop_owned.assert_not_awaited()
        self.assertFalse(self.session.inflight)

    async def test_unconfirmed_work_stops_only_exact_owned_engine(self) -> None:
        self.session.inflight = True
        await self.session.drain()
        runtime.manager.stop_owned.assert_awaited_once_with('ace_step', 'a' * 32)
        self.assertFalse(self.session.inflight)

    async def test_replacement_engine_is_never_stopped(self) -> None:
        self.session.inflight = True
        with patch.object(runtime.manager, 'owned_instance', return_value='b' * 32):
            with self.assertRaises(runtime.CaptionError):
                await self.session.drain()
        runtime.manager.stop_owned.assert_not_awaited()
        self.assertTrue(self.session.inflight)

    async def test_missing_installed_assets_refuses_start(self) -> None:
        self.session.instance = None
        with patch.object(runtime, 'require_installed', side_effect=runtime.CaptionError('training_caption_setup_required')), \
             patch.object(runtime.manager, 'switch_to', new=AsyncMock()) as switch:
            with self.assertRaises(runtime.CaptionError):
                await self.session.start()
        switch.assert_not_awaited()

    async def test_native_error_details_are_never_returned(self) -> None:
        def response(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'code': 500, 'error': '/private/path SECRET', 'data': None})
        with patch.object(runtime, 'CLIENT_TRANSPORT', httpx.MockTransport(response)):
            with self.assertRaises(runtime.CaptionError) as failure:
                await runtime.request('POST', '/v1/init', {'init_llm': True})
        self.assertNotIn('SECRET', str(failure.exception))

    async def test_excerpt_uses_only_first_seconds_and_rejects_invalid_pcm(self) -> None:
        with patch.object(runtime, 'tool', return_value='ffmpeg'), patch.object(runtime, 'run_owned_command', new=AsyncMock(return_value=Mock(returncode=1))) as command:
            with self.assertRaises(runtime.CaptionError):
                await self.real_prepare_excerpt(Path('/tmp/source.mp3'), Path(self.temp.name) / 'excerpt.wav', 12)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index('-t') + 1], '12')
        self.assertIn('file,pipe', argv)
        self.assertIn('mp3', argv)
