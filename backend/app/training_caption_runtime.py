"""Local ACE audio understanding, with bounded excerpts and verified native drain."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import wave
from typing import Literal
import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from .contracts import JsonObject, JsonValue
from .config import MODELS, ACE_STEP_LM_MODEL
from .job_lifecycle import await_cleanup
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .video_media import tool
from .video_process import run_owned_command

CLIENT_TRANSPORT: httpx.AsyncBaseTransport | None = None
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class CaptionError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class _Released(BaseModel):
    task_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


class _NativeResult(_Released):
    status: int = Field(strict=True, ge=0, le=2)
    result: str = Field(max_length=1024 * 1024)


_results = TypeAdapter(list[_NativeResult])


async def request(method: Literal['GET', 'POST'], endpoint: str, payload: JsonObject | None = None) -> JsonValue:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=5, pool=5), trust_env=False,
                                     follow_redirects=False, transport=CLIENT_TRANSPORT) as client:
            async with client.stream(method, MODELS['ace_step'].proxy_target + endpoint, json=payload) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > 2 * 1024 * 1024:
                        raise CaptionError('training_caption_native_invalid', 502)
                    content.extend(chunk)
        value = _json.validate_json(bytes(content))
        if not isinstance(value, dict) or type(value.get('code')) is not int or value.get('code') != 200 or value.get('error'):
            raise CaptionError('training_caption_native_unavailable', 502)
        return value.get('data')
    except (httpx.HTTPError, ValueError) as error:
        raise CaptionError('training_caption_native_unavailable', 502) from error


def require_installed() -> None:
    from .module_catalog import configured_environment, engine_python
    from .module_music_models import music_models_present
    environment = configured_environment()
    root = environment.paths['ace_step']
    if (not (root / 'acestep/api_server.py').is_file() or not engine_python(root, environment.platform).is_file()
            or not music_models_present(environment, 'ace_step')):
        raise CaptionError('training_caption_setup_required', 503)
    tool('ffmpeg')


async def prepare_excerpt(source: Path, output: Path, seconds: int) -> None:
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac'}
    if source.suffix.lower() not in formats or not 6 <= seconds <= 30:
        raise CaptionError('training_caption_source_invalid', 422)
    result = await run_owned_command([tool('ffmpeg'), '-nostdin', '-v', 'error', '-xerror',
        '-protocol_whitelist', 'file,pipe', '-f', formats[source.suffix.lower()], '-i', str(source),
        '-t', str(seconds), '-map', '0:a:0', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 'wav', str(output)],
        timeout=120, max_bytes=1024)
    if result.returncode != 0:
        raise CaptionError('training_caption_source_invalid', 422)
    try:
        with wave.open(str(output), 'rb') as audio:
            if (audio.getnchannels() != 2 or audio.getsampwidth() != 2 or audio.getframerate() != 48000
                    or not .1 <= audio.getnframes() / 48000 <= seconds + .01 or output.stat().st_size > 6 * 1024**2):
                raise CaptionError('training_caption_source_invalid', 422)
    except (OSError, wave.Error, EOFError) as error:
        raise CaptionError('training_caption_source_invalid', 422) from error


class CaptionSession:
    def __init__(self) -> None:
        self.instance: str | None = None
        self.task_id: str | None = None
        self.inflight = False
        self._excerpt: Path | None = None
        self._stop_on_close = False

    def _owned(self) -> bool:
        return self.instance is not None and manager.owned_instance('ace_step') == self.instance

    async def start(self) -> None:
        require_installed()
        if manager.state.models['ace_step'].status != ModelStatus.RUNNING:
            self._stop_on_close = True
            try:
                await manager.switch_to('ace_step')
            finally:
                self.instance = manager.owned_instance('ace_step')
        self.instance = manager.owned_instance('ace_step')
        if self.instance is None:
            raise CaptionError('training_caption_engine_unowned', 503)
        health = await request('GET', '/health')
        if not isinstance(health, dict) or health.get('status') != 'ok' or health.get('service') != 'ACE-Step API':
            raise CaptionError('training_caption_native_invalid', 502)
        if health.get('models_initialized') is True and health.get('llm_initialized') is True:
            return
        if not self._owned():
            raise CaptionError('training_caption_engine_changed')
        self._stop_on_close = True
        self.inflight = True
        initialized = await request('POST', '/v1/init', {'init_llm': True, 'lm_model_path': ACE_STEP_LM_MODEL})
        if (not isinstance(initialized, dict) or initialized.get('message') != 'Model initialization completed'
                or initialized.get('slot') != 1 or initialized.get('llm_initialized') is not True):
            raise CaptionError('training_caption_native_invalid', 502)
        self.inflight = False

    async def _result(self) -> _NativeResult:
        rows = _results.validate_python(await request('POST', '/query_result', {'task_id_list': [self.task_id]}))
        result = next((row for row in rows if row.task_id == self.task_id), None)
        if result is None:
            raise CaptionError('training_caption_native_invalid', 502)
        return result

    def _clean_excerpt(self) -> None:
        if self._excerpt is not None:
            shutil.rmtree(self._excerpt)
            self._excerpt = None

    async def analyze(self, source: Path, seconds: int) -> str:
        self._excerpt = Path(tempfile.mkdtemp(prefix='openfabric-training-caption-'))
        excerpt = self._excerpt / 'excerpt.wav'
        try:
            await prepare_excerpt(source, excerpt, seconds)
            if not self._owned():
                raise CaptionError('training_caption_engine_changed')
            self.task_id, self.inflight = None, True
            released = _Released.model_validate(await request('POST', '/release_task',
                {'full_analysis_only': True, 'src_audio_path': str(excerpt), 'thinking': False,
                 'use_cot_caption': False, 'use_cot_language': False}))
            self.task_id = released.task_id
            async with asyncio.timeout(1800):
                while True:
                    if not self._owned():
                        raise CaptionError('training_caption_engine_changed')
                    result = await self._result()
                    if result.status in (1, 2):
                        self.inflight = False
                        if result.status != 1:
                            raise CaptionError('training_caption_analysis_failed', 502)
                        value = _json.validate_json(result.result)
                        first = value[0] if isinstance(value, list) and len(value) == 1 else None
                        caption = first.get('prompt') if isinstance(first, dict) else None
                        if (not isinstance(first, dict) or first.get('status_message') != 'Full Hardware Analysis Success'
                                or not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 4000):
                            raise CaptionError('training_caption_native_invalid', 502)
                        return caption.strip()
                    await asyncio.sleep(1)
        except (ValidationError, ValueError, TimeoutError) as error:
            raise CaptionError('training_caption_analysis_failed', 502) from error
        finally:
            if self.inflight:
                await await_cleanup(self.drain())
            if not self.inflight:
                self._clean_excerpt()

    async def _stop_instance(self) -> None:
        if not self._owned():
            if not (manager.state.models['ace_step'].status in (ModelStatus.STOPPED, ModelStatus.ERROR)
                    and 'ace_step' not in manager._instances and 'ace_step' not in manager._processes):
                raise CaptionError('training_caption_stop_failed', 503)
        else:
            try:
                await manager.stop_owned('ace_step', self.instance or '')
            except (RuntimeError, ValueError) as error:
                raise CaptionError('training_caption_stop_failed', 503) from error
        self.inflight = False
        self._stop_on_close = False
        self._clean_excerpt()

    async def drain(self) -> None:
        if not self.inflight:
            self._clean_excerpt()
            return
        if not self._owned():
            await self._stop_instance()
            return
        if self.task_id is not None:
            try:
                async with asyncio.timeout(15):
                    await request('POST', '/cancel_task', {'task_id': self.task_id})
                    while True:
                        if (await self._result()).status in (1, 2):
                            self.inflight = False
                            self._clean_excerpt()
                            return
                        await asyncio.sleep(.25)
            except (CaptionError, ValidationError, TimeoutError):
                pass
        await self._stop_instance()

    async def close(self) -> None:
        await self.drain()
        if self._stop_on_close:
            await self._stop_instance()
