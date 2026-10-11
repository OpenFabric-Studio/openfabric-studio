"""Owned local clip copies behind a dedicated, temporary HTTPS tunnel.

Restart never republishes old capabilities. Only the clip-only relay is tunneled;
the main application, settings and library endpoints are never mounted here.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
import socket
import stat
import sys
import threading
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, Field, model_validator
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from . import clip_host_settings as settings, openrouter_requests as ledger
from .atomic_files import document_lock
from .clip_host_contracts import ClipHostContract, ClipHostStatus, HostedClip, Identifier, Digest
from .config import DATA_DIR
from .job_lifecycle import await_cleanup, kill_process_tree
from .openrouter_errors import OpenRouterError
from .video_io import copy_verified, hash_file
from .video_process import WorkerIdentity, spawn_owned, terminate_verified
from .video_projects import VideoProjectError

MAX_CLIP_BYTES=3749976
_VERIFY_TIMEOUT=180.0
_VERIFY_BACKOFF=5.0
_LOG=logging.getLogger(__name__)
_ID=re.compile(r'^[0-9a-f]{32}$')
_TOKEN=re.compile(r'^[0-9a-f]{64}$')
_ORIGIN=re.compile(r'https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?=[\s\"\'])')
_ACTIVE: set[asyncio.Task[object]]=set()
_REGISTRY_LOCK=threading.RLock()
_operation_lock=asyncio.Lock()
_served: dict[str,str]={}
_origin: str | None=None
_closing=False
_unresolved=False
_maintenance: asyncio.Task[None] | None=None
_MAC_BINARY_PATHS=(Path('/opt/homebrew/bin/cloudflared'),Path('/usr/local/bin/cloudflared'))


class _SecretURLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message=record.getMessage()
        redacted=re.sub(r'https?://[^\s\"\']+/clip/[0-9a-f]{64}\.mp4','[private clip URL]',message)
        if redacted!=message:
            record.msg=redacted; record.args=()
        return True


# HTTPX normally logs full request URLs at INFO. Only capability URLs are
# filtered; ordinary application/provider diagnostics keep their normal level.
logging.getLogger('httpx').addFilter(_SecretURLFilter())


class ClipHostError(Exception):
    def __init__(self, code: str, status: int=503) -> None:
        self.code=code; self.status=status
        super().__init__(code)


class _Grant(ClipHostContract):
    receipt_id: Identifier
    source_sha256: Digest
    token: str=Field(pattern=r'^[0-9a-f]{64}$',repr=False)
    bytes: int=Field(gt=0,le=MAX_CLIP_BYTES)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    cleanup_pending: bool=False

    @model_validator(mode='after')
    def expiry(self) -> _Grant:
        if not timedelta(0)<self.expires_at-self.created_at<=timedelta(hours=24):
            raise ValueError('clip_host_expiry_invalid')
        return self


class _RelayServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None,None,None]:
        # The outer application owns process signal handlers.
        yield


@dataclass
class _Relay:
    server: _RelayServer
    task: asyncio.Task[None]
    listener: socket.socket
    proc: asyncio.subprocess.Process | None=None
    drain: asyncio.Task[None] | None=None


_relay: _Relay | None=None


def _root() -> Path:
    path=DATA_DIR/'providers'/'clip-assets'
    if any(item.is_symlink() for item in (DATA_DIR,path.parent,path)):
        raise ClipHostError('clip_host_storage_unavailable')
    return path.resolve()


def _child(parent: Path, name: str) -> Path:
    path=parent/name
    if path.is_symlink() or path.resolve().parent!=parent.resolve():
        raise ClipHostError('clip_host_storage_unavailable')
    return path


def _folder(identifier: str) -> Path:
    if _ID.fullmatch(identifier) is None: raise ClipHostError('clip_host_request_invalid',400)
    return _child(_root(),identifier)


def _private_write(path: Path, data: bytes) -> None:
    temporary=_child(path.parent,'.'+path.name+'.'+uuid4().hex+'.tmp')
    with document_lock(path):
        try:
            with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)


def _save(path: Path, model: _Grant | WorkerIdentity) -> None:
    _private_write(path,model.model_dump_json().encode())


def _load(identifier: str) -> _Grant:
    path=_child(_folder(identifier),'grant.json')
    try:
        if path.stat().st_size>4096: raise ValueError('grant_limit')
        grant=_Grant.model_validate_json(path.read_bytes())
        if grant.receipt_id!=identifier: raise ValueError('grant_identity')
        return grant
    except (OSError,ValueError) as error: raise ClipHostError('clip_host_storage_unavailable') from error


def _binary() -> str | None:
    value=shutil.which('cloudflared')
    if value: return str(Path(value).resolve())
    if sys.platform=='darwin':
        for candidate in _MAC_BINARY_PATHS:
            if candidate.is_file() and os.access(candidate,os.X_OK): return str(candidate.resolve())
    return None


def status() -> ClipHostStatus:
    preference=settings.load(); available=_binary() is not None; root=_root()
    count=sum(1 for child in root.iterdir() if _ID.fullmatch(child.name)) if root.exists() else 0
    return ClipHostStatus(enabled=preference.enabled,configured=preference.enabled and available and not _unresolved,
        binary_available=available,tunnel_active=_origin is not None,active_clips=count)


async def ensure_ready() -> None:
    if _closing: raise ClipHostError('clip_host_stopping',409)
    if _unresolved: raise ClipHostError('clip_host_worker_unresolved')
    if not settings.load().enabled: raise ClipHostError('clip_host_disabled',409)
    if _binary() is None: raise ClipHostError('clip_host_binary_missing',409)
    _root()


@asynccontextmanager
async def _owned() -> AsyncIterator[None]:
    if _closing: raise ClipHostError('clip_host_stopping',409)
    task: asyncio.Task[object] | None=asyncio.current_task()
    if task is None: raise ClipHostError('clip_host_stopping',409)
    with _REGISTRY_LOCK: _ACTIVE.add(task)
    try: yield
    finally:
        with _REGISTRY_LOCK: _ACTIVE.discard(task)


class _ClipRelay(FastAPI):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def private_send(message: Message) -> None:
            if message['type']=='http.response.start':
                MutableHeaders(raw=message['headers'])['cache-control']='private, no-store, max-age=0'
            await send(message)
        await super().__call__(scope,receive,private_send)


class _ClipResponse(FileResponse):
    """Keep FileResponse's range parsing, serve an immutable checked snapshot.

    FileResponse otherwise reopens the path after validation. A local mutation
    at that point must not expose different bytes through an existing token.
    """
    def __init__(self, path: Path, data: bytes, info: os.stat_result) -> None:
        super().__init__(path,media_type='video/mp4',stat_result=info,
            headers={'Cache-Control':'private, no-store, max-age=0','X-Content-Type-Options':'nosniff'})
        self._clip_data=data

    async def _handle_simple(self, send: Send, send_header_only: bool, send_pathsend: bool) -> None:
        await send({'type':'http.response.start','status':self.status_code,'headers':self.raw_headers})
        await send({'type':'http.response.body','body':b'' if send_header_only else self._clip_data,'more_body':False})

    async def _handle_single_range(self, send: Send, start: int, end: int, file_size: int, send_header_only: bool) -> None:
        headers=MutableHeaders(raw=list(self.raw_headers))
        headers['content-range']=f'bytes {start}-{end-1}/{file_size}'
        headers['content-length']=str(end-start)
        await send({'type':'http.response.start','status':206,'headers':headers.raw})
        await send({'type':'http.response.body','body':b'' if send_header_only else self._clip_data[start:end],'more_body':False})

    async def _handle_multiple_ranges(self, send: Send, ranges: list[tuple[int,int]], file_size: int, send_header_only: bool) -> None:
        boundary=secrets.token_hex(13)
        length,header=self.generate_multipart(ranges,boundary,file_size,self.headers['content-type'])
        headers=MutableHeaders(raw=list(self.raw_headers))
        headers['content-type']=f'multipart/byteranges; boundary={boundary}'
        headers['content-length']=str(length)
        await send({'type':'http.response.start','status':206,'headers':headers.raw})
        if not send_header_only:
            for start,end in ranges:
                await send({'type':'http.response.body','body':header(start,end),'more_body':True})
                await send({'type':'http.response.body','body':self._clip_data[start:end]+b'\r\n','more_body':True})
        await send({'type':'http.response.body','body':b'' if send_header_only else f'--{boundary}--'.encode('latin-1'),'more_body':False})


relay_app=_ClipRelay(openapi_url=None,docs_url=None,redoc_url=None,redirect_slashes=False)


@relay_app.api_route('/clip/{token}.mp4',methods=['GET','HEAD'])
async def clip(token: str, request: Request) -> FileResponse:
    if _TOKEN.fullmatch(token) is None or request.url.query: raise HTTPException(404)
    identifier=_served.get(token)
    if identifier is None: raise HTTPException(404)
    try:
        grant=_load(identifier)
        if grant.token!=token or grant.cleanup_pending or grant.expires_at<=datetime.now(timezone.utc):
            raise ClipHostError('clip_host_expired')
        path=_child(_folder(identifier),'clip.mp4')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size!=grant.bytes or await hash_file(path)!=grant.source_sha256:
            raise ClipHostError('clip_host_source_changed')
        path=_child(_folder(identifier),'clip.mp4')
        with path.open('rb') as handle: data=handle.read(grant.bytes+1)
        if len(data)!=grant.bytes or hashlib.sha256(data).hexdigest()!=grant.source_sha256:
            raise ClipHostError('clip_host_source_changed')
        return _ClipResponse(path,data,info)
    except (ClipHostError,OSError,VideoProjectError,ValueError) as error: raise HTTPException(404) from error


async def _drain_output(proc: asyncio.subprocess.Process, ready: asyncio.Future[str]) -> None:
    global _origin
    window=b''
    try:
        if proc.stdout is None: raise ClipHostError('clip_host_tunnel_failed')
        while chunk:=await proc.stdout.read(4096):
            window=(window+chunk)[-8192:]
            match=_ORIGIN.search(window.decode(errors='replace'))
            if match and not ready.done(): ready.set_result(match.group())
        if not ready.done(): ready.set_exception(ClipHostError('clip_host_tunnel_failed'))
        await proc.wait()
    finally:
        _origin=None
        _served.clear()


def _worker_environment() -> dict[str,str]:
    allowed={'PATH','HOME','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','LANG','LC_ALL'}
    return {name:value for name,value in os.environ.items() if name in allowed}


async def _start_tunnel() -> str:
    global _relay,_origin
    if _origin is not None: return _origin
    if _relay is not None: await _stop_tunnel()
    binary=_binary()
    if binary is None: raise ClipHostError('clip_host_binary_missing',409)
    root=_root(); root.mkdir(parents=True,exist_ok=True,mode=0o700)
    config=_child(root,'relay.yaml'); _private_write(config,b'{}\n')
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0)); listener.setblocking(False)
    server=_RelayServer(uvicorn.Config(relay_app,host='127.0.0.1',port=0,log_config=None,
        access_log=False,proxy_headers=False,server_header=False,lifespan='off',ws='none',
        timeout_graceful_shutdown=3,log_level='warning'))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    _relay=_Relay(server,task,listener)
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done(): raise ClipHostError('clip_host_tunnel_failed')
                await asyncio.sleep(0.01)
        port=listener.getsockname()[1]
        receipt=_child(root,'worker-receipt.json'); ownership=_child(root,'worker.json')
        argv=[binary,'tunnel','--config',str(config),'--no-autoupdate','--url',f'http://127.0.0.1:{port}',
            '--metrics','127.0.0.1:0','--management-diagnostics=false']
        proc=await spawn_owned(argv,receipt_path=receipt,env=_worker_environment(),stdout=asyncio.subprocess.PIPE,
            on_identity=lambda value:_save(ownership,WorkerIdentity(pid=value.pid,token=value.token,receipt=receipt.name)))
        _relay.proc=proc
        ready: asyncio.Future[str]=asyncio.get_running_loop().create_future()
        _relay.drain=asyncio.create_task(_drain_output(proc,ready))
        async with asyncio.timeout(60): _origin=await ready
        return _origin
    except BaseException as error:
        await await_cleanup(_stop_tunnel())
        if isinstance(error,(TimeoutError,OSError)): raise ClipHostError('clip_host_tunnel_failed') from error
        raise


async def _stop_tunnel() -> None:
    global _relay,_origin,_unresolved
    relay=_relay; _origin=None; _served.clear()
    if relay is None: return
    drained=False
    try:
        # kill_process_tree drains stdout itself: release our reader first.
        if relay.drain is not None:
            if not relay.drain.done(): relay.drain.cancel()
            await await_cleanup(asyncio.gather(relay.drain,return_exceptions=True))
        if relay.proc is not None: await await_cleanup(kill_process_tree(relay.proc))
        elif _child(_root(),'worker.json').exists(): await _recover_worker()
        drained=True
    except BaseException:
        _unresolved=True
        raise
    finally:
        relay.server.should_exit=True
        try:
            async with asyncio.timeout(8): await asyncio.shield(relay.task)
        except (TimeoutError,asyncio.CancelledError):
            relay.task.cancel(); await await_cleanup(asyncio.gather(relay.task,return_exceptions=True))
        relay.listener.close()
        if relay.drain is not None:
            if not relay.drain.done(): relay.drain.cancel()
            await await_cleanup(asyncio.gather(relay.drain,return_exceptions=True))
        if drained:
            root=_root()
            _child(root,'worker.json').unlink(missing_ok=True)
            _child(root,'worker-receipt.json').unlink(missing_ok=True)
            _relay=None


class ClipHostClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None=None) -> None:
        self.transport=transport

    async def verify(self, hosted: HostedClip, size: int) -> None:
        try:
            async with asyncio.timeout(_VERIFY_TIMEOUT):
                delay=_VERIFY_BACKOFF
                while True:
                    try:
                        await self._verify_once(hosted,size)
                        return
                    except (httpx.HTTPError,TimeoutError): pass
                    except ClipHostError as error:
                        if error.code!='clip_host_edge_not_ready': raise
                    # Newly issued hostnames can be negatively cached by the
                    # local resolver. Only reads repeat; tunnel and DNS settings
                    # stay unchanged, and no AI submission is part of this loop.
                    await asyncio.sleep(delay)
                    delay=min(10.0,delay*2)
        except TimeoutError as error: raise ClipHostError('clip_host_unavailable') from error

    def _delivery_headers(self, response: httpx.Response, size: int) -> None:
        if response.status_code in {408,429,502,503,504}: raise ClipHostError('clip_host_edge_not_ready')
        directives={part.strip().lower() for part in response.headers.get('cache-control','').split(',')}
        if (response.status_code!=200 or response.headers.get('content-type','').split(';')[0]!='video/mp4'
                or response.headers.get('content-length')!=str(size) or 'no-store' not in directives):
            raise ClipHostError('clip_host_delivery_invalid')
        for part in directives:
            if part.startswith(('max-age=','s-maxage=')) and part.split('=',1)[1].strip('"')!='0':
                raise ClipHostError('clip_host_delivery_invalid')

    async def _verify_once(self, hosted: HostedClip, size: int) -> None:
        try:
            async with asyncio.timeout(60), httpx.AsyncClient(follow_redirects=False,trust_env=False,
                    transport=self.transport,timeout=httpx.Timeout(30,connect=10)) as client:
                async with client.stream('HEAD',hosted.url) as response:
                    self._delivery_headers(response,size)
                async with client.stream('GET',hosted.url) as response:
                    self._delivery_headers(response,size)
                    digest=hashlib.sha256(); count=0
                    async for chunk in response.aiter_bytes(65536):
                        count+=len(chunk)
                        if count>size: raise ClipHostError('clip_host_delivery_invalid')
                        digest.update(chunk)
                    if count!=size or digest.hexdigest()!=hosted.source_sha256:
                        raise ClipHostError('clip_host_delivery_invalid')
        except (httpx.HTTPError,TimeoutError): raise


client=ClipHostClient()


async def stage_clip(receipt_id: str, path: Path, expected_sha256: str) -> HostedClip:
    async with _owned(),_operation_lock:
        await ensure_ready()
        folder=_folder(receipt_id)
        if folder.exists(): raise ClipHostError('clip_host_asset_exists',409)
        try:
            if (not path.is_absolute() or any(item.is_symlink() for item in (path,*path.parents))
                    or re.fullmatch(r'[0-9a-f]{64}',expected_sha256) is None):
                raise ClipHostError('clip_host_source_changed',400)
            info=path.stat()
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_CLIP_BYTES:
                raise ClipHostError('clip_host_source_changed',400)
            if await hash_file(path)!=expected_sha256: raise ClipHostError('clip_host_source_changed',400)
            created=datetime.now(timezone.utc)
            grant=_Grant(receipt_id=receipt_id,source_sha256=expected_sha256,token=secrets.token_hex(32),
                bytes=info.st_size,created_at=created,expires_at=created+timedelta(hours=24))
            folder.parent.mkdir(parents=True,exist_ok=True,mode=0o700); folder.mkdir(mode=0o700)
            _save(_child(folder,'grant.json'),grant)
            pending=_child(folder,'pending.mp4')
            await copy_verified(path,pending,expected_sha256)
            if pending.stat().st_size!=grant.bytes: raise ClipHostError('clip_host_source_changed',400)
            pending.rename(_child(folder,'clip.mp4'))
            origin=await _start_tunnel()
            _served[grant.token]=receipt_id
            hosted=HostedClip(receipt_id=receipt_id,source_sha256=expected_sha256,
                url=origin+'/clip/'+grant.token+'.mp4',expires_at=grant.expires_at)
            await client.verify(hosted,grant.bytes)
            return hosted
        except BaseException as error:
            if folder.exists():
                try: await await_cleanup(_release_inner(receipt_id))
                except (ClipHostError,OSError): _LOG.warning('Unsubmitted clip retained for local cleanup')
            if isinstance(error,(OSError,ValueError,VideoProjectError)):
                raise ClipHostError('clip_host_source_changed',400) from error
            raise


async def _release_inner(identifier: str) -> None:
    folder=_folder(identifier)
    if not folder.exists(): return
    path=_child(folder,'grant.json')
    with document_lock(path):
        grant=_load(identifier); grant.cleanup_pending=True; _save(path,grant)
        _served.pop(grant.token,None)
        for child in folder.iterdir():
            if child==path: continue
            if child.name not in {'clip.mp4','pending.mp4'} and re.fullmatch(r'\.grant\.json\.[0-9a-f]{32}\.tmp',child.name) is None:
                raise ClipHostError('clip_host_storage_unavailable')
            child.unlink()
        path.unlink(); folder.rmdir()
    if not _served: await _stop_tunnel()


async def release(receipt_id: str) -> None:
    async with _owned(),_operation_lock:
        try: await _release_inner(receipt_id)
        except OSError as error: raise ClipHostError('clip_host_storage_unavailable') from error


async def _cleanup_inner() -> None:
    root=_root()
    if not root.exists(): return
    failed=False
    for child in root.iterdir():
        if _ID.fullmatch(child.name) is None: continue
        try:
            folder=_folder(child.name)
            if not _child(folder,'grant.json').exists() and not any(folder.iterdir()):
                folder.rmdir()
                continue
            grant=_load(child.name)
            terminal=False
            try: terminal=ledger.get(grant.receipt_id).state in {'failed','completed'}
            except OpenRouterError: pass
            if grant.cleanup_pending or terminal or grant.expires_at<=datetime.now(timezone.utc):
                await _release_inner(grant.receipt_id)
        except (ClipHostError,OSError,ValueError): failed=True
    if failed: raise ClipHostError('clip_host_storage_unavailable')


async def cleanup() -> ClipHostStatus:
    async with _owned(),_operation_lock:
        await _cleanup_inner()
        return status()


async def connection() -> ClipHostStatus:
    async with _owned():
        await ensure_ready()
        return status()


async def _recover_worker() -> None:
    global _unresolved
    root=_root(); ownership=_child(root,'worker.json')
    if not ownership.exists(): return
    try:
        if ownership.stat().st_size>4096: raise ValueError('worker_limit')
        identity=WorkerIdentity.model_validate_json(ownership.read_bytes())
        if identity.receipt!='worker-receipt.json': raise ValueError('worker_receipt')
        receipt=_child(root,'worker-receipt.json')
        if not await terminate_verified(WorkerIdentity(pid=identity.pid,token=identity.token,receipt=str(receipt))):
            raise ClipHostError('clip_host_worker_unresolved')
        ownership.unlink(); receipt.unlink(missing_ok=True)
    except (ClipHostError,OSError,ValueError) as error:
        _unresolved=True
        raise ClipHostError('clip_host_worker_unresolved') from error


async def _maintain() -> None:
    while True:
        await asyncio.sleep(900)
        try: await cleanup()
        except ClipHostError: _LOG.warning('Private clips retained for later local cleanup')


async def recover() -> None:
    global _closing,_unresolved,_maintenance
    _closing=False
    if _ACTIVE or _relay is not None: return
    _served.clear(); _unresolved=False
    try:
        await _recover_worker()
        async with _operation_lock: await _cleanup_inner()
    except (ClipHostError,OSError,ValueError):
        _unresolved=True
        _LOG.warning('Clip relay evidence retained; sharing unavailable until recovery')
    if _maintenance is None or _maintenance.done(): _maintenance=asyncio.create_task(_maintain())


def work_busy() -> bool:
    with _REGISTRY_LOCK: return bool(_ACTIVE or _served or _unresolved)


async def shutdown() -> None:
    global _closing,_maintenance
    _closing=True
    with _REGISTRY_LOCK: tasks=set(_ACTIVE)
    if _maintenance is not None: tasks.add(_maintenance)
    current=asyncio.current_task()
    tasks.discard(current)
    for task in tasks:
        if not task.done(): task.cancel()
    await await_cleanup(asyncio.gather(*tasks,return_exceptions=True))
    _maintenance=None
    async with _operation_lock: await await_cleanup(_stop_tunnel())
