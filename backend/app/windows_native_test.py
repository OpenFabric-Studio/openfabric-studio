"""Native launchers must retain ownership after their immediate child exits."""
from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import io
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.config import ProcessSpec
from app.orchestrator.process import ManagedProcess


class WindowsJobTests(unittest.TestCase):
    def test_parent_pipe_eof_terminates_the_owned_job(self) -> None:
        from app.orchestrator.windows_supervisor import _watch_parent
        job = Mock()
        _watch_parent(io.BytesIO(b'parent bytes'), job)
        job.terminate.assert_called_once_with()

    def test_native_handle_results_are_narrowed_and_invalid_values_rejected(self) -> None:
        from app.orchestrator.windows_job import checked_handle, checked_success
        self.assertEqual(checked_handle(42), 42)
        for value in (None, True, -1, 0, '42', 1 << 64):
            with self.subTest(value=value), self.assertRaises(OSError):
                checked_handle(value)
        for value in (None, True, 0, '1'):
            with self.subTest(value=value), self.assertRaises(OSError):
                checked_success(value)

    def test_job_creation_closes_handle_if_kill_on_close_cannot_be_enabled(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        api = Mock()
        api.create.return_value = 42
        api.set_kill_on_close.side_effect = OSError('limit failed')
        with self.assertRaises(OSError):
            WindowsJob.create(api)
        api.close.assert_called_once_with(42)

    def test_open_job_preserves_kernel_absence_and_access_error_codes(self) -> None:
        from app.orchestrator.windows_job import CtypesJobApi
        native = CtypesJobApi.__new__(CtypesJobApi)
        native._open = Mock(return_value=None)
        error = OSError('access denied')
        error.winerror = 5
        with patch('app.orchestrator.windows_job.sys.platform', 'win32'), \
             patch('app.orchestrator.windows_job.ctypes.get_last_error', return_value=5, create=True), \
             patch('app.orchestrator.windows_job.ctypes.WinError', return_value=error, create=True), \
             self.assertRaises(OSError) as failure:
            native.open('Local\\OpenFabricNative_' + 'a' * 32)
        self.assertIs(failure.exception, error)

    def test_open_job_prototype_enables_private_last_error_tracking(self) -> None:
        from app.orchestrator.windows_job import CtypesJobApi
        flags: dict[str, dict[str, object]] = {}
        def prototype(*args: object, **kwargs: object) -> Mock:
            def resolve(symbol: tuple[str, object]) -> Mock:
                flags[symbol[0]] = kwargs
                return Mock()
            return Mock(side_effect=resolve)
        with patch('app.orchestrator.windows_job.sys.platform', 'win32'), \
             patch('app.orchestrator.windows_job.ctypes.WinDLL', create=True), \
             patch('app.orchestrator.windows_job.ctypes.WINFUNCTYPE', side_effect=prototype, create=True):
            CtypesJobApi()
        self.assertIs(flags['OpenJobObjectW'].get('use_last_error'), True)

    def test_supervisor_assigns_itself_before_creating_the_native_child(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('app.orchestrator.windows_supervisor'))
        from app.orchestrator.windows_supervisor import supervise
        events: list[str] = []
        job = Mock()
        job.assign_current.side_effect = lambda: events.append('assigned')
        job.terminate.side_effect = lambda code=1: events.append('terminated')
        child = Mock()
        child.wait.side_effect = lambda: events.append('waited') or 0
        def launch(command: list[str]) -> subprocess.Popen[bytes]:
            events.append('spawned')
            return child
        supervise(job, ['fixture'], io.BytesIO(), launch)
        self.assertEqual(events[:2], ['assigned', 'spawned'])
        self.assertIn('terminated', events)

    def test_failed_job_assignment_never_spawns_unowned_child(self) -> None:
        from app.orchestrator.windows_supervisor import supervise
        job = Mock()
        job.assign_current.side_effect = OSError('assignment rejected')
        launcher = Mock()
        with self.assertRaises(OSError):
            supervise(job, ['fixture'], io.BytesIO(), launcher)
        launcher.assert_not_called()


class WindowsProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_command_is_wrapped_and_parent_retains_job_query_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = ManagedProcess(ProcessSpec(name='fixture', cwd=Path(directory), cmd=['engine', '--flag']))
            job = Mock()
            job.name = 'Local\\OpenFabricNative_' + 'a' * 32
            with patch('app.orchestrator.process.IS_WINDOWS', True), \
                 patch('app.orchestrator.process.LOG_DIR', Path(directory)), \
                 patch('app.orchestrator.process.WindowsJob.create', return_value=job), \
                 patch('app.orchestrator.process.subprocess.Popen') as launch:
                process.start()
            command = launch.call_args.args[0]
            self.assertIn('windows_supervisor.py', command[2])
            self.assertEqual(command[-2:], ['engine', '--flag'])
            self.assertEqual(launch.call_args.kwargs['stdin'], subprocess.PIPE)
            self.assertIs(process._windows_job, job)
            process._close_log()

    async def test_exited_supervisor_does_not_mean_its_job_children_are_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = ManagedProcess(ProcessSpec(name='fixture', cwd=Path(directory), cmd=['fixture']))
            process._proc = Mock()
            process._proc.poll.return_value = 0
            process._proc.pid = 42
            job = Mock()
            job.active_processes.return_value = 2
            process._windows_job = job
            with patch('app.orchestrator.process.IS_WINDOWS', True), \
                 patch.object(process, 'wait_stopped', new=AsyncMock(return_value=False)):
                with self.assertRaises(RuntimeError):
                    await process.stop()
            job.terminate.assert_called()
            job.close.assert_not_called()


class WindowsToolOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_job_exists_before_wrapper_creation_and_is_registered(self) -> None:
        from app import job_lifecycle, video_process
        job = Mock()
        job.name = 'Local\\OpenFabricNative_' + 'b' * 32
        proc = Mock()
        proc.pid = 42
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'', b''))
        job.active_processes.return_value = 0
        events: list[str] = []
        def create_job() -> Mock:
            events.append('job')
            return job
        async def create_wrapper(*args: str, **kwargs: object) -> Mock:
            events.append('wrapper')
            self.assertIn('--job', args)
            self.assertIn(job.name, args)
            self.assertEqual(kwargs['stdin'], asyncio.subprocess.PIPE)
            return proc
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.video_process.sys.platform', 'win32'), \
             patch('app.orchestrator.windows_job.WindowsJob.create', side_effect=create_job), \
             patch('asyncio.create_subprocess_exec', side_effect=create_wrapper):
            result = await video_process.spawn_owned(['fixture'], receipt_path=Path(directory) / 'receipt.json')
            self.assertEqual(events, ['job', 'wrapper'])
            self.assertIs(result, proc)
            await job_lifecycle.kill_process_tree(proc)
        job.close.assert_called_once_with()

    async def test_exited_tool_wrapper_still_terminates_and_drains_job_descendants(self) -> None:
        from app import job_lifecycle
        proc = Mock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'', b''))
        job = Mock()
        job.active_processes.side_effect = [2, 0]
        registry = {proc: job}
        with patch.object(job_lifecycle, '_windows_jobs', registry, create=True), \
             patch('app.job_lifecycle.sys.platform', 'win32'):
            await job_lifecycle.kill_process_tree(proc)
        job.terminate.assert_called_once_with()
        job.close.assert_called_once_with()
        self.assertFalse(registry)

    async def test_failed_tool_job_accounting_retains_ownership_after_wrapper_exit(self) -> None:
        from app import job_lifecycle
        proc = Mock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'', b''))
        job = Mock()
        job.active_processes.side_effect = OSError('query failed')
        registry = {proc: job}
        with patch.object(job_lifecycle, '_windows_jobs', registry, create=True), \
             patch('app.job_lifecycle.sys.platform', 'win32'), self.assertRaises(OSError):
            await job_lifecycle.kill_process_tree(proc)
        self.assertIs(registry[proc], job)
        job.close.assert_not_called()

    async def test_cancelled_tool_creation_is_drained_before_ownership_is_released(self) -> None:
        from app import job_lifecycle, video_process
        job = Mock()
        job.name = 'Local\\OpenFabricNative_' + 'c' * 32
        job.active_processes.return_value = 0
        proc = Mock()
        proc.pid = 42
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'', b''))
        started = asyncio.Event()
        finish = asyncio.Event()
        async def create_wrapper(*args: str, **kwargs: object) -> Mock:
            started.set()
            await finish.wait()
            return proc
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.video_process.sys.platform', 'win32'), \
             patch('app.orchestrator.windows_job.WindowsJob.create', return_value=job), \
             patch('asyncio.create_subprocess_exec', side_effect=create_wrapper):
            task = asyncio.create_task(video_process.spawn_owned(['fixture'], receipt_path=Path(directory) / 'receipt.json'))
            await started.wait()
            task.cancel()
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        job.terminate.assert_called_once_with()
        job.close.assert_called_once_with()
        self.assertNotIn(proc, job_lifecycle._windows_jobs)

    async def test_repeated_cancellation_while_creating_tool_still_drains_created_job(self) -> None:
        from app import job_lifecycle, video_process
        job = Mock()
        job.name = 'Local\\OpenFabricNative_' + 'e' * 32
        job.active_processes.return_value = 0
        proc = Mock()
        proc.pid = 43
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b'', b''))
        started = asyncio.Event()
        finish = asyncio.Event()
        async def create_wrapper(*args: str, **kwargs: object) -> Mock:
            started.set()
            await finish.wait()
            return proc
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.video_process.sys.platform', 'win32'), \
             patch('app.orchestrator.windows_job.WindowsJob.create', return_value=job), \
             patch('asyncio.create_subprocess_exec', side_effect=create_wrapper):
            task = asyncio.create_task(video_process.spawn_owned(['fixture'], receipt_path=Path(directory) / 'receipt.json'))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        job.terminate.assert_called_once_with()
        proc.communicate.assert_awaited_once_with()
        job.close.assert_called_once_with()
        self.assertNotIn(proc, job_lifecycle._windows_jobs)

    async def test_empty_job_before_wrapper_assignment_cannot_release_running_wrapper(self) -> None:
        from app import job_lifecycle
        proc = Mock()
        proc.returncode = None
        entered = asyncio.Event()
        exited = asyncio.Event()
        async def communicate() -> tuple[bytes, bytes]:
            entered.set()
            await exited.wait()
            return b'', b''
        proc.communicate = AsyncMock(side_effect=communicate)
        job = Mock()
        job.active_processes.return_value = 0
        registry = {proc: job}
        with patch.object(job_lifecycle, '_windows_jobs', registry, create=True):
            task = asyncio.create_task(job_lifecycle.kill_process_tree(proc))
            await entered.wait()
            proc.stdin.close.assert_called_once_with()
            self.assertFalse(task.done())
            self.assertIn(proc, registry)
            job.close.assert_not_called()
            exited.set()
            await task
        job.close.assert_called_once_with()

    async def test_concurrent_tool_cleanup_callers_share_one_verified_drain(self) -> None:
        from app import job_lifecycle
        proc = Mock()
        proc.returncode = 0
        entered = asyncio.Event()
        exited = asyncio.Event()
        async def communicate() -> tuple[bytes, bytes]:
            entered.set()
            await exited.wait()
            return b'', b''
        proc.communicate = AsyncMock(side_effect=communicate)
        job = Mock()
        job.active_processes.return_value = 0
        registry = {proc: job}
        with patch.object(job_lifecycle, '_windows_jobs', registry, create=True):
            first = asyncio.create_task(job_lifecycle.kill_process_tree(proc))
            await entered.wait()
            second = asyncio.create_task(job_lifecycle.kill_process_tree(proc))
            await asyncio.sleep(0)
            exited.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(results, [None, None])
        proc.communicate.assert_awaited_once_with()
        job.close.assert_called_once_with()
        self.assertFalse(registry)


class WindowsToolSupervisorTests(unittest.TestCase):
    def test_tool_assigns_job_before_child_spawn_and_kills_orphans_after_exit(self) -> None:
        from app import video_process
        job = Mock()
        job.name = 'Local\\OpenFabricNative_' + 'd' * 32
        events: list[str] = []
        job.assign_current.side_effect = lambda: events.append('assigned')
        job.terminate.side_effect = lambda code=1: events.append('terminated')
        child = Mock()
        child.wait.return_value = 0
        def launch(*args: object, **kwargs: object) -> Mock:
            events.append('spawned')
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            return child
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.video_process.subprocess.Popen', side_effect=launch), \
             patch('app.video_process.threading.Thread'):
            value = video_process.WorkerReceipt(pid=42, token='a' * 32)
            code = video_process._supervise_worker(['fixture'], Path(directory) / 'receipt.json', value, io.BytesIO(), None, job)
            self.assertEqual(code, 0)
            self.assertEqual(events, ['assigned', 'spawned', 'terminated'])
            self.assertEqual(video_process.WorkerReceipt.model_validate_json((Path(directory) / 'receipt.json').read_bytes()).job_name, job.name)

    def test_failed_tool_assignment_does_not_launch_child(self) -> None:
        from app import video_process
        job = Mock()
        job.assign_current.side_effect = OSError('assignment failed')
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.video_process.subprocess.Popen') as launch, \
             self.assertRaises(OSError):
            video_process._supervise_worker(['fixture'], Path(directory) / 'receipt.json', video_process.WorkerReceipt(pid=42, token='a' * 32), io.BytesIO(), None, job)
        launch.assert_not_called()

    def test_tool_parent_eof_terminates_entire_owned_job(self) -> None:
        from app import video_process
        job = Mock()
        video_process._watch_parent(io.BytesIO(b'bytes then EOF'), job)
        job.terminate.assert_called_once_with()


class WindowsToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_dead_wrapper_recovery_drains_named_job_before_reporting_success(self) -> None:
        from app import video_process
        job = Mock()
        job.active_processes.side_effect = [2, 0]
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'receipt.json'
            receipt.write_text('{"pid":42,"token":"' + 'a' * 32 + '","job_name":"Local\\\\OpenFabricNative_' + 'b' * 32 + '"}')
            identity = video_process.WorkerIdentity(pid=42, token='a' * 32, receipt=str(receipt))
            with patch('app.video_process.sys.platform', 'win32'), \
                 patch('app.job_lifecycle.spawn_process', new=AsyncMock()), \
                 patch('app.job_lifecycle.communicate_process', new=AsyncMock(return_value=(b'', b''))), \
                 patch('app.orchestrator.windows_job.WindowsJob.open', return_value=job):
                self.assertTrue(await video_process.terminate_verified(identity))
        job.terminate.assert_called_once_with()
        job.close.assert_called_once_with()

    async def test_only_verified_missing_windows_job_is_accepted_as_drained(self) -> None:
        from app import video_process
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'receipt.json'
            receipt.write_text('{"pid":42,"token":"' + 'a' * 32 + '","job_name":"Local\\\\OpenFabricNative_' + 'b' * 32 + '"}')
            identity = video_process.WorkerIdentity(pid=42, token='a' * 32, receipt=str(receipt))
            for error_code, expected in ((2, True), (5, False), (87, False)):
                error = OSError('native open failure')
                error.winerror = error_code
                with self.subTest(error_code=error_code), \
                     patch('app.video_process.sys.platform', 'win32'), \
                     patch('app.job_lifecycle.spawn_process', new=AsyncMock()), \
                     patch('app.job_lifecycle.communicate_process', new=AsyncMock(return_value=(b'', b''))), \
                     patch('app.orchestrator.windows_job.WindowsJob.open', side_effect=error) as opener:
                    self.assertEqual(await video_process.terminate_verified(identity), expected)
                    opener.assert_called_once()

    async def test_recovery_accounting_failure_retains_handle_for_a_verified_retry(self) -> None:
        from app import video_process
        job = Mock()
        job.active_processes.side_effect = [OSError('accounting failed'), 0]
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'receipt.json'
            receipt.write_text('{"pid":42,"token":"' + 'a' * 32 + '","job_name":"Local\\\\OpenFabricNative_' + 'c' * 32 + '"}')
            identity = video_process.WorkerIdentity(pid=42, token='a' * 32, receipt=str(receipt))
            with patch('app.video_process.sys.platform', 'win32'), \
                 patch('app.job_lifecycle.spawn_process', new=AsyncMock()), \
                 patch('app.job_lifecycle.communicate_process', new=AsyncMock(return_value=(b'', b''))), \
                 patch('app.orchestrator.windows_job.WindowsJob.open', return_value=job) as opener:
                self.assertFalse(await video_process.terminate_verified(identity))
                job.close.assert_not_called()
                self.assertTrue(await video_process.terminate_verified(identity))
                opener.assert_called_once()
        job.close.assert_called_once_with()


@unittest.skipUnless(sys.platform == 'win32', 'Real Windows Job Objects require Windows')
class WindowsKernelTests(unittest.TestCase):
    def test_missing_job_reports_fresh_kernel_error_instead_of_stale_access_error(self) -> None:
        from app.orchestrator.windows_job import CtypesJobApi
        native = CtypesJobApi()
        self.assertTrue(getattr(native._open, '_flags_') & getattr(ctypes, '_FUNCFLAG_USE_LASTERROR'))
        ctypes.set_last_error(5)
        with self.assertRaises(OSError) as failure:
            native.open('Local\\OpenFabricNative_' + uuid.uuid4().hex)
        self.assertEqual(failure.exception.winerror, 2)

    def test_native_launcher_exit_terminates_orphaned_grandchild(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        from app.orchestrator.windows_supervisor import supervisor_command
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ready'
            child = Path(directory) / 'child.py'
            child.write_text("import subprocess,sys\nfrom pathlib import Path\n"
                             "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                             "Path(sys.argv[1]).write_text('ready')\n")
            job = WindowsJob.create()
            process = subprocess.Popen(supervisor_command(job.name, [sys.executable, str(child), str(marker)]), stdin=subprocess.PIPE)
            try:
                process.wait(timeout=10)
                self.assertTrue(marker.is_file())
                deadline = time.monotonic() + 10
                while job.active_processes() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(job.active_processes(), 0)
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                job.terminate()
                process.wait(timeout=10)
                job.close()

    def test_parent_eof_terminates_native_descendants_and_job_becomes_empty(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        from app.orchestrator.windows_supervisor import supervisor_command
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ready'
            child = Path(directory) / 'child.py'
            child.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                             "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                             "Path(sys.argv[1]).write_text('ready')\ntime.sleep(120)\n")
            job = WindowsJob.create()
            process = subprocess.Popen(supervisor_command(job.name, [sys.executable, str(child), str(marker)]), stdin=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail('Supervisor exited before spawning the owned child')
                    time.sleep(.05)
                self.assertTrue(marker.is_file())
                self.assertGreaterEqual(job.active_processes(), 3)
                self.assertIsNotNone(process.stdin)
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=10)
                while job.active_processes() and time.monotonic() < deadline + 10:
                    time.sleep(.05)
                self.assertEqual(job.active_processes(), 0)
            finally:
                job.terminate()
                process.wait(timeout=10)
                job.close()

    def test_backend_crash_kills_shared_tool_job_with_descendants(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        from app.video_process import WorkerReceipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'ready'
            receipt = root / 'receipt.json'
            child = root / 'tool.py'
            child.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                             "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                             "Path(sys.argv[1]).write_text('ready')\ntime.sleep(120)\n")
            code = ("import asyncio,sys\nfrom pathlib import Path\nfrom app.video_process import spawn_owned\n"
                    "async def main():\n"
                    " proc=await spawn_owned([sys.executable,sys.argv[1],sys.argv[2]],receipt_path=Path(sys.argv[3]))\n"
                    " await proc.wait()\nasyncio.run(main())\n")
            backend = subprocess.Popen([sys.executable, '-c', code, str(child), str(marker), str(receipt)])
            job = None
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    if backend.poll() is not None:
                        self.fail('Backend exited before owned tool became ready')
                    time.sleep(.05)
                self.assertTrue(marker.is_file())
                value = WorkerReceipt.model_validate_json(receipt.read_bytes())
                self.assertIsNotNone(value.job_name)
                if value.job_name is None:
                    self.fail('Tool did not persist its named job')
                job = WindowsJob.open(value.job_name)
                self.assertGreaterEqual(job.active_processes(), 3)
                backend.kill()
                backend.wait(timeout=10)
                deadline = time.monotonic() + 10
                while job.active_processes() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(job.active_processes(), 0)
            finally:
                if backend.poll() is None:
                    backend.kill()
                backend.wait(timeout=10)
                if job is not None:
                    job.terminate()
                    job.close()


@unittest.skipUnless(sys.platform == 'win32', 'Real Windows tool jobs require Windows')
class WindowsToolKernelTests(unittest.IsolatedAsyncioTestCase):
    async def _fixture(self, *, launcher_exits: bool) -> None:
        from app import job_lifecycle, video_process
        from app.orchestrator.windows_job import WindowsJob
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'ready'
            child = root / 'tool.py'
            child.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                             "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                             "Path(sys.argv[1]).write_text('ready')\n" + ('' if launcher_exits else 'time.sleep(120)\n'))
            proc = await video_process.spawn_owned([sys.executable, str(child), str(marker)],
                                                   receipt_path=root / 'receipt.json', stdout=asyncio.subprocess.DEVNULL)
            job = job_lifecycle._windows_jobs[proc]
            observer = WindowsJob.open(job.name)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    if proc.returncode is not None:
                        self.fail('Tool wrapper exited before child became ready')
                    await asyncio.sleep(.02)
                self.assertTrue(marker.is_file())
                if not launcher_exits:
                    self.assertGreaterEqual(job.active_processes(), 3)
                    self.assertIsNotNone(proc.stdin)
                    if proc.stdin is not None:
                        proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 10)
                await job_lifecycle.kill_process_tree(proc)
                self.assertNotIn(proc, job_lifecycle._windows_jobs)
                # An independent still-open kernel handle cannot report a
                # synthetic zero merely because the owner's handle closed.
                self.assertEqual(observer.active_processes(), 0)
                if launcher_exits:
                    self.assertEqual(proc.returncode, 0)
                    value = video_process.WorkerReceipt.model_validate_json((root / 'receipt.json').read_bytes())
                    self.assertEqual(value.returncode, 0)
            finally:
                try:
                    await job_lifecycle.kill_process_tree(proc)
                finally:
                    observer.terminate()
                    observer.close()

    async def test_tool_launcher_exit_terminates_orphaned_grandchild(self) -> None:
        await self._fixture(launcher_exits=True)

    async def test_tool_parent_eof_terminates_descendants_before_handle_release(self) -> None:
        await self._fixture(launcher_exits=False)
