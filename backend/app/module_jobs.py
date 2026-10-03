"""Persist and own reviewed setup jobs independently of browser lifetime."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import IO, Literal
import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .module_catalog import approval_scope, ModuleEnvironment, configured_environment, DEFINITIONS, now, plan
from .module_contracts import (
    ModuleId, ModuleInstallJob, ModuleInstallRequest, ModuleJobsResponse, ModuleJobStep, ModulePlan,
    ModulePlanRequest, ModuleStepState,
)
from .video_process import spawn_owned, terminate_verified, WorkerIdentity

logger = logging.getLogger(__name__)
ModuleErrorCode = Literal['plan_changed', 'setup_busy', 'insufficient_disk', 'job_missing', 'invalid_job', 'job_corrupt', 'installation_failed', 'installer_failed', 'installer_output_limit', 'installer_timeout', 'worker_unverified', 'unsafe_install_path', 'not_managed', 'unsupported_platform', 'artifact_integrity', 'archive_unsafe', 'installation_conflict', 'environment_unverified', 'setup_not_resumable']


class ModuleSetupError(Exception):
    def __init__(self, code: ModuleErrorCode) -> None:
        self.code = code
        super().__init__(code)


class StoredModuleJob(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job: ModuleInstallJob
    plan: ModulePlan
    worker: WorkerIdentity | None = None
    review_scope: str = ''


class ModuleJobStore:
    """SQLite transactions arbitrate concurrent local application instances."""
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / '.setup' / 'modules.sqlite3'

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root):
            raise ModuleSetupError('unsafe_install_path')
        if read_only:
            return sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute('PRAGMA busy_timeout=5000')
        version: object = connection.execute('PRAGMA user_version').fetchone()
        if not isinstance(version, tuple) or not version or not isinstance(version[0], int) or version[0] not in (0, 1):
            connection.close()
            raise ModuleSetupError('job_corrupt')
        if version[0] == 0:
            connection.execute('CREATE TABLE IF NOT EXISTS setup_jobs (id TEXT PRIMARY KEY CHECK(length(id)=32), state TEXT NOT NULL, worker INTEGER NOT NULL DEFAULT 0, document TEXT NOT NULL)')
            connection.execute('PRAGMA user_version=1')
        return connection

    def reserve(self, stored: StoredModuleJob) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            active: object = connection.execute("SELECT id FROM setup_jobs WHERE state IN ('queued','running') OR worker=1 LIMIT 1").fetchone()
            if active is not None:
                raise ModuleSetupError('setup_busy')
            connection.execute('INSERT INTO setup_jobs(id,state,worker,document) VALUES(?,?,?,?)',
                (stored.job.id, stored.job.state, int(stored.worker is not None), stored.model_dump_json()))

    def write(self, stored: StoredModuleJob) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('UPDATE setup_jobs SET state=?,worker=?,document=? WHERE id=?',
                (stored.job.state, int(stored.worker is not None), stored.model_dump_json(), stored.job.id))

    def requeue(self, stored: StoredModuleJob) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            active: object = connection.execute("SELECT id FROM setup_jobs WHERE (state IN ('queued','running') OR worker=1) LIMIT 1").fetchone()
            if active is not None:
                raise ModuleSetupError('setup_busy')
            row: object = connection.execute('SELECT state FROM setup_jobs WHERE id=?', (stored.job.id,)).fetchone()
            if not isinstance(row, tuple) or not row or row[0] not in ('cancelled', 'interrupted', 'failed', 'awaiting_manual'):
                raise ModuleSetupError('setup_not_resumable')
            connection.execute('UPDATE setup_jobs SET state=?,worker=0,document=? WHERE id=?',
                ('queued', stored.model_dump_json(), stored.job.id))

    def read(self, identifier: str) -> StoredModuleJob:
        if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
            raise ModuleSetupError('invalid_job')
        if not self.path.exists():
            raise ModuleSetupError('job_missing')
        with closing(self._connect(read_only=True)) as connection:
            row: object = connection.execute('SELECT document FROM setup_jobs WHERE id=?', (identifier,)).fetchone()
        if not isinstance(row, tuple) or not row or not isinstance(row[0], str):
            raise ModuleSetupError('job_missing')
        try:
            if len(row[0]) > 1_000_000:
                raise ValueError('oversized_job')
            return StoredModuleJob.model_validate_json(row[0])
        except (ValidationError, ValueError) as exc:
            raise ModuleSetupError('job_corrupt') from exc

    def records(self, *, recovery: bool = False) -> list[StoredModuleJob]:
        if not self.path.exists():
            return []
        with closing(self._connect(read_only=True)) as connection:
            query = "SELECT id FROM setup_jobs WHERE state IN ('queued','running') OR worker=1 ORDER BY rowid" if recovery else 'SELECT id FROM setup_jobs ORDER BY rowid DESC LIMIT 50'
            rows = connection.execute(query).fetchall()
        identifiers: list[str] = []
        for row in rows:
            if isinstance(row, tuple) and row and isinstance(row[0], str):
                identifiers.append(row[0])
        return [self.read(identifier) for identifier in identifiers]


@dataclass(frozen=True)
class InstallOutcome:
    state: Literal['verified', 'manual']
    detail: str
    restart_required: bool = False


class InstallContext:
    def __init__(self, service: ModuleJobService, identifier: str) -> None:
        self.service = service
        self.environment = service.environment
        self.identifier = identifier
        self.workspace = self.environment.root / '.setup' / 'staging' / identifier

    def confined(self, path: Path) -> Path:
        if path.is_symlink() or not path.resolve().is_relative_to(self.environment.root.resolve()):
            raise ModuleSetupError('unsafe_install_path')
        return path

    async def run(self, argv: list[str], *, cwd: Path | None = None,
                  env: Mapping[str, str] | None = None, timeout: float = 3600) -> None:
        """Only catalog adapters construct argv; caller input never reaches it."""
        receipt = self.confined(self.environment.root / '.setup' / 'workers' / f'{self.identifier}.json')
        logs = self.confined(self.environment.root / '.setup' / 'logs')
        logs.mkdir(parents=True, exist_ok=True)
        proc: asyncio.subprocess.Process | None = None
        def identity(value: WorkerIdentity) -> None:
            stored = self.service.store.read(self.identifier)
            stored.worker = value
            self.service.store.write(stored)
        try:
            proc = await spawn_owned(argv, receipt_path=receipt, cwd=cwd, env=env,
                                     stdout=asyncio.subprocess.PIPE, on_identity=identity)
            self.service.processes[self.identifier] = proc
            if proc.stdout is None:
                raise ModuleSetupError('installer_failed')
            log_path = logs / f'{self.identifier}.log'
            if log_path.is_symlink():
                raise ModuleSetupError('unsafe_install_path')
            with log_path.open('ab') as log:
                written = log_path.stat().st_size
                async with asyncio.timeout(timeout):
                    while chunk := await proc.stdout.read(16384):
                        written += len(chunk)
                        if written > 20 * 1024**2:
                            raise ModuleSetupError('installer_output_limit')
                        log.write(chunk)
                    code = await proc.wait()
            if code != 0:
                raise ModuleSetupError('installer_failed')
        except TimeoutError as exc:
            raise ModuleSetupError('installer_timeout') from exc
        finally:
            if proc is not None:
                # Never release the receipt/ownership after a failed drain.
                await await_cleanup(kill_process_tree(proc))
                stored = self.service.store.read(self.identifier)
                stored.worker = None
                self.service.store.write(stored)
                receipt.unlink(missing_ok=True)
                self.service.processes.pop(self.identifier, None)


Installer = Callable[[InstallContext, ModuleId, bool], Awaitable[InstallOutcome]]

_speech_gate = threading.RLock()
_speech_inflight = 0
_setup_reservations = 0


def work_busy() -> bool:
    with _speech_gate:
        if _setup_reservations > 0:
            return True
    # Other backend instances share the managed job registry.
    if _service is not None:
        return bool(_service.store.records(recovery=True))
    return False


@contextmanager
def speech_admission() -> Iterator[None]:
    """Thread-safe exclusion between actual speech synthesis and setup."""
    global _speech_inflight
    with _speech_gate:
        if work_busy():
            raise ModuleSetupError('setup_busy')
        _speech_inflight += 1
    try:
        yield
    finally:
        with _speech_gate:
            _speech_inflight -= 1


def _application_busy() -> bool:
    from .orchestrator.manager import manager
    from .orchestrator.state import ModelStatus
    from .resource_admission import native_work_inflight
    from .stems import gpu_lock
    from .work_busy import local_work_busy
    from .video_jobs import work_busy as video_work_busy
    from .audiobook_narration import work_busy as narration_work_busy
    return native_work_inflight() or gpu_lock.locked() or local_work_busy() or video_work_busy() or narration_work_busy() or any(
        state.status in (ModelStatus.STARTING, ModelStatus.RUNNING, ModelStatus.STOPPING)
        for state in manager.state.models.values())


def _reserve_setup() -> None:
    global _setup_reservations
    with _speech_gate:
        if _speech_inflight or _setup_reservations or _application_busy():
            raise ModuleSetupError('setup_busy')
        _setup_reservations += 1


def _release_setup() -> None:
    global _setup_reservations
    with _speech_gate:
        _setup_reservations -= 1


class SetupLease:
    """OS releases the advisory lease after a backend crash, before recovery."""
    def __init__(self, root: Path) -> None:
        path = root / '.setup/installation.lock'
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ModuleSetupError('unsafe_install_path')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream: IO[bytes] = path.open('a+b')
        try:
            if sys.platform == 'win32':
                import msvcrt
                if path.stat().st_size == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise ModuleSetupError('setup_busy') from exc

    def close(self) -> None:
        if not self.stream.closed:
            self.stream.close()


class ModuleJobService:
    def __init__(self, environment: ModuleEnvironment, *, installer: Installer | None = None) -> None:
        self.environment = environment
        self.store = ModuleJobStore(environment.root)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.lock = asyncio.Lock()
        self.stopping = False
        self.reservations: set[str] = set()
        self.lease: SetupLease | None = None
        self.installer: Installer = installer or _install

    def get(self, identifier: str) -> ModuleInstallJob:
        return self.store.read(identifier).job

    def list(self) -> ModuleJobsResponse:
        return ModuleJobsResponse(jobs=[record.job for record in self.store.records()])

    async def _review(self, request: ModulePlanRequest) -> ModulePlan:
        current = await plan(request, self.environment)
        if not current.can_install:
            raise ModuleSetupError('insufficient_disk' if current.free_bytes is None or current.free_bytes < current.required_free_bytes else 'unsupported_platform')
        return current

    async def create(self, request: ModuleInstallRequest) -> ModuleInstallJob:
        async with self.lock:
            if self.stopping:
                raise ModuleSetupError('setup_busy')
            current = await self._review(request)
            if request.plan_token != current.plan_token:
                raise ModuleSetupError('plan_changed')
            stamp = now()
            job = ModuleInstallJob(id=uuid.uuid4().hex, state='queued', created_at=stamp, updated_at=stamp,
                features=request.features, download_models=request.download_models,
                steps=[ModuleJobStep(module_id=step.module_id, name=step.name, state='queued', detail=step.detail) for step in current.steps])
            stored = StoredModuleJob(job=job, plan=current, review_scope=approval_scope(request, self.environment))
            from .resource_admission import admission_lock
            async with admission_lock:
                lease_created = self._claim_lease()
                try:
                    _reserve_setup()
                    try:
                        self.store.reserve(stored)
                    except BaseException:
                        _release_setup()
                        raise
                except BaseException:
                    if lease_created:
                        self._close_lease()
                    raise
                self.reservations.add(job.id)
            self.tasks[job.id] = asyncio.create_task(self._execute(job.id))
            return job.model_copy(deep=True)

    async def _execute(self, identifier: str) -> None:
        context = InstallContext(self, identifier)
        try:
            stored = self.store.read(identifier)
            stored.job.state = 'running'
            stored.job.updated_at = now()
            self.store.write(stored)
            for index, planned in enumerate(stored.plan.steps):
                stored = self.store.read(identifier)
                step = stored.job.steps[index]
                stored.job.current_step = index
                step.state = 'running'
                stored.job.updated_at = now()
                self.store.write(stored)
                dependency_manual = any(other.module_id in DEFINITIONS[step.module_id].dependencies and other.state in ('manual', 'failed', 'skipped') for other in stored.job.steps[:index])
                if planned.operation == 'unsupported':
                    result = InstallOutcome('manual', 'Unsupported on this platform. No installation was attempted.')
                elif planned.operation == 'manual' or dependency_manual:
                    result = InstallOutcome('manual', planned.detail if not dependency_manual else 'Complete the preceding manual dependency setup, then review this feature again.')
                elif planned.operation == 'verify':
                    from .module_install import verify
                    result = await verify(context, step.module_id)
                else:
                    result = await self.installer(context, step.module_id, stored.job.download_models)
                stored = self.store.read(identifier)
                stored.job.steps[index].state = result.state
                stored.job.steps[index].detail = result.detail[:1000]
                stored.job.restart_required |= result.restart_required
                self.store.write(stored)
            stored = self.store.read(identifier)
            stored.job.state = 'awaiting_manual' if any(step.state in ('manual', 'skipped') for step in stored.job.steps) else 'completed'
            stored.job.current_step = None
            stored.job.updated_at = now()
            self.store.write(stored)
        except asyncio.CancelledError:
            stored = self.store.read(identifier)
            stored.job.state = 'interrupted' if self.stopping else 'cancelled'
            stored.job.updated_at = now()
            self.store.write(stored)
            raise
        except Exception as exc:
            logger.exception('Module installation failed for job %s', identifier)
            stored = self.store.read(identifier)
            code: ModuleErrorCode = exc.code if isinstance(exc, ModuleSetupError) else 'installation_failed'
            stored.job.state = 'failed'
            stored.job.error_code = code
            stored.job.updated_at = now()
            if stored.job.current_step is not None:
                step = stored.job.steps[stored.job.current_step]
                step.state = 'failed'
                step.detail = 'Installation failed. Review the stable error code and local setup log before retrying.'
                step.error_code = code
            self.store.write(stored)
        finally:
            self.tasks.pop(identifier, None)
            self._release_if_clean(identifier)

    def _release_if_clean(self, identifier: str) -> None:
        if identifier in self.reservations and self.store.read(identifier).worker is None:
            self.reservations.remove(identifier)
            _release_setup()
        if not self.reservations:
            self._close_lease()

    def _claim_lease(self) -> bool:
        if self.lease is not None:
            return False
        self.lease = SetupLease(self.environment.root)
        return True

    def _close_lease(self) -> None:
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def _quarantine(self, identifier: str) -> None:
        global _setup_reservations
        if identifier not in self.reservations:
            with _speech_gate:
                _setup_reservations += 1
            self.reservations.add(identifier)

    async def cancel(self, identifier: str) -> ModuleInstallJob:
        async with self.lock:
            initial = self.store.read(identifier)
            if identifier not in self.tasks and (initial.worker is not None or initial.job.state in ('queued', 'running')):
                self._claim_lease()
                self._quarantine(identifier)
            await cancel_and_wait(self.tasks.get(identifier))
            stored = self.store.read(identifier)
            if stored.worker is not None:
                await self._recover_worker(stored)
            if stored.job.state in ('queued', 'running', 'interrupted') and stored.worker is None:
                stored.job.state = 'cancelled'
                stored.job.updated_at = now()
                self.store.write(stored)
            self._release_if_clean(identifier)
            return self.get(identifier)

    async def resume(self, identifier: str) -> ModuleInstallJob:
        async with self.lock:
            stored = self.store.read(identifier)
            if stored.job.state not in ('cancelled', 'interrupted', 'failed', 'awaiting_manual'):
                raise ModuleSetupError('setup_not_resumable')
            if stored.worker is not None or self.tasks or self.stopping:
                raise ModuleSetupError('setup_busy')
            current = await self._review(ModulePlanRequest(features=stored.job.features, download_models=stored.job.download_models))
            request = ModulePlanRequest(features=stored.job.features, download_models=stored.job.download_models)
            previous = {step.module_id: step for step in stored.plan.steps}
            if stored.review_scope != approval_scope(request, self.environment) or any(
                step.operation == 'install' and (step.module_id not in previous or previous[step.module_id].operation != 'install'
                    or step.estimated_download_bytes is None and previous[step.module_id].estimated_download_bytes is not None
                    or (step.estimated_download_bytes or 0) > (previous[step.module_id].estimated_download_bytes or 0))
                for step in current.steps):
                raise ModuleSetupError('plan_changed')
            stored.plan = current
            stored.job.steps = [ModuleJobStep(module_id=step.module_id, name=step.name, state='queued', detail=step.detail) for step in current.steps]
            stored.job.state = 'queued'
            stored.job.error_code = None
            stored.job.updated_at = now()
            from .resource_admission import admission_lock
            async with admission_lock:
                lease_created = self._claim_lease()
                try:
                    _reserve_setup()
                    try:
                        self.store.requeue(stored)
                    except BaseException:
                        _release_setup()
                        raise
                except BaseException:
                    if lease_created:
                        self._close_lease()
                    raise
                self.reservations.add(identifier)
            self.tasks[identifier] = asyncio.create_task(self._execute(identifier))
            return stored.job.model_copy(deep=True)

    async def _recover_worker(self, stored: StoredModuleJob) -> None:
        identity = stored.worker
        if identity is None:
            return
        expected = self.environment.root / '.setup' / 'workers' / f'{stored.job.id}.json'
        if Path(identity.receipt) != expected or expected.is_symlink() or not expected.resolve().is_relative_to(self.environment.root):
            verified = False
        else:
            verified = await terminate_verified(identity)
        if not verified:
            stored.job.state = 'failed'
            stored.job.error_code = 'worker_unverified'
            self.store.write(stored)
            return
        stored.worker = None
        self.store.write(stored)
        expected.unlink(missing_ok=True)

    async def recover(self) -> None:
        async with self.lock:
            records = self.store.records(recovery=True)
            if not records:
                return
            try:
                self._claim_lease()
            except ModuleSetupError as exc:
                if exc.code == 'setup_busy':
                    return
                raise
            for stored in records:
                self._quarantine(stored.job.id)
            for stored in records:
                await self._recover_worker(stored)
                if stored.worker is None and stored.job.state in ('queued', 'running'):
                    stored.job.state = 'interrupted'
                    stored.job.updated_at = now()
                    self.store.write(stored)
                self._release_if_clean(stored.job.id)

    async def shutdown(self) -> None:
        self.stopping = True
        async with self.lock:
            await asyncio.gather(*(cancel_and_wait(task) for task in list(self.tasks.values())))
            for identifier in list(self.reservations):
                stored = self.store.read(identifier)
                if stored.worker is None and stored.job.state in ('queued', 'running'):
                    stored.job.state = 'interrupted'
                    stored.job.updated_at = now()
                    self.store.write(stored)
                self._release_if_clean(identifier)
        self.stopping = False


async def _install(context: InstallContext, identifier: ModuleId, download_models: bool) -> InstallOutcome:
    from .module_install import install
    return await install(context, identifier, download_models)


_service: ModuleJobService | None = None


def service() -> ModuleJobService:
    global _service
    environment = configured_environment()
    if _service is None or _service.environment != environment:
        if _service is not None and (_service.tasks or _service.reservations):
            raise ModuleSetupError('setup_busy')
        _service = ModuleJobService(environment)
    return _service


async def recover() -> None:
    await service().recover()


async def shutdown() -> None:
    if _service is not None:
        await _service.shutdown()
