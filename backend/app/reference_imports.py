"""Optional, bounded reference imports with durable state and owned workers.

Only canonical YouTube video URLs are accepted. The downloader has no user
configuration/plugins and all provider traffic crosses our HTTPS tunnel proxy.
Source containers are retained unchanged in the ordinary upload catalog.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib.util
import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
from urllib.parse import parse_qs, urlsplit
import uuid
from weakref import WeakValueDictionary

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from . import db
from .atomic_files import document_lock, write_object
from .config import FFMPEG_BIN_DIR
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, request_cancel
from .reference_contracts import (
    ReferenceCapabilities, ReferenceDeleteResponse, ReferenceErrorCode, ReferenceImport,
    ReferenceImportRequest, ReferenceImportsResponse, ReferenceLyrics, ReferenceLyricsRequest,
    ReferenceSeparationCapability, ReferenceSource, ReferenceStage, ReferenceToolCapability,
    ReferenceTrackPreparationRequest, ReferenceStageName, ReferenceStageStatus,
)
from .reference_tools import ReferenceToolError, score_pitches, transform_lyrics
from .video_process import WorkerIdentity, WorkerOutputError, WorkerReceipt, read_owned_output, spawn_owned, terminate_verified

logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_DURATION = 600
_ID = re.compile(r'^[0-9a-f]{32}$')
_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_LANGUAGE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,34}$')
_JSON = TypeAdapter(JsonObject)
_jobs: dict[str, _OwnedJob] = {}
_probe_tasks: set[asyncio.Task[ReferenceSource]] = set()
_probe_owners: dict[str, _ProbeOwner] = {}
_blocked_tracks: dict[int, int] = {}
_unverified_workers: set[str] = set()
_recovered_root: Path | None = None
_accepting_work = False
_recover_lock = asyncio.Lock()
_import_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class ReferenceImportError(Exception):
    def __init__(self, code: ReferenceErrorCode) -> None:
        self.code = code
        super().__init__(code)


class _Worker(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pid: int = Field(gt=0)
    token: str = Field(pattern=r'^[0-9a-f]{32}$')
    receipt_name: str = Field(pattern=r'^receipt-[0-9a-f]{32}\.json$')


class _StoredProbe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    worker: _Worker | None = None


class _Promotion(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    filename: str = Field(pattern=r'^reference_[0-9a-f]{32}\.(?:m4a|webm|ogg|opus|wav|flac|mp3|aac|mp4)$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int = Field(gt=0, le=MAX_SOURCE_BYTES)
    duration_seconds: float = Field(gt=0, le=MAX_DURATION)


class _StoredImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = Field(default=1, ge=1, le=1)
    public: ReferenceImport
    source_filename: str = Field(default='', pattern=r'^(?:reference_[0-9a-f]{32}\.(?:m4a|webm|ogg|opus|wav|flac|mp3|aac|mp4))?$')
    worker: _Worker | None = None
    promotion: _Promotion | None = None
    vocal_filename: str = Field(default='', pattern=r'^(?:vocal\.(?:wav|flac|m4a|webm|ogg|opus|mp3|aac))?$')


class _Subtitle(BaseModel):
    ext: str = Field(max_length=30)


class _YtdlpInfo(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{11}$')
    title: str = Field(max_length=500)
    duration: float = Field(gt=0, le=MAX_DURATION)
    is_live: bool = False
    subtitles: dict[str, list[_Subtitle]] = Field(default_factory=dict)


@dataclass
class _OwnedJob:
    task: asyncio.Task[None] | None = None
    proc: asyncio.subprocess.Process | None = None


@dataclass
class _ProbeOwner:
    workspace: Path
    worker: _Worker | None = None
    proc: asyncio.subprocess.Process | None = None
    unverified: bool = False


def canonical_youtube_url(value: str) -> tuple[str, str]:
    try:
        url = urlsplit(value.strip())
        if url.scheme != 'https' or url.username is not None or url.password is not None or url.port is not None or url.fragment:
            raise ReferenceImportError('invalid_url')
        host = (url.hostname or '').lower()
        if host not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
            raise ReferenceImportError('provider_not_allowed')
        if host == 'youtu.be':
            identifier = url.path.removeprefix('/')
        else:
            query = parse_qs(url.query, strict_parsing=False)
            if url.path != '/watch' or len(query.get('v', [])) != 1:
                raise ReferenceImportError('invalid_url')
            identifier = query['v'][0]
        if _VIDEO_ID.fullmatch(identifier) is None:
            raise ReferenceImportError('invalid_url')
        return f'https://www.youtube.com/watch?v={identifier}', identifier
    except ValueError as exc:
        raise ReferenceImportError('invalid_url') from exc


def _provider_host(host: str) -> bool:
    host = host.lower()
    return host in ('youtube.com', 'youtu.be', 'youtubei.googleapis.com') or any(
        host.endswith('.' + parent) for parent in ('youtube.com', 'googlevideo.com', 'ytimg.com')
    )


def public_addresses(host: str, addresses: list[str]) -> list[str]:
    if not _provider_host(host):
        raise ReferenceImportError('redirect_blocked')
    if not addresses:
        raise ReferenceImportError('network_failed')
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ReferenceImportError('private_address') from exc
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_private:
            raise ReferenceImportError('private_address')
    return addresses


class ProviderProxy:
    """Owned HTTPS CONNECT proxy: validate DNS once, connect to that numeric IP.

    Every redirect that opens a different host must make another validated
    CONNECT request. Direct HTTP/FTP/file URLs and all non-provider hosts fail.
    """
    def __init__(self, *, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.bytes = 0
        self.port = 0
        self.server: asyncio.Server | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.error: ReferenceErrorCode | None = None

    async def __aenter__(self) -> ProviderProxy:
        self.server = await asyncio.start_server(self._accept, '127.0.0.1', 0, limit=8192)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in self.tasks:
            request_cancel(task)
        await await_cleanup(asyncio.gather(*self.tasks, return_exceptions=True))

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.tasks) >= 32:
            writer.close()
            return
        task = asyncio.create_task(self._tunnel(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while chunk := await reader.read(65536):
            self.bytes += len(chunk)
            if self.bytes > self.max_bytes:
                self.error = 'network_limit'
                raise ReferenceImportError('network_limit')
            writer.write(chunk)
            await writer.drain()

    async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        remote: asyncio.StreamWriter | None = None
        established = False
        try:
            async with asyncio.timeout(15):
                headers = await reader.readuntil(b'\r\n\r\n')
                line = headers.split(b'\r\n', 1)[0].decode('ascii')
                request = re.fullmatch(r'CONNECT ([A-Za-z0-9.-]+):([0-9]+) HTTP/1\.[01]', line)
                if request is None or request[2] != '443' or not _provider_host(request[1]):
                    raise ReferenceImportError('redirect_blocked')
                resolved = await asyncio.get_running_loop().getaddrinfo(request[1], 443, type=socket.SOCK_STREAM)
                addresses = public_addresses(request[1], list(dict.fromkeys(entry[4][0] for entry in resolved)))
                upstream: asyncio.StreamReader | None = None
                for address in addresses:
                    try:
                        upstream, remote = await asyncio.open_connection(address, 443)
                        break
                    except OSError:
                        continue
                if upstream is None or remote is None:
                    raise ReferenceImportError('network_failed')
                writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
                await writer.drain()
                established = True
            async with asyncio.timeout(330):
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._pipe(reader, remote))
                    group.create_task(self._pipe(upstream, writer))
        except ReferenceImportError as exc:
            self.error = exc.code
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError):
            self.error = 'network_failed'
        except ExceptionGroup:
            self.error = self.error or 'network_failed'
        finally:
            if not established and not writer.is_closing():
                writer.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
                try:
                    await writer.drain()
                except OSError:
                    pass
            for target in (remote, writer):
                if target is not None:
                    target.close()
                    try:
                        await target.wait_closed()
                    except OSError:
                        pass


def ytdlp_command(url: str, workspace: Path, proxy: str, *, download: bool, subtitle_language: str | None = None) -> list[str]:
    canonical, _identifier = canonical_youtube_url(url)
    command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-plugin-dirs', '--no-cache-dir',
               '--no-exec', '--no-remote-components', '--fixup', 'never', '--no-playlist', '--use-extractors', 'Youtube', '--proxy', proxy,
               '--socket-timeout', '15', '--retries', '2', '--fragment-retries', '2', '--extractor-retries', '2',
               '--no-js-runtimes', '--js-runtimes', 'deno', '--quiet', '--no-warnings', '--dump-single-json',
               '--match-filters', 'duration <= 600 & !is_live', '--max-filesize', str(MAX_SOURCE_BYTES),
               '--output', str(workspace / 'source.%(ext)s')]
    if download:
        command += ['--no-simulate', '--format', 'bestaudio[protocol=https]', '--downloader', 'native', '--no-continue', '--no-part']
    else:
        command += ['--skip-download']
    if subtitle_language is not None:
        if _LANGUAGE.fullmatch(subtitle_language) is None:
            raise ReferenceImportError('subtitle_language_unavailable')
        command += ['--write-subs', '--no-write-auto-subs', '--sub-langs', subtitle_language, '--sub-format', 'vtt/srt']
    return [*command, canonical]


def references_root() -> Path:
    # Existing library migration already moves the files subtree; no settings/catalog split is introduced.
    return db.FILES_DIR / '_references'


def _confined_root() -> Path:
    raw_root = references_root()
    root = raw_root.resolve()
    if raw_root.is_symlink() or not root.is_relative_to(db.FILES_DIR.resolve()):
        raise ReferenceImportError('source_missing')
    return root


def job_dir(identifier: str) -> Path:
    if _ID.fullmatch(identifier) is None:
        raise ReferenceImportError('source_missing')
    root = _confined_root()
    target = root / identifier
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ReferenceImportError('source_missing')
    return target


def _path(identifier: str) -> Path:
    return job_dir(identifier) / 'import.json'


def _read(identifier: str) -> _StoredImport:
    try:
        path = _path(identifier)
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ReferenceImportError('source_missing')
        stored = _StoredImport.model_validate_json(path.read_bytes())
        if stored.public.id != identifier or (stored.promotion is not None and not stored.promotion.filename.startswith(f'reference_{identifier}.')):
            raise ReferenceImportError('source_missing')
        return stored
    except (OSError, ValidationError) as exc:
        raise ReferenceImportError('source_missing') from exc


def _write(identifier: str, stored: _StoredImport) -> None:
    path = _path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_object(path, _JSON.validate_json(stored.model_dump_json()))


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _save(public: ReferenceImport) -> None:
    path = _path(public.id)
    with document_lock(path):
        stored = _read(public.id)
        public.updated_at = _now()
        stored.public = public
        _write(public.id, stored)


def get(identifier: str) -> ReferenceImport:
    return _read(identifier).public


def list_imports() -> ReferenceImportsResponse:
    records: list[ReferenceImport] = []
    root = _confined_root()
    if root.is_dir():
        for entry in root.iterdir():
            if _ID.fullmatch(entry.name) is not None:
                try:
                    records.append(get(entry.name))
                except ReferenceImportError:
                    logger.warning('Invalid reference import metadata: %s', entry.name)
    return ReferenceImportsResponse(imports=sorted(records, key=lambda item: item.created_at, reverse=True))


def _tool_path(name: str) -> str | None:
    configured = FFMPEG_BIN_DIR / (name + '.exe' if sys.platform == 'win32' else name)
    return str(configured) if configured.is_file() else shutil.which(name)


def _whisper() -> tuple[str | None, Path | None]:
    configured = os.environ.get('REFERENCE_WHISPER_BIN', 'whisper-cli')
    binary = shutil.which(configured)
    model = os.environ.get('REFERENCE_WHISPER_MODEL', '')
    return binary, Path(model).resolve() if model and Path(model).is_file() else None


def capabilities() -> ReferenceCapabilities:
    from .voice_separation import separation_options
    available = importlib.util.find_spec('yt_dlp') is not None and shutil.which('deno') is not None
    if available:
        try:
            available = importlib.metadata.version('yt-dlp') == '2026.8.19' and importlib.metadata.version('yt-dlp-ejs') == '0.8.0'
        except (importlib.metadata.PackageNotFoundError, ValueError):
            available = False
    source = ReferenceToolCapability(available=available, reason=None if available else 'yt_dlp_missing',
        setup_hint='' if available else 'Install the pinned backend/requirements-reference.txt in the backend environment and Deno 2.6.6 or newer. No models are downloaded by reference setup.')
    binary, model = _whisper()
    reason: ReferenceErrorCode | None = 'whisper_missing' if binary is None else 'whisper_model_missing' if model is None else 'ffmpeg_missing' if _tool_path('ffmpeg') is None else None
    try:
        from .reference_melody import capability
        melody = capability()
    except ImportError:
        melody = ReferenceToolCapability(available=False, reason='melody_unavailable', setup_hint='Install SheetSage2 weights and start YuE2 before preparing melody.')
    separation = [ReferenceSeparationCapability(id=option.id, available=option.available,
                  reason=None if option.available else 'separation_unavailable',
                  setup_hint='' if option.available else 'Install the selected Demucs or configure RoFormer using the existing voice setup.')
                  for option in separation_options().options]
    return ReferenceCapabilities(source_import=source, subtitles=source.model_copy(),
        whisper=ReferenceToolCapability(available=reason is None, reason=reason,
            setup_hint='' if reason is None else 'Install whisper.cpp whisper-cli; set REFERENCE_WHISPER_BIN and REFERENCE_WHISPER_MODEL to an existing local model. FFmpeg is required. Models are never downloaded automatically.'),
        separation=separation, melody=melody)


def _set_proc(identifier: str, proc: asyncio.subprocess.Process | None) -> None:
    owned = _jobs.get(identifier)
    if owned is not None:
        owned.proc = proc


def _write_probe(owner: _ProbeOwner) -> None:
    write_object(owner.workspace / 'probe.json', _JSON.validate_json(_StoredProbe(worker=owner.worker).model_dump_json()))


def _unverified_probes() -> bool:
    return any(owner.unverified for owner in _probe_owners.values())


def _ensure_accepting() -> None:
    if not _accepting_work:
        raise ReferenceImportError('busy')


async def _drain_worker(identifier: str | None, proc: asyncio.subprocess.Process | None, *, probe_id: str | None = None) -> None:
    drained = False

    async def stop() -> None:
        nonlocal drained
        await kill_process_tree(proc)
        drained = True

    try:
        await await_cleanup(stop())
    except BaseException:
        # An exited supervisor is not proof that all descendants stopped.
        # Cancellation propagated after successful cleanup is not a drain failure.
        if not drained and identifier is not None and proc is not None:
            _unverified_workers.add(identifier)
            _set_proc(identifier, proc)
        if not drained and probe_id is not None:
            owner = _probe_owners[probe_id]
            owner.unverified, owner.proc = True, proc
        raise


async def _run_tool(command: list[str], workspace: Path, *, timeout: float, identifier: str | None = None, probe_id: str | None = None) -> bytes:
    receipt = workspace / f'receipt-{uuid.uuid4().hex}.json'
    proc: asyncio.subprocess.Process | None = None
    def identity(value: WorkerIdentity) -> None:
        if probe_id is not None:
            owner = _probe_owners[probe_id]
            owner.worker = _Worker(pid=value.pid, token=value.token, receipt_name=receipt.name)
            _write_probe(owner)
        if identifier is not None:
            path = _path(identifier)
            with document_lock(path):
                stored = _read(identifier)
                stored.worker = _Worker(pid=value.pid, token=value.token, receipt_name=receipt.name)
                # Receipts live at job root so their confinement survives recovery/migration.
                stored.worker.receipt_name = receipt.name
                _write(identifier, stored)
    if identifier is not None:
        receipt = job_dir(identifier) / receipt.name
    try:
        environment = {key: value for key, value in os.environ.items() if not key.lower().endswith('_proxy') and key not in ('NODE_OPTIONS', 'NODE_PATH') and not key.startswith('DENO_')}
        environment['DENO_DIR'] = str(workspace / '.deno-cache')
        proc = await spawn_owned(command, receipt_path=receipt, cwd=workspace, env=environment, stdout=asyncio.subprocess.PIPE, on_identity=identity)
        if identifier is not None:
            _set_proc(identifier, proc)
        if probe_id is not None:
            _probe_owners[probe_id].proc = proc
        output = await read_owned_output(proc, max_bytes=8 * 1024 * 1024, timeout=timeout)
        if proc.returncode != 0:
            logger.warning('Reference tool failed (exit %s)', proc.returncode)
            raise ReferenceImportError('tool_failed')
        return output
    except TimeoutError as exc:
        raise ReferenceImportError('tool_timeout') from exc
    except WorkerOutputError as exc:
        raise ReferenceImportError('size_limit') from exc
    except OSError as exc:
        raise ReferenceImportError('tool_failed') from exc
    finally:
        if proc is not None:
            await _drain_worker(identifier, proc, probe_id=probe_id)
        elif probe_id is not None and (_probe_owners[probe_id].worker is not None or receipt.exists()):
            # Spawn can fail after producing a receipt; keep proof until reconciliation.
            _probe_owners[probe_id].unverified = True
            raise ReferenceImportError('busy')
        if probe_id is not None:
            owner = _probe_owners[probe_id]
            owner.proc, owner.worker = None, None
            try:
                _write_probe(owner)
            except OSError:
                owner.unverified = True
                raise
        if identifier is not None:
            _set_proc(identifier, None)
            path = _path(identifier)
            with document_lock(path):
                stored = _read(identifier)
                stored.worker = None
                _write(identifier, stored)
        receipt.unlink(missing_ok=True)


async def _ytdlp(url: str, workspace: Path, *, download: bool, identifier: str | None = None, language: str | None = None, probe_id: str | None = None) -> _YtdlpInfo:
    if not capabilities().source_import.available:
        raise ReferenceImportError('yt_dlp_missing')
    deno = shutil.which('deno')
    if deno is None:
        raise ReferenceImportError('yt_dlp_missing')
    version = await _run_tool([deno, '--version'], workspace, timeout=5, identifier=identifier, probe_id=probe_id)
    match = re.match(rb'deno ([0-9]+)\.([0-9]+)\.([0-9]+)(?:\s|$)', version)
    if match is None or tuple(int(part) for part in match.groups()) < (2, 6, 6):
        raise ReferenceImportError('yt_dlp_missing')
    async with ProviderProxy(max_bytes=(MAX_SOURCE_BYTES + 16 * 1024 * 1024) if download else 16 * 1024 * 1024) as proxy:
        command = ytdlp_command(url, workspace, f'http://127.0.0.1:{proxy.port}', download=download, subtitle_language=language)
        try:
            output = await _run_tool(command, workspace, timeout=300 if download else 60, identifier=identifier, probe_id=probe_id)
        except ReferenceImportError:
            if proxy.error is not None:
                raise ReferenceImportError(proxy.error) from None
            raise
        if proxy.error is not None:
            raise ReferenceImportError(proxy.error)
        try:
            info = _YtdlpInfo.model_validate_json(output)
        except ValidationError as exc:
            raise ReferenceImportError('duration_limit' if any(error['loc'] == ('duration',) for error in exc.errors()) else 'tool_failed') from exc
        if info.id != canonical_youtube_url(url)[1] or info.is_live:
            raise ReferenceImportError('invalid_url')
        return info


async def _probe_inner(url: str) -> ReferenceSource:
    canonical, identifier = canonical_youtube_url(url)
    root = _confined_root()
    root.mkdir(parents=True, exist_ok=True)
    probe_id = uuid.uuid4().hex
    workspace = root / f'.probe-{probe_id}'
    workspace.mkdir()
    owner = _ProbeOwner(workspace=workspace)
    _probe_owners[probe_id] = owner
    try:
        _write_probe(owner)
        info = await _ytdlp(canonical, workspace, download=False, probe_id=probe_id)
    finally:
        if owner.unverified:
            raise ReferenceImportError('busy')
        try:
            shutil.rmtree(workspace)
        except OSError as exc:
            owner.unverified = True
            raise ReferenceImportError('busy') from exc
        _probe_owners.pop(probe_id, None)
    languages = [language for language, entries in info.subtitles.items()
                 if _LANGUAGE.fullmatch(language) and any(entry.ext in ('vtt', 'srt') for entry in entries)]
    return ReferenceSource(canonical_url=canonical, video_id=identifier, title=info.title,
                           duration_seconds=info.duration, subtitle_languages=languages[:64])


async def probe(request: str) -> ReferenceSource:
    from .resource_admission import admission_lock
    from .video_jobs import work_busy as video_busy
    canonical_youtube_url(request)
    _ensure_accepting()
    await recover()
    async with admission_lock:
        _ensure_accepting()
        if _unverified_workers or _unverified_probes() or video_busy() or len(_probe_tasks) >= 2:
            raise ReferenceImportError('busy')
        task = asyncio.create_task(_probe_inner(request))
        _probe_tasks.add(task)
    try:
        return await task
    finally:
        if not task.done():
            task.cancel()
        try:
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        finally:
            _probe_tasks.discard(task)


def _stages(request: ReferenceImportRequest | ReferenceTrackPreparationRequest) -> list[ReferenceStage]:
    options: list[tuple[ReferenceStageName, bool]] = [
        ('download', isinstance(request, ReferenceImportRequest)), ('lyrics', request.lyrics_source != 'none'),
        ('separation', request.separation != 'none'), ('melody', request.melody)]
    return [ReferenceStage(name=name, status='queued' if enabled else 'skipped') for name, enabled in options]


def _new(request: ReferenceImportRequest | ReferenceTrackPreparationRequest, track_id: int | None = None) -> ReferenceImport:
    identifier = uuid.uuid4().hex
    public = ReferenceImport(id=identifier, status='queued', request=request, track_id=track_id,
        audio_url=f'/api/tracks/{track_id}/audio' if track_id else None, stages=_stages(request), created_at=_now(), updated_at=_now())
    _write(identifier, _StoredImport(public=public))
    return public


def work_busy() -> bool:
    return bool(_unverified_workers) or bool(_probe_owners) or bool(_probe_tasks) or any(owned.task is not None and not owned.task.done() for owned in _jobs.values())


def _check_admission() -> None:
    from .video_jobs import work_busy as video_busy
    _ensure_accepting()
    if _unverified_workers or _unverified_probes() or video_busy() or sum(owned.task is not None and not owned.task.done() for owned in _jobs.values()) >= 2:
        raise ReferenceImportError('busy')


def _admit(public: ReferenceImport) -> None:
    _check_admission()
    if public.track_id in _blocked_tracks:
        raise ReferenceImportError('busy')
    current = _jobs.get(public.id)
    if current is not None and current.task is not None and not current.task.done():
        raise ReferenceImportError('busy')


def _import_lock(identifier: str) -> asyncio.Lock:
    # Hold the lock across awaits; waiting operations keep it alive without retaining deleted IDs forever.
    current = _import_locks.get(identifier)
    if current is None:
        current = asyncio.Lock()
        _import_locks[identifier] = current
    return current


def _launch(public: ReferenceImport) -> None:
    _admit(public)
    owned = _OwnedJob()
    _jobs[public.id] = owned
    owned.task = asyncio.create_task(_run(public.id))


async def create(request: ReferenceImportRequest) -> ReferenceImport:
    from .resource_admission import admission_lock
    canonical, _identifier = canonical_youtube_url(request.url)
    _ensure_accepting()
    await recover()
    async with admission_lock:
        _check_admission()
        public = _new(request.model_copy(update={'url': canonical}))
        _launch(public)
        return public


async def prepare(request: ReferenceTrackPreparationRequest) -> ReferenceImport:
    from .resource_admission import admission_lock
    _ensure_accepting()
    await recover()
    async with admission_lock:
        _check_admission()
        row = db.get_track(request.track_id)
        if row is None:
            raise ReferenceImportError('track_missing')
        if request.track_id in _blocked_tracks or any(get(identifier).track_id == request.track_id for identifier, owned in _jobs.items() if owned.task is not None and not owned.task.done()):
            raise ReferenceImportError('busy')
        public = _new(request, request.track_id)
        _launch(public)
        return public


def _stage(public: ReferenceImport, name: ReferenceStageName, status: ReferenceStageStatus, error: ReferenceErrorCode | None = None) -> None:
    entry = next(stage for stage in public.stages if stage.name == name)
    entry.status = status
    entry.error_code = error
    _save(public)


def _confined_file(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ReferenceImportError('source_missing')
    if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ReferenceImportError('size_limit')
    return path.resolve()


async def _download_source(public: ReferenceImport, workspace: Path) -> Path:
    if not isinstance(public.request, ReferenceImportRequest):
        raise ReferenceImportError('invalid_url')
    await _ytdlp(public.request.url, workspace, download=True, identifier=public.id)
    candidates = [path for path in workspace.iterdir() if re.fullmatch(r'source\.(?:m4a|webm|ogg|opus|wav|flac|mp3|aac|mp4)', path.name)]
    if len(candidates) != 1:
        raise ReferenceImportError('invalid_audio')
    return _confined_file(candidates[0], workspace)


async def _inspect_audio(path: Path, workspace: Path, identifier: str) -> float:
    tool = _tool_path('ffprobe')
    if tool is None:
        raise ReferenceImportError('ffmpeg_missing')
    output = await _run_tool([tool, '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-format_whitelist', 'wav,mp3,flac,ogg,mov,matroska,webm,aac', '-select_streams', 'a:0',
        '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)], workspace, timeout=30, identifier=identifier)
    class Format(BaseModel):
        duration: float = Field(gt=0, le=MAX_DURATION, allow_inf_nan=False)
    class AudioStream(BaseModel):
        codec_type: str
    class Probe(BaseModel):
        format: Format
        streams: list[AudioStream]
    try:
        parsed = Probe.model_validate_json(output)
        if len(parsed.streams) != 1 or parsed.streams[0].codec_type != 'audio':
            raise ReferenceImportError('invalid_audio')
        return parsed.format.duration
    except ValidationError as exc:
        code: ReferenceErrorCode = 'duration_limit' if any(error['loc'] == ('format', 'duration') for error in exc.errors()) else 'invalid_audio'
        raise ReferenceImportError(code) from exc


def _existing_source(identifier: str) -> int | None:
    for row in db.list_tracks('upload'):
        try:
            params = _JSON.validate_json(row['params_json'])
        except ValidationError:
            continue
        provenance = params.get('reference_import')
        if isinstance(provenance, dict) and provenance.get('id') == identifier:
            value: object = row['id']
            if isinstance(value, int) and value > 0:
                return value
    return None


def _digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _pending_source(public: ReferenceImport) -> tuple[Path, float] | None:
    promotion = _read(public.id).promotion
    if promotion is None:
        return None
    target = db.model_dir('upload') / promotion.filename
    if not target.exists():
        return None
    _confined_file(target, db.FILES_DIR)
    if target.stat().st_size != promotion.size_bytes or _digest(target) != promotion.sha256:
        raise ReferenceImportError('source_missing')
    return target, promotion.duration_seconds


def _persist_source(public: ReferenceImport, source: Path, duration: float) -> Path:
    existing = _existing_source(public.id)
    target = db.model_dir('upload') / f'reference_{public.id}{source.suffix}'
    if existing is None:
        if target.exists() and source != target:
            raise ReferenceImportError('busy')
        promotion = _Promotion(filename=target.name, sha256=_digest(source), size_bytes=source.stat().st_size, duration_seconds=duration)
        with document_lock(_path(public.id)):
            stored = _read(public.id)
            stored.promotion = promotion
            _write(public.id, stored)
        # Intent precedes promotion; retry adopts only this exact content identity.
        if source != target:
            source.replace(target)
        provenance: JsonObject = {'id': public.id, 'provider': 'youtube',
            'canonical_url': public.source.canonical_url if public.source else '',
            'video_id': public.source.video_id if public.source else '', 'imported_at': _now(),
            'sha256': promotion.sha256, 'size_bytes': promotion.size_bytes}
        existing = db.insert_track(model='upload', title=public.request.title if isinstance(public.request, ReferenceImportRequest) and public.request.title else public.source.title if public.source else 'Reference',
            lyrics='', seed=None, duration_ms=duration * 1000, wall_ms=None,
            params={'source': 'reference_import', 'reference_import': provenance}, audio_path=target, abc_path=None)
    public.track_id = existing
    public.audio_url = f'/api/tracks/{existing}/audio'
    with document_lock(_path(public.id)):
        stored = _read(public.id)
        stored.public = public
        stored.source_filename = target.name
        stored.promotion = None
        _write(public.id, stored)
    return target


def _source_path(public: ReferenceImport) -> Path:
    if public.track_id is None:
        raise ReferenceImportError('source_missing')
    row = db.get_track(public.track_id)
    if row is None:
        raise ReferenceImportError('track_missing')
    stored = _read(public.id)
    if stored.source_filename:
        return _confined_file(db.model_dir('upload') / stored.source_filename, db.FILES_DIR)
    from .audio_versions import list_versions, retain_original, resolve_source
    if isinstance(public.request, ReferenceTrackPreparationRequest) and public.request.source_version_id is not None:
        return _confined_file(resolve_source(public.track_id, public.request.source_version_id), db.FILES_DIR)
    # Reconcile legacy replacements before considering current exports.
    original = next((item for item in list_versions(public.track_id).versions if item.kind == 'original' and item.status == 'done'), None)
    if original is None:
        original = retain_original(public.track_id, _confined_file(Path(row['audio_path']), db.FILES_DIR))
    return _confined_file(resolve_source(public.track_id, original.id), db.FILES_DIR)


async def _subtitle_lyrics(public: ReferenceImport, workspace: Path) -> ReferenceLyrics:
    request = public.request
    if not isinstance(request, ReferenceImportRequest) or request.subtitle_language is None:
        raise ReferenceImportError('subtitle_language_required')
    if public.source is None or request.subtitle_language not in public.source.subtitle_languages:
        raise ReferenceImportError('subtitle_language_unavailable')
    await _ytdlp(request.url, workspace, download=False, identifier=public.id, language=request.subtitle_language)
    candidates = [workspace / f'source.{request.subtitle_language}.{extension}' for extension in ('vtt', 'srt')]
    candidates = [path for path in candidates if path.is_file()]
    if len(candidates) != 1:
        raise ReferenceImportError('subtitles_unavailable')
    path = _confined_file(candidates[0], workspace)
    if path.stat().st_size > 512 * 1024:
        raise ReferenceImportError('size_limit')
    try:
        return transform_lyrics(ReferenceLyricsRequest(text=path.read_text(encoding='utf-8'),
            format='vtt' if path.suffix == '.vtt' else 'srt', language=request.subtitle_language))
    except (UnicodeError, ValidationError) as exc:
        raise ReferenceImportError('unsupported_subtitles') from exc


async def _transcribe_whisper(public: ReferenceImport, source: Path, workspace: Path) -> ReferenceLyrics:
    capability = capabilities().whisper
    if not capability.available:
        raise ReferenceImportError(capability.reason or 'whisper_missing')
    binary, model = _whisper()
    ffmpeg = _tool_path('ffmpeg')
    if binary is None or model is None or ffmpeg is None:
        raise ReferenceImportError('whisper_missing')
    wav = workspace / 'whisper-input.wav'
    output = workspace / 'whisper-output'
    await _run_tool([ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,flac,ogg,mov,matroska,webm,aac', '-i', str(source), '-map', '0:a:0', '-vn', '-t', str(MAX_DURATION), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], workspace, timeout=120, identifier=public.id)
    from .stems import gpu_lock
    async with gpu_lock:
        await _run_tool([binary, '-m', str(model), '-f', str(wav), '-osrt', '-of', str(output), '-l', public.request.subtitle_language or 'auto'], workspace, timeout=900, identifier=public.id)
    subtitles = _confined_file(output.with_suffix('.srt'), workspace)
    if subtitles.stat().st_size > 512 * 1024:
        raise ReferenceImportError('size_limit')
    result = transform_lyrics(ReferenceLyricsRequest(text=subtitles.read_text(encoding='utf-8'), format='srt', language=public.request.subtitle_language))
    return result.model_copy(update={'lines': [line.model_copy(update={'provenance': 'whisper'}) for line in result.lines]})


async def _separate(public: ReferenceImport, source: Path, workspace: Path) -> Path:
    from .voice_separation import separate_vocal
    quality = public.request.separation
    if quality == 'none':
        return source
    if not next(option.available for option in capabilities().separation if option.id == quality):
        raise ReferenceImportError('separation_unavailable')
    receipt = job_dir(public.id) / f'receipt-{uuid.uuid4().hex}.json'
    children: list[asyncio.subprocess.Process] = []

    def identity(value: WorkerIdentity) -> None:
        with document_lock(_path(public.id)):
            stored = _read(public.id)
            stored.worker = _Worker(pid=value.pid, token=value.token, receipt_name=receipt.name)
            _write(public.id, stored)

    async def spawn(argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
        proc = await spawn_owned(argv, receipt_path=receipt, cwd=cwd, env=env, stdout=stdout, on_identity=identity)
        children.append(proc)
        _set_proc(public.id, proc)
        return proc

    try:
        async with asyncio.timeout(1200):
            vocal = await separate_vocal(source, workspace / 'stems', quality=quality,
                log_name=f'reference_{public.id}', on_proc=lambda proc: _set_proc(public.id, proc), spawn=spawn)
        return _confined_file(vocal, workspace)
    finally:
        for child in children:
            await _drain_worker(public.id, child)
        _set_proc(public.id, None)
        with document_lock(_path(public.id)):
            stored = _read(public.id)
            if stored.worker is not None and stored.worker.receipt_name == receipt.name:
                stored.worker = None
                _write(public.id, stored)
        receipt.unlink(missing_ok=True)


async def _prepare_melody(public: ReferenceImport, source: Path, workspace: Path) -> str:
    try:
        from .reference_melody import prepare_reference_melody
    except ImportError as exc:
        raise ReferenceImportError('melody_unavailable') from exc
    if not capabilities().melody.available:
        raise ReferenceImportError('melody_unavailable')
    async def run_tool(command: list[str], directory: Path, deadline: float) -> bytes:
        return await _run_tool(command, directory, timeout=deadline, identifier=public.id)
    async with asyncio.timeout(1200):
        return await prepare_reference_melody(source, workspace=workspace, on_proc=lambda proc: _set_proc(public.id, proc), run_tool=run_tool)


def vocal_path(identifier: str) -> Path:
    stored = _read(identifier)
    if not stored.vocal_filename:
        raise ReferenceImportError('source_missing')
    return _confined_file(job_dir(identifier) / stored.vocal_filename, job_dir(identifier))


def _persist_vocal(public: ReferenceImport, source: Path) -> Path:
    target = job_dir(public.id) / ('vocal' + source.suffix)
    stored = _read(public.id)
    # Replacement is atomically promoted; the original source stays in its catalog.
    if source != target:
        source.replace(target)
    stored.vocal_filename = target.name
    public.vocal_audio_url = f'/api/references/imports/{public.id}/vocal'
    stored.public = public
    _write(public.id, stored)
    return target


def _persist_score(public: ReferenceImport, workspace: Path) -> None:
    # Prepared material is reviewed in the generator; catalog metadata is never overwritten.
    if public.abc is not None:
        target = job_dir(public.id) / 'melody.abc'
        temporary = workspace / 'melody.abc'
        temporary.write_text(public.abc, encoding='utf-8')
        temporary.replace(target)


async def _run(identifier: str) -> None:
    public = get(identifier)
    workspace = job_dir(identifier) / f'staging-{uuid.uuid4().hex}'
    workspace.mkdir()
    try:
        public.status = 'running'
        _save(public)
        if public.track_id is None:
            if not isinstance(public.request, ReferenceImportRequest):
                raise ReferenceImportError('track_missing')
            _stage(public, 'download', 'running')
            public.source = await probe(public.request.url)
            _save(public)
            existing = _existing_source(public.id)
            if existing is not None:
                public.track_id = existing
                public.audio_url = f'/api/tracks/{existing}/audio'
                _save(public)
                source = _source_path(public)
            else:
                pending = _pending_source(public)
                if pending is None:
                    source = await _download_source(public, workspace)
                    _confined_file(source, workspace)
                    duration = await _inspect_audio(source, workspace, identifier)
                else:
                    source, duration = pending
                source = _persist_source(public, source, duration)
            _stage(public, 'download', 'done')
        else:
            source = _source_path(public)
            await _inspect_audio(source, workspace, identifier)
        stages: list[tuple[ReferenceStageName, bool]] = [('separation', public.request.separation != 'none'), ('lyrics', public.request.lyrics_source != 'none'), ('melody', public.request.melody)]
        prepared_source = source
        for name, enabled in stages:
            if not enabled:
                continue
            if _unverified_workers:
                raise ReferenceImportError('busy')
            existing_stage = next(stage for stage in public.stages if stage.name == name)
            if existing_stage.status == 'done':
                if name == 'separation':
                    prepared_source = vocal_path(identifier)
                continue
            _stage(public, name, 'running')
            try:
                if name == 'separation':
                    prepared_source = _persist_vocal(public, await _separate(public, source, workspace))
                elif name == 'lyrics':
                    public.lyrics = await _subtitle_lyrics(public, workspace) if public.request.lyrics_source == 'subtitles' else await _transcribe_whisper(public, prepared_source, workspace)
                else:
                    candidate = await _prepare_melody(public, prepared_source, workspace)
                    if len(candidate) > 100_000:
                        raise ReferenceImportError('size_limit')
                    score_pitches(candidate)
                    public.abc = candidate
                _stage(public, name, 'done')
                _persist_score(public, workspace)
            except (ReferenceImportError, ReferenceToolError) as exc:
                missing = exc.code in ('whisper_missing', 'whisper_model_missing', 'ffmpeg_missing', 'separation_unavailable', 'melody_unavailable')
                _stage(public, name, 'missing_tool' if missing else 'failed', exc.code)
            except TimeoutError:
                _stage(public, name, 'failed', 'tool_timeout')
            except Exception:
                logger.exception('Reference preparation failed: %s stage %s', identifier, name)
                if identifier in _unverified_workers:
                    raise ReferenceImportError('busy') from None
                _stage(public, name, 'failed', 'tool_failed')
        public.status = 'partial' if any(stage.status in ('failed', 'missing_tool') for stage in public.stages) else 'done'
        _save(public)
    except asyncio.CancelledError:
        public.status = 'cancelled'
        for stage in public.stages:
            if stage.status in ('queued', 'running'):
                stage.status = 'cancelled'
                stage.error_code = None
        _save(public)
        raise
    except (ReferenceImportError, ReferenceToolError) as exc:
        public.status = 'partial' if public.track_id is not None else 'failed'
        active = next((stage for stage in public.stages if stage.status == 'running'), public.stages[0])
        active.status = 'failed'
        active.error_code = exc.code
        _save(public)
    except Exception:
        logger.exception('Reference import failed: %s', identifier)
        public.status = 'partial' if public.track_id is not None else 'failed'
        active = next((stage for stage in public.stages if stage.status == 'running'), public.stages[0])
        active.status, active.error_code = 'failed', 'import_failed'
        _save(public)
    finally:
        owned = _jobs.get(identifier)
        if owned is not None:
            try:
                await _drain_worker(identifier, owned.proc)
                owned.proc = None
            except Exception:
                logger.exception('Reference worker cleanup failed: %s', identifier)
                public.status = 'partial' if public.track_id is not None else 'failed'
                active = next((stage for stage in public.stages if stage.status in ('running', 'cancelled')), public.stages[0])
                active.status, active.error_code = 'failed', 'busy'
                _save(public)
        if identifier not in _unverified_workers:
            shutil.rmtree(workspace)


async def _cancel(identifier: str) -> ReferenceImport:
    if identifier in _unverified_workers:
        raise ReferenceImportError('busy')
    owned = _jobs.get(identifier)
    if owned is not None:
        await cancel_and_wait(owned.task)
    # Task cleanup can introduce a drain failure while cancellation is awaited.
    if identifier in _unverified_workers:
        raise ReferenceImportError('busy')
    public = get(identifier)
    if public.status in ('queued', 'running'):
        public.status = 'cancelled'
        for stage in public.stages:
            if stage.status in ('queued', 'running'):
                stage.status = 'cancelled'
        _save(public)
    return public


async def cancel(identifier: str) -> ReferenceImport:
    async with _import_lock(identifier):
        return await _cancel(identifier)


async def retry(identifier: str) -> ReferenceImport:
    from .resource_admission import admission_lock
    _ensure_accepting()
    async with _import_lock(identifier):
        await recover()
        async with admission_lock:
            public = get(identifier)
            if public.status in ('queued', 'running'):
                raise ReferenceImportError('busy')
            _admit(public)
            for stage in public.stages:
                if stage.status in ('failed', 'missing_tool', 'cancelled'):
                    stage.status, stage.error_code = 'queued', None
            public.status = 'queued'
            _save(public)
            _launch(public)
            return public


async def delete(identifier: str) -> ReferenceDeleteResponse:
    async with _import_lock(identifier):
        await _cancel(identifier)
        public = get(identifier)
        shutil.rmtree(job_dir(identifier))
        _jobs.pop(identifier, None)
        return ReferenceDeleteResponse(deleted=True, track_retained=public.track_id is not None and db.get_track(public.track_id) is not None)


async def _recover_probe_workers(root: Path) -> None:
    if not root.exists():
        return
    for workspace in root.glob('.probe-*'):
        identifier = workspace.name.removeprefix('.probe-')
        if _ID.fullmatch(identifier) is None or workspace.is_symlink() or not workspace.is_dir() or not workspace.resolve().is_relative_to(root):
            continue
        owner = _probe_owners.get(identifier)
        if owner is not None and not owner.unverified:
            continue
        if owner is None:
            owner = _ProbeOwner(workspace=workspace, unverified=True)
            _probe_owners[identifier] = owner
        try:
            metadata = workspace / 'probe.json'
            if metadata.is_symlink() or metadata.stat().st_size > 65536:
                raise ValueError('invalid_probe_metadata')
            stored = _StoredProbe.model_validate_json(metadata.read_bytes())
            owner.worker = owner.worker or stored.worker
            if owner.worker is None:
                # Cancellation can precede the spawn callback. Recover the exact bounded receipt.
                receipts = list(workspace.glob('receipt-*.json'))
                if len(receipts) > 1:
                    raise ValueError('ambiguous_probe_receipt')
                if receipts:
                    receipt = receipts[0]
                    if receipt.is_symlink() or receipt.stat().st_size > 65536:
                        raise ValueError('invalid_probe_receipt')
                    proof = WorkerReceipt.model_validate_json(receipt.read_bytes())
                    owner.worker = _Worker(pid=proof.pid, token=proof.token, receipt_name=receipt.name)
            if owner.worker is not None:
                receipt = workspace / owner.worker.receipt_name
                if receipt.is_symlink():
                    raise ValueError('invalid_probe_receipt')
                identity = WorkerIdentity(pid=owner.worker.pid, token=owner.worker.token, receipt=str(receipt))
                if not await terminate_verified(identity):
                    logger.error('Cannot verify interrupted reference probe: %s', identifier)
                    continue
            # A retained Process may still own a Windows Job handle after its wrapper exited.
            await _drain_worker(None, owner.proc, probe_id=identifier)
            shutil.rmtree(workspace)
            _probe_owners.pop(identifier, None)
        except (OSError, ValueError, ValidationError, TimeoutError):
            logger.exception('Reference probe reconciliation failed: %s', identifier)


async def recover() -> None:
    global _recovered_root
    root = _confined_root()
    async with _recover_lock:
        if _recovered_root == root and not _unverified_workers and not _unverified_probes():
            return
        await _recover_probe_workers(root)
        for public in list_imports().imports:
            if public.id in _jobs and public.id not in _unverified_workers:
                continue
            stored = _read(public.id)
            if stored.worker is not None:
                identity = WorkerIdentity(pid=stored.worker.pid, token=stored.worker.token, receipt=str(job_dir(public.id) / stored.worker.receipt_name))
                if not await terminate_verified(identity):
                    logger.error('Cannot verify interrupted reference worker: %s', public.id)
                    _unverified_workers.add(public.id)
                    continue
                owned = _jobs.get(public.id)
                if owned is not None and owned.proc is not None:
                    try:
                        await _drain_worker(public.id, owned.proc)
                    except (OSError, TimeoutError):
                        logger.exception('Reference worker reconciliation failed: %s', public.id)
                        continue
                    owned.proc = None
                _unverified_workers.discard(public.id)
                stored.worker = None
            if public.status in ('queued', 'running'):
                public.status = 'partial' if public.track_id is not None else 'failed'
                for stage in public.stages:
                    if stage.status in ('queued', 'running'):
                        stage.status, stage.error_code = 'failed', 'import_interrupted'
                stored.public = public
            _write(public.id, stored)
            for staged in job_dir(public.id).glob('staging-*'):
                if staged.is_dir() and not staged.is_symlink() and staged.resolve().is_relative_to(job_dir(public.id).resolve()):
                    shutil.rmtree(staged)
        _recovered_root = root


async def start() -> None:
    global _accepting_work
    _accepting_work = False
    await recover()
    _accepting_work = True


async def shutdown() -> None:
    global _accepting_work
    _accepting_work = False
    for task in _probe_tasks:
        task.cancel()
    for owned in _jobs.values():
        request_cancel(owned.task)
    await await_cleanup(asyncio.gather(*[cancel(identifier) for identifier in list(_jobs)], *list(_probe_tasks), return_exceptions=True))
    try:
        await await_cleanup(recover())
    except ReferenceImportError as exc:
        # Known tasks were drained above; never traverse an invalid storage root during shutdown.
        logger.warning('Reference storage unavailable during shutdown: %s', exc.code)


@asynccontextmanager
async def protect_track_removal(track_id: int) -> AsyncIterator[None]:
    _blocked_tracks[track_id] = _blocked_tracks.get(track_id, 0) + 1
    try:
        identifiers = [public.id for public in list_imports().imports if public.track_id == track_id]
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in identifiers)))
        yield
    finally:
        if _blocked_tracks[track_id] == 1:
            del _blocked_tracks[track_id]
        else:
            _blocked_tracks[track_id] -= 1
