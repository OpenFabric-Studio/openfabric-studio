"""Optional SheetSage adapter using verified native specs and owned requests."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import soundfile as sf

from . import db, native_yue
from .config import SHEETSAGE_MODEL_PATH, yue2_specs
from .contracts import JsonObject
from .native_artifacts import score_text
from .reference_contracts import ReferenceToolCapability
from .job_lifecycle import await_cleanup, kill_process_tree
from .video_media import tool, VideoMediaError
from .video_process import spawn_owned, read_owned_output


def capability() -> ReferenceToolCapability:
    available = SHEETSAGE_MODEL_PATH.is_file() and native_yue.engine_running()
    if available:
        try:
            tool('ffmpeg')
        except VideoMediaError:
            return ReferenceToolCapability(available=False, reason='ffmpeg_missing', setup_hint='Install FFmpeg for lossless WAV preparation.')
    return ReferenceToolCapability(available=available, reason=None if available else 'melody_unavailable',
        setup_hint='Install the SheetSage2 model with the YuE model manager and start YuE.')


async def prepare_reference_melody(source: Path, *, workspace: Path,
        on_proc: Callable[[asyncio.subprocess.Process | None], None],
        run_tool: Callable[[list[str], Path, float], Awaitable[bytes]] | None = None) -> str:
    if not capability().available:
        raise RuntimeError('melody_unavailable')
    root = db.FILES_DIR.resolve().parent
    if source.resolve() != source or not source.is_relative_to(root) or not source.is_file() or not 0 < source.stat().st_size <= 512 * 1024**2:
        raise ValueError('source_missing')
    if workspace.resolve() != workspace or not workspace.is_relative_to(root) or not workspace.is_dir():
        raise ValueError('source_missing')
    wav = workspace / 'melody-input.wav'
    if wav.exists() or wav.is_symlink():
        raise ValueError('source_missing')
    proc: asyncio.subprocess.Process | None = None
    try:
        # app/cli/request.cpp uses read_wav_f32. Keep the source container
        # unchanged and prepare bounded floating-point PCM for that reader.
        command = [tool('ffmpeg'), '-v', 'error', '-nostdin', '-y',
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,flac,ogg,mov,matroska,webm,aac',
            '-i', str(source), '-map', '0:a:0', '-vn', '-map_metadata', '-1',
            '-t', '601', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_f32le',
            '-fs', str(128 * 1024**2), str(wav)]
        if run_tool is not None:
            await run_tool(command, workspace, 90)
        else:
            proc = await spawn_owned(command, receipt_path=workspace / 'melody-worker.json', stdout=asyncio.subprocess.PIPE)
            on_proc(proc)
            await read_owned_output(proc, max_bytes=256 * 1024, timeout=90)
            if proc.returncode != 0:
                raise ValueError('invalid_audio')
        info = sf.info(str(wav))
        if info.frames <= 0 or info.duration > 600.01 or info.format not in ('WAV', 'WAVEX'):
            raise ValueError('invalid_audio')
        await await_cleanup(kill_process_tree(proc))
        if proc is not None and proc.stdin is not None:
            proc.stdin.close()
        proc = None
        on_proc(None)
        return await _extract(wav)
    finally:
        await await_cleanup(kill_process_tree(proc))
        if proc is not None and proc.stdin is not None:
            proc.stdin.close()
        on_proc(None)
        wav.unlink(missing_ok=True)


async def _extract(source: Path) -> str:
    async with native_yue.owned_session() as session, asyncio.timeout(600):
        # This is an explicit, bounded model switch; max-loaded-models=1 keeps
        # unused generators from retaining accelerator memory.
        spec: JsonObject = {key: value for key, value in yue2_specs()['sheetsage2'].items()}
        await session.post('/v1/models/load', {**spec, 'load_options': {}, 'session_options': {}})
        result = await session.post('/v1/tasks/run', {'model': 'sheetsage2', 'request': {
            'audio': str(source).replace('\\', '/'), 'options': {'max_tokens': 5120}}})
    score = score_text(result)
    if not score:
        raise ValueError('invalid_abc')
    return score
