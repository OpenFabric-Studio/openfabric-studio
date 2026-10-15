"""The safe export boundary owns its copy through cancellation and shutdown."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import config, lora_datasets, lora_export, resource_admission
from app.api import routes_lora_export as routes
from app.lora_export_contracts import LoraExportRequest, LoraTrainedFilesRequest
from app.job_lifecycle import spawn_process
from starlette.requests import Request
from starlette.types import Message


class LoraExportRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine = self.root / 'engine'
        self.source = self.engine / 'lora_output' / 'run-one' / 'final'
        (self.source / 'adapter').mkdir(parents=True)
        (self.source / 'adapter' / 'adapter_config.json').write_text('{}')
        (self.source / 'adapter' / 'adapter_model.safetensors').write_bytes(b'original')
        self.destination = self.root / 'new-export'
        self.body = {'lora_output_dir': 'lora_output/run-one', 'export_path': str(self.destination)}
        self.enterContext(patch.object(config, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(lora_datasets, 'native_lock', asyncio.Lock()))
        self.idle = self.enterContext(patch.object(lora_datasets, 'require_native_idle', new=AsyncMock()))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.enterContext(patch('app.music_queue_ownership._owner', None))
        app = FastAPI()
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.addAsyncCleanup(self.client.aclose)

    async def test_preview_and_export_return_the_backend_selected_paths(self) -> None:
        preview = await self.client.post('/api/lora-export/preview', json=self.body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['source'], str(self.source))
        self.assertFalse(self.destination.exists())
        exported = await self.client.post('/api/lora-export', json=self.body)
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()['adapter_path'], str(self.destination / 'adapter'))
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'original')
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_trained_files_check_returns_existing_final_without_copying_or_gpu_admission(self) -> None:
        response = await self.client.post('/api/lora-export/check', json={'lora_output_dir': self.body['lora_output_dir']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'checkpoint': {
            'source': str(self.source), 'adapter_path': str(self.source / 'adapter'), 'kind': 'final',
        }})
        self.idle.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())
        self.assertFalse(self.destination.exists())

    async def test_existing_destination_created_after_preview_is_refused_without_losing_files(self) -> None:
        preview = await self.client.post('/api/lora-export/preview', json=self.body)
        self.assertEqual(preview.status_code, 200)
        self.destination.mkdir()
        sentinel = self.destination / 'important.wav'
        sentinel.write_bytes(b'keep')
        response = await self.client.post('/api/lora-export', json=self.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'lora_export_destination_exists')
        self.assertEqual(sentinel.read_bytes(), b'keep')

    async def test_concurrent_exports_have_one_winner_without_replacing_its_copy(self) -> None:
        responses = await asyncio.gather(
            self.client.post('/api/lora-export', json=self.body),
            self.client.post('/api/lora-export', json=self.body),
        )
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'original')
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_copy_failure_returns_only_a_stable_code_and_preserves_partial_copy(self) -> None:
        def fail(source: Path, destination: Path, **kwargs: object) -> str:
            destination.mkdir()
            (destination / 'partial').write_bytes(b'partial copy')
            raise OSError('private path and internal error')
        with patch.object(lora_export, '_copy_tree', side_effect=fail), self.assertLogs(lora_export.logger, level='ERROR'):
            response = await self.client.post('/api/lora-export', json=self.body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'lora_export_copy_failed'})
        self.assertEqual((self.destination / 'partial').read_bytes(), b'partial copy')
        self.assertEqual((self.source / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'original')

    async def test_untrusted_origin_is_rejected_before_picker_or_export_work(self) -> None:
        for endpoint in ('', '/preview', '/pick', '/check'):
            with self.subTest(endpoint=endpoint):
                response = await self.client.post('/api/lora-export' + endpoint, json=self.body, headers={'origin': 'https://attacker.invalid'})
                self.assertEqual(response.status_code, 403)
        self.idle.assert_not_awaited()
        self.assertFalse(self.destination.exists())

    async def test_trained_files_check_is_available_with_stopped_or_busy_engine(self) -> None:
        for code in ('native_unavailable', 'dataset_busy'):
            with self.subTest(code=code), patch.object(routes, 'reserve_native_control', new=AsyncMock(side_effect=AssertionError('read-only check requested GPU control'))) as reserve:
                self.idle.side_effect = lora_datasets.LoraDatasetError(code)
                response = await self.client.post('/api/lora-export/check', json={'lora_output_dir': 'lora_output/run-one'})
                self.assertEqual(response.status_code, 200, response.text)
                self.idle.assert_not_awaited()
                reserve.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_trained_files_check_missing_adapter_is_a_normal_result(self) -> None:
        for output in ('lora_output/missing', 'lora_output/incomplete'):
            if output.endswith('incomplete'):
                (self.engine / output / 'final' / 'adapter').mkdir(parents=True)
            response = await self.client.post('/api/lora-export/check', json={'lora_output_dir': output})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'checkpoint': None})
        self.assertFalse(self.destination.exists())

    async def test_trained_files_check_reports_complete_checkpoint_when_final_is_incomplete(self) -> None:
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        checkpoint = self.source.parent / 'checkpoints' / 'epoch_10_loss_0.2500'
        (checkpoint / 'adapter').mkdir(parents=True)
        (checkpoint / 'adapter' / 'adapter_config.json').write_text('{}')
        (checkpoint / 'adapter' / 'adapter_model.safetensors').write_bytes(b'complete')
        response = await self.client.post('/api/lora-export/check', json={'lora_output_dir': 'lora_output/run-one'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'checkpoint': {
            'source': str(checkpoint), 'adapter_path': str(checkpoint / 'adapter'), 'kind': 'checkpoint',
        }})
        self.assertFalse(self.destination.exists())

    async def test_trained_files_check_schema_body_size_and_unsafe_source_are_bounded(self) -> None:
        for body in ({}, {'lora_output_dir': ''}, {'lora_output_dir': 12}, {'lora_output_dir': 'x' * 4097},
                     {'lora_output_dir': 'lora_output/run-one', 'export_path': str(self.destination)}):
            with self.subTest(body=body):
                response = await self.client.post('/api/lora-export/check', json=body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {'detail': 'lora_export_request_invalid'})
        large = await self.client.post('/api/lora-export/check', content=b' ' * 65537, headers={'content-type': 'application/json'})
        self.assertEqual(large.status_code, 413)
        self.assertEqual(large.json(), {'detail': 'lora_export_request_too_large'})
        unsafe = await self.client.post('/api/lora-export/check', json={'lora_output_dir': '../private'})
        self.assertEqual(unsafe.status_code, 422)
        self.assertEqual(unsafe.json(), {'detail': 'lora_export_source_unsafe'})

    async def test_trained_files_check_storage_failure_never_returns_internal_error(self) -> None:
        with patch.object(lora_export, '_source', side_effect=PermissionError('private path and internal exception')), self.assertLogs(lora_export.logger, level='ERROR'):
            response = await self.client.post('/api/lora-export/check', json={'lora_output_dir': 'lora_output/run-one'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'lora_export_storage_unavailable'})

    async def test_cancelled_trained_files_check_keeps_lock_until_read_finishes_and_shutdown_drains(self) -> None:
        started, finish = threading.Event(), threading.Event()
        check = lora_export.check_trained_files
        def slow(body: LoraTrainedFilesRequest) -> lora_export.LoraTrainedFilesResponse:
            started.set()
            if not finish.wait(5):
                raise RuntimeError('test check was not released')
            return check(body)
        with patch.object(lora_export, 'check_trained_files', side_effect=slow):
            task = asyncio.create_task(routes.check(LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one')))
            shutdown: asyncio.Task[None] | None = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertTrue(lora_datasets.native_lock.locked())
                self.assertFalse(resource_admission.native_work_inflight())
                task.cancel()
                shutdown = asyncio.create_task(routes.shutdown())
                await asyncio.sleep(0)
                self.assertFalse(shutdown.done())
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                if shutdown is not None:
                    await shutdown
        self.assertFalse(lora_datasets.native_lock.locked())
        self.idle.assert_not_awaited()
        self.assertFalse(self.destination.exists())

    async def test_request_size_and_schema_are_bounded_and_have_stable_errors(self) -> None:
        large = await self.client.post('/api/lora-export', content=b' ' * 65537, headers={'content-type': 'application/json'})
        self.assertEqual(large.status_code, 413)
        self.assertEqual(large.json()['detail'], 'lora_export_request_too_large')
        for body in ({**self.body, 'overwrite': True}, {**self.body, 'export_path': 12}, {**self.body, 'export_path': 'x' * 4097}):
            response = await self.client.post('/api/lora-export', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail'], 'lora_export_request_invalid')
        self.idle.assert_not_awaited()

    async def test_busy_training_fails_closed_without_starting_a_copy(self) -> None:
        self.idle.side_effect = lora_datasets.LoraDatasetError('dataset_busy')
        response = await self.client.post('/api/lora-export', json=self.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'dataset_busy')
        self.assertFalse(self.destination.exists())
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_cancelled_request_keeps_lock_and_lease_until_copy_finishes(self) -> None:
        started, finish = threading.Event(), threading.Event()
        copy = lora_export.export_lora
        def slow(body: LoraExportRequest) -> lora_export.LoraExportResponse:
            started.set()
            if not finish.wait(5):
                raise RuntimeError('test copy was not released')
            return copy(body)
        with patch.object(lora_export, 'export_lora', side_effect=slow):
            task = asyncio.create_task(routes.export(LoraExportRequest(**self.body)))
            shutdown: asyncio.Task[None] | None = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertTrue(lora_datasets.native_lock.locked())
                self.assertTrue(resource_admission.native_work_inflight())
                task.cancel()
                shutdown = asyncio.create_task(routes.shutdown())
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(shutdown.done())
                shutdown.cancel()
                await asyncio.sleep(0)
                self.assertFalse(shutdown.done())
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                if shutdown is not None:
                    with self.assertRaises(asyncio.CancelledError):
                        await shutdown
        self.assertTrue(self.destination.is_dir())
        self.assertFalse(lora_datasets.native_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_picker_uses_a_fixed_prompt_returns_parent_and_handles_cancel(self) -> None:
        async def choose(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
            self.assertEqual(argv[:2], ('osascript', '-e'))
            self.assertIn('LoRA export', argv[2])
            return await spawn_process(sys.executable, '-c', 'import sys; print(sys.argv[1])', str(self.root) + '/', stdout=stdout, stderr=stderr)
        with patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=choose):
            options = await self.client.get('/api/lora-export/options')
            self.assertEqual(options.json(), {'can_pick': True})
            response = await self.client.post('/api/lora-export/pick')
            self.assertEqual(response.json(), {'path': str(self.root)})
        async def cancel(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
            return await spawn_process(sys.executable, '-c', 'import sys; print("User canceled. (-128)"); sys.exit(1)', stdout=stdout, stderr=stderr)
        with patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=cancel):
            cancelled = await self.client.post('/api/lora-export/pick')
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json(), {'path': None})

    async def test_picker_failures_have_stable_errors_and_other_platforms_report_unavailable(self) -> None:
        with patch.object(lora_export.sys, 'platform', 'linux'):
            options = await self.client.get('/api/lora-export/options')
            self.assertEqual(options.json(), {'can_pick': False})
            unavailable = await self.client.post('/api/lora-export/pick')
            self.assertEqual(unavailable.json()['detail'], 'lora_export_picker_unavailable')
        async def invalid_output(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
            return await spawn_process(sys.executable, '-c', 'print("relative/secret")', stdout=stdout, stderr=stderr)
        with patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=invalid_output):
            invalid = await self.client.post('/api/lora-export/pick')
            self.assertEqual(invalid.status_code, 503)
            self.assertEqual(invalid.json()['detail'], 'lora_export_picker_failed')

    async def slow_picker(self, processes: list[asyncio.subprocess.Process], started: asyncio.Event, *argv: str,
                          stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
        process = await spawn_process(sys.executable, '-c', 'import time; time.sleep(30)', stdout=stdout, stderr=stderr)
        processes.append(process)
        started.set()
        return process

    async def test_picker_request_cancellation_and_shutdown_terminate_owned_processes(self) -> None:
        for stop in ('request', 'shutdown'):
            processes: list[asyncio.subprocess.Process] = []
            started = asyncio.Event()
            async def spawn(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
                return await self.slow_picker(processes, started, *argv, stdout=stdout, stderr=stderr)
            with self.subTest(stop=stop), patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=spawn):
                request = asyncio.create_task(self.client.post('/api/lora-export/pick'))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    if stop == 'request':
                        request.cancel()
                    else:
                        await routes.shutdown()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(request, 2)
                    self.assertIsNotNone(processes[0].returncode)
                    self.assertEqual(routes._pickers, set())
                finally:
                    if not request.done():
                        request.cancel()
                    await asyncio.gather(request, return_exceptions=True)

    async def test_disconnected_picker_request_terminates_chooser_without_waiting_for_timeout(self) -> None:
        messages: asyncio.Queue[Message] = asyncio.Queue()
        async def receive() -> Message:
            return await messages.get()
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/lora-export/pick', 'headers': [], 'query_string': b''}, receive)
        processes: list[asyncio.subprocess.Process] = []
        started = asyncio.Event()
        async def spawn(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
            return await self.slow_picker(processes, started, *argv, stdout=stdout, stderr=stderr)
        with patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=spawn):
            picker = asyncio.create_task(routes.pick(request))
            try:
                await asyncio.wait_for(started.wait(), 2)
                await messages.put({'type': 'http.disconnect'})
                result = await asyncio.wait_for(picker, 2)
                self.assertIsNone(result.path)
                self.assertIsNotNone(processes[0].returncode)
                self.assertEqual(routes._pickers, set())
            finally:
                if not picker.done():
                    picker.cancel()
                await asyncio.gather(picker, return_exceptions=True)

    async def test_picker_timeout_reaps_process_and_returns_stable_error(self) -> None:
        processes: list[asyncio.subprocess.Process] = []
        started = asyncio.Event()
        async def spawn(*argv: str, stdout: int | None = None, stderr: int | None = None) -> asyncio.subprocess.Process:
            return await self.slow_picker(processes, started, *argv, stdout=stdout, stderr=stderr)
        with patch.object(lora_export.sys, 'platform', 'darwin'), patch.object(lora_export, 'spawn_process', side_effect=spawn), \
             patch.object(lora_export, 'read_owned_output', new=AsyncMock(side_effect=TimeoutError)) as read, self.assertLogs(lora_export.logger, level='ERROR'):
            response = await self.client.post('/api/lora-export/pick')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'lora_export_picker_failed'})
        self.assertEqual(read.call_args.kwargs, {'max_bytes': 16384, 'timeout': 600})
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(routes._pickers, set())
