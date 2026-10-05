"""Fixed-origin, bounded OpenRouter media calls; there are no paid retries."""
from __future__ import annotations
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from collections.abc import AsyncIterator, Callable
from typing import Literal
import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from . import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings, openrouter_credentials as credentials
from .contracts import JsonObject
from .job_lifecycle import await_cleanup
from .openrouter_contracts import OpenRouterCatalog, OpenRouterConnection, OpenRouterMusicRequest, OpenRouterQuoteRequest, OpenRouterSpeechRequest, OpenRouterVideoRequest, OpenRouterVideoJob, Usd
from .openrouter_errors import OpenRouterError

_ORIGIN = 'https://openrouter.ai'
_BASE = _ORIGIN+'/api/v1'
_JSON_LIMIT = 4*1024*1024
_AUDIO_LIMIT = 64*1024*1024
_VIDEO_LIMIT = 512*1024*1024
_SSE_LIMIT = 192*1024*1024
_ACTIVE: set[asyncio.Task[object]] = set()
_REGISTRY_LOCK = threading.RLock()
_STOPPING = False
_json: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

@dataclass(frozen=True)
class AudioResult:
    data: bytes
    format: Literal['mp3','wav']
    generation_id: str | None = None
    actual_cost_usd: float | None = None
    transcript: str = ''

@dataclass(frozen=True)
class VideoDownload:
    bytes: int
    sha256: str

class _Wire(BaseModel):
    model_config=ConfigDict(extra='ignore',allow_inf_nan=False)
class _Usage(_Wire):
    cost: Usd | None = None
class _Remote(_Wire):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,160}$')
    status: Literal['pending','queued','in_progress','processing','completed','failed','cancelled']
    polling_url: str | None = Field(default=None,max_length=2000)
    unsigned_urls: list[str] = Field(default_factory=list,max_length=4)
    progress: float | None = Field(default=None,ge=0,le=100)
    usage: _Usage | None = None
class _KeyData(_Wire):
    limit: Usd | None = None
    limit_remaining: Usd | None = None
    usage: Usd = 0
    is_free_tier: bool = False
class _Key(_Wire):
    data: _KeyData
class _AudioDelta(_Wire):
    data: str = Field(default='',max_length=2*1024*1024)
    transcript: str = Field(default='',max_length=16000)
class _Delta(_Wire):
    audio: _AudioDelta | None = None
class _Choice(_Wire):
    index: int = Field(default=0,ge=0,le=3)
    delta: _Delta = Field(default_factory=_Delta)
    finish_reason: str | None = Field(default=None,max_length=40)
class _Chunk(_Wire):
    id: str | None = Field(default=None,max_length=160,pattern=r'^[A-Za-z0-9_-]+$')
    choices: list[_Choice] = Field(default_factory=list,max_length=4)
    usage: _Usage | None = None
    error: JsonObject | None = None


def _remote_id(value: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9_-]{1,160}',value) is None: raise OpenRouterError('provider_response_invalid')
    return value

def _remote(value: JsonObject) -> OpenRouterVideoJob:
    try:
        wire=_Remote.model_validate(value)
        if wire.polling_url not in (None,f'{_BASE}/videos/{wire.id}'):
            raise ValueError()
        for index,url in enumerate(wire.unsigned_urls):
            if url not in (f'{_BASE}/videos/{wire.id}/content?index={index}',f'{_BASE}/videos/{wire.id}/content' if index==0 else ''):
                raise ValueError()
        status: Literal['pending','queued','processing','completed','failed','cancelled']
        status='processing' if wire.status=='in_progress' else wire.status
        return OpenRouterVideoJob(id=wire.id,status=status,progress=wire.progress,actual_cost_usd=wire.usage.cost if wire.usage else None)
    except (ValidationError,ValueError) as error: raise OpenRouterError('provider_response_invalid') from error

def _http_error(status: int) -> OpenRouterError:
    code={401:'credential_rejected',402:'provider_credit_limit',403:'provider_access_denied',429:'provider_rate_limited'}.get(status,'provider_request_failed')
    return OpenRouterError(code,409 if status in {402,429} else 503)

class OpenRouterClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None=None) -> None:
        self.transport=transport

    @asynccontextmanager
    async def _session(self, *, authenticated: bool) -> AsyncIterator[httpx.AsyncClient]:
        if _STOPPING: raise OpenRouterError('provider_stopping')
        headers={'X-Title':'OpenFabric Studio'}
        if authenticated:
            key,_source=credentials.resolve()
            if key is None: raise OpenRouterError('credential_required',409)
            # A configured environment key is also an untrusted header boundary.
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,512}',key): raise OpenRouterError('credential_invalid',409)
            headers['Authorization']='Bearer '+key
        task: asyncio.Task[object] | None=asyncio.current_task()
        if task is None: raise OpenRouterError('provider_stopping')
        with _REGISTRY_LOCK: _ACTIVE.add(task)
        client=httpx.AsyncClient(base_url=_BASE,headers=headers,follow_redirects=False,trust_env=False,transport=self.transport,timeout=httpx.Timeout(120,connect=10,write=30,pool=10))
        try:
            yield client
        finally:
            try: await await_cleanup(client.aclose())
            finally:
                with _REGISTRY_LOCK: _ACTIVE.discard(task)

    async def _json_request(self, method: str, path: str, *, payload: JsonObject | None=None, authenticated: bool=True) -> JsonObject:
        try:
            async with asyncio.timeout(180), self._session(authenticated=authenticated) as client, client.stream(method,_BASE+path,json=payload) as response:
                if not 200<=response.status_code<300: raise _http_error(response.status_code)
                data=await self._read(response,_JSON_LIMIT)
                return _json.validate_json(data)
        except OpenRouterError: raise
        except (httpx.HTTPError,TimeoutError) as error: raise OpenRouterError('provider_unavailable') from error
        except (ValidationError,ValueError) as error: raise OpenRouterError('provider_response_invalid') from error

    async def _read(self, response: httpx.Response, limit: int) -> bytes:
        data=bytearray()
        async for chunk in response.aiter_bytes(65536):
            if len(data)+len(chunk)>limit: raise OpenRouterError('provider_output_too_large')
            data.extend(chunk)
        if not data: raise OpenRouterError('provider_response_invalid')
        return bytes(data)

    async def connection(self) -> OpenRouterConnection:
        try: key=_Key.model_validate(await self._json_request('GET','/key'))
        except ValidationError as error: raise OpenRouterError('provider_response_invalid') from error
        return OpenRouterConnection(connected=True,limit_usd=key.data.limit,limit_remaining_usd=key.data.limit_remaining,usage_usd=key.data.usage,is_free_tier=key.data.is_free_tier,checked_at=catalog.now())

    async def refresh_catalog(self) -> OpenRouterCatalog:
        try:
            video=catalog.normalize_video(await self._json_request('GET','/videos/models',authenticated=False))
            speech=catalog.normalize_audio(await self._json_request('GET','/models?output_modalities=speech',authenticated=False),'speech')
            music=catalog.normalize_audio(await self._json_request('GET','/models?output_modalities=audio',authenticated=False),'music')
            reviewed=[]
            for model in speech:
                endpoints=await self._json_request('GET',f'/models/{model.id}/endpoints',authenticated=False)
                reviewed.append(catalog.apply_endpoints(model,endpoints))
            return catalog.publish([*video,*reviewed,*music])
        except (ValidationError,ValueError,KeyError) as error: raise OpenRouterError('catalog_invalid') from error

    def _begin(self, receipt_id: str, quote_id: str, body: OpenRouterQuoteRequest, before_submit: Callable[[], None] | None = None) -> None:
        if _STOPPING: raise OpenRouterError('provider_stopping')
        if not settings.load().enabled: raise OpenRouterError('provider_disabled',409)
        quote=catalog.validate_quote(quote_id,body)
        receipt=ledger.get(receipt_id)
        if receipt.model_id!=quote.model_id or receipt.model_fingerprint!=quote.model_fingerprint or receipt.kind!=quote.kind:
            raise OpenRouterError('quote_mismatch',409)
        # Resolve before intent becomes ambiguous, but never persist this value.
        key,_source=credentials.resolve()
        if key is None: raise OpenRouterError('credential_required',409)
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,512}',key): raise OpenRouterError('credential_invalid',409)
        if before_submit is not None: before_submit()
        ledger.begin_submit(receipt_id,quote_id,quote.request_fingerprint)
        catalog.consume_quote(quote_id)

    def _failed(self, receipt_id: str, error: BaseException) -> OpenRouterError:
        # Only explicit HTTP rejection is a confirmed failure. Invalid bodies,
        # interrupted streams and disconnects can follow paid acceptance.
        certain=isinstance(error,OpenRouterError) and error.code in {'credential_rejected','provider_credit_limit','provider_access_denied','provider_rate_limited'}
        code=error.code if certain and isinstance(error,OpenRouterError) else 'submission_unknown'
        try: ledger.update(receipt_id,state='failed' if certain else 'submission_unknown',error_code=code)
        except OpenRouterError: pass
        return OpenRouterError(code,409 if certain else 503)

    async def submit_video(self, receipt_id: str, body: OpenRouterVideoRequest, quote_id: str) -> OpenRouterVideoJob:
        self._begin(receipt_id,quote_id,catalog.video_parameters(body))
        payload=_json.validate_json(body.model_dump_json(exclude={'reference_image'},exclude_none=True))
        if body.reference_image:
            payload['frame_images']=[{'type':'image_url','image_url':{'url':body.reference_image},'frame_type':'first_frame'}]
        try:
            result=_remote(await self._json_request('POST','/videos',payload=payload))
            ledger.update(receipt_id,state='submitted',remote_id=result.id,actual_cost_usd=result.actual_cost_usd)
            return result
        except asyncio.CancelledError as error:
            self._failed(receipt_id,error)
            raise
        except (OpenRouterError,OSError,ValueError) as error: raise self._failed(receipt_id,error) from error

    async def poll_video(self, remote_id: str) -> OpenRouterVideoJob:
        identifier=_remote_id(remote_id)
        result=_remote(await self._json_request('GET',f'/videos/{identifier}'))
        if result.id!=identifier: raise OpenRouterError('provider_response_invalid')
        return result

    async def download_video(self, remote_id: str, dest: Path) -> VideoDownload:
        identifier=_remote_id(remote_id)
        if dest.is_symlink() or dest.exists(): raise OpenRouterError('provider_output_exists',409)
        created=False
        try:
            async with asyncio.timeout(300), self._session(authenticated=True) as client, client.stream('GET',f'{_BASE}/videos/{identifier}/content?index=0') as response:
                if not 200<=response.status_code<300: raise _http_error(response.status_code)
                if response.headers.get('content-type','').split(';')[0] not in {'video/mp4','application/octet-stream'}: raise OpenRouterError('provider_response_invalid')
                count=0; checksum=hashlib.sha256()
                with dest.open('xb') as handle:
                    created=True
                    async for chunk in response.aiter_bytes(65536):
                        count+=len(chunk)
                        if count>_VIDEO_LIMIT: raise OpenRouterError('provider_output_too_large')
                        handle.write(chunk); checksum.update(chunk)
                    if not count: raise OpenRouterError('provider_response_invalid')
                    handle.flush(); os.fsync(handle.fileno())
                return VideoDownload(bytes=count,sha256=checksum.hexdigest())
        except (httpx.HTTPError,TimeoutError) as error: raise OpenRouterError('provider_unavailable') from error
        except OSError as error: raise OpenRouterError('provider_storage_unavailable') from error
        finally:
            # Domain validates the uniquely owned staging file before publication.
            # Failed/canceled downloads must leave no partial media.
            import sys
            if created and sys.exc_info()[0] is not None:
                try: dest.unlink(missing_ok=True)
                except OSError: pass

    async def speech(self, receipt_id: str, body: OpenRouterSpeechRequest, quote_id: str, *, before_submit: Callable[[], None] | None = None) -> AudioResult:
        self._begin(receipt_id,quote_id,catalog.speech_parameters(body),before_submit)
        payload: JsonObject={'model':body.model,'input':body.input,'response_format':'mp3'}
        if body.voice is not None: payload['voice']=body.voice
        if body.reference_audio is not None:
            parts: list[JsonObject]=[{'type':'input_audio','input_audio':{'data':body.reference_audio}}]
            if body.reference_transcript is not None: parts.append({'type':'text','text':body.reference_transcript})
            payload['input_references']=list(parts)
        try:
            async with asyncio.timeout(300), self._session(authenticated=True) as client, client.stream('POST',_BASE+'/audio/speech',json=payload) as response:
                if not 200<=response.status_code<300: raise _http_error(response.status_code)
                if response.headers.get('content-type','').split(';')[0]!='audio/mpeg': raise OpenRouterError('provider_response_invalid')
                data=await self._read(response,_AUDIO_LIMIT)
                generation=response.headers.get('x-generation-id')
                if generation is not None: _remote_id(generation)
                ledger.update(receipt_id,state='completed',remote_id=generation)
                return AudioResult(data=data,format='mp3',generation_id=generation)
        except asyncio.CancelledError as error:
            self._failed(receipt_id,error); raise
        except (httpx.HTTPError,TimeoutError,OpenRouterError,ValueError) as error: raise self._failed(receipt_id,error) from error

    async def complete_music(self, receipt_id: str, body: OpenRouterMusicRequest, quote_id: str) -> AudioResult:
        self._begin(receipt_id,quote_id,catalog.music_parameters(body))
        payload: JsonObject={'model':body.model,'messages':[{'role':'user','content':body.prompt}],
            'stream':True,'modalities':['text','audio'],'audio':{'format':'wav'}}
        if body.seed is not None: payload['seed']=body.seed
        try:
            async with asyncio.timeout(600), self._session(authenticated=True) as client, client.stream('POST',_BASE+'/chat/completions',json=payload) as response:
                if not 200<=response.status_code<300: raise _http_error(response.status_code)
                if response.headers.get('content-type','').split(';')[0]!='text/event-stream': raise OpenRouterError('provider_response_invalid')
                result=await self._music_stream(response)
                ledger.update(receipt_id,state='completed',remote_id=result.generation_id,actual_cost_usd=result.actual_cost_usd)
                return result
        except asyncio.CancelledError as error:
            self._failed(receipt_id,error); raise
        except (httpx.HTTPError,TimeoutError,OpenRouterError,ValueError) as error: raise self._failed(receipt_id,error) from error

    async def _music_stream(self, response: httpx.Response) -> AudioResult:
        buffer=b''; data=bytearray(); transcript=''; generation: str | None=None; cost: float | None=None; done=False; received=0
        async for incoming in response.aiter_bytes(65536):
            received+=len(incoming)
            if received>_SSE_LIMIT: raise OpenRouterError('provider_output_too_large')
            buffer+=incoming
            while b'\n' in buffer:
                line,buffer=buffer.split(b'\n',1); line=line.rstrip(b'\r')
                if len(line)>2*1024*1024: raise OpenRouterError('provider_output_too_large')
                if not line.startswith(b'data:'): continue
                content=line[5:].strip()
                if content==b'[DONE]': done=True; continue
                if done: raise OpenRouterError('provider_response_invalid')
                try: chunk=_Chunk.model_validate_json(content)
                except ValidationError as error: raise OpenRouterError('provider_response_invalid') from error
                if chunk.error or any(choice.finish_reason=='error' for choice in chunk.choices): raise OpenRouterError('provider_request_failed')
                if chunk.id:
                    if generation is not None and generation!=chunk.id: raise OpenRouterError('provider_response_invalid')
                    generation=chunk.id
                if chunk.usage and chunk.usage.cost is not None: cost=chunk.usage.cost
                for choice in chunk.choices:
                    if choice.index!=0: raise OpenRouterError('provider_response_invalid')
                    audio=choice.delta.audio
                    if audio is None: continue
                    try: decoded=base64.b64decode(audio.data,validate=True)
                    except binascii.Error as error: raise OpenRouterError('provider_response_invalid') from error
                    if len(data)+len(decoded)>_AUDIO_LIMIT or len(transcript)+len(audio.transcript)>16000: raise OpenRouterError('provider_output_too_large')
                    data.extend(decoded); transcript+=audio.transcript
            if len(buffer)>2*1024*1024: raise OpenRouterError('provider_output_too_large')
        if buffer.strip() or not done or not data: raise OpenRouterError('provider_response_invalid')
        return AudioResult(data=bytes(data),format='wav',generation_id=generation,actual_cost_usd=cost,transcript=transcript)


def work_busy() -> bool:
    with _REGISTRY_LOCK: return bool(_ACTIVE)

def start() -> None:
    global _STOPPING
    ledger.recover()
    _STOPPING=False

async def _cancel_and_drain(task: asyncio.Task[object]) -> None:
    # This coroutine always executes on the HTTP owner's loop, including
    # asyncio.run loops inside synchronous speech synthesis worker threads.
    if not task.done() and not task.cancelling(): task.cancel()
    await asyncio.gather(task,return_exceptions=True)

async def shutdown() -> None:
    global _STOPPING
    _STOPPING=True
    current=asyncio.current_task(); own_loop=asyncio.get_running_loop()
    with _REGISTRY_LOCK: tasks=tuple(task for task in _ACTIVE if task is not current)
    waits: list[asyncio.Future[None] | asyncio.Task[None]]=[]
    for task in tasks:
        if task.done(): continue
        loop=task.get_loop()
        if loop is own_loop:
            waits.append(asyncio.create_task(_cancel_and_drain(task)))
        else:
            if loop.is_closed(): raise OpenRouterError('provider_worker_unverified')
            future=asyncio.run_coroutine_threadsafe(_cancel_and_drain(task),loop)
            waits.append(asyncio.wrap_future(future))
    if waits: await await_cleanup(asyncio.gather(*waits,return_exceptions=True))
