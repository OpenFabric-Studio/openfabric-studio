"""Durable chapter read-along composition from immutable accepted narration.

No synthesis, alignment guesses, model downloads or text in FFmpeg expressions.
Interrupted jobs are resumable after owned codec recovery verifies process exit.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import logging
import math
from pathlib import Path
import re
import shutil
import threading
import uuid
import wave
from typing import Annotated, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, ValidationError
from . import audiobooks, audiobook_publish, audiobook_workflows, export_provenance, voice_profiles
from .export_provenance_contracts import ProvenanceComponent
from .job_lifecycle import await_cleanup
from .reading_media_contracts import ReadAlongExport, ReadAlongExportsResponse, ReadAlongRequest, ReadingCue
from .video_projects import atomic_text
from .video_text import _font, font_identity

_LOG = logging.getLogger(__name__)
_ID = re.compile(r'^[0-9a-f]{32}$')
_LOCK = threading.RLock()
_TASKS: dict[str, asyncio.Task[None]] = {}
_STOPPING = False
_FILES = {'movie.mp4', 'captions.srt', 'captions.vtt', 'manifest.json', 'source.wav', 'export.json', 'slides.ffconcat'}


class ReadingMediaError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ChapterSnapshot:
    book_id: str
    chapter_index: int
    revision: int
    title: str
    audio_path: Path
    audio_sha256: str
    duration_ms: int
    cues: list[ReadingCue]
    profile_ids: list[str]
    components: list[ProvenanceComponent]
    text_basis: Literal['original_mapping', 'spoken_fallback'] = 'original_mapping'


class _StoredExport(BaseModel):
    export: ReadAlongExport
    title: str = Field(max_length=200)
    cues: list[ReadingCue] = Field(max_length=20000)
    profile_ids: list[str] = Field(max_length=17)
    components: list[ProvenanceComponent] = Field(max_length=1000)
    cues_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    artifact_hashes: dict[str, Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]] = Field(default_factory=dict, max_length=4)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def directory(identifier: str) -> Path:
    if not _ID.fullmatch(identifier):
        raise ReadingMediaError('reading_export_not_found', 404)
    root = audiobooks.books_root().resolve()
    parent = root / '.readalong'
    path = parent / identifier
    if parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ReadingMediaError('reading_storage_unavailable', 503)
    return path


def artifact(identifier: str, name: str) -> Path:
    if name not in _FILES and not re.fullmatch(r'card-\d{5}\.png', name):
        raise ReadingMediaError('reading_export_not_found', 404)
    root = directory(identifier)
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ReadingMediaError('reading_storage_unavailable', 503)
    return path


def _save(stored: _StoredExport) -> None:
    with _LOCK:
        atomic_text(artifact(stored.export.id, 'export.json'), stored.model_dump_json())


def _load(identifier: str) -> _StoredExport:
    with _LOCK:
        path = artifact(identifier, 'export.json')
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('oversized export')
            stored = _StoredExport.model_validate_json(path.read_bytes())
            if stored.export.id != identifier:
                raise ValueError('identity mismatch')
            return stored
        except (OSError, ValueError, ValidationError) as error:
            raise ReadingMediaError('reading_export_not_found', 404) from error


def require_consent(profile_ids: list[str]) -> None:
    for profile_id in profile_ids:
        profile = voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            raise ReadingMediaError('consent_required', 403)


def _cues_hash(cues: list[ReadingCue]) -> str:
    return hashlib.sha256('\n'.join(cue.model_dump_json() for cue in cues).encode()).hexdigest()


def chapter_snapshot(book_id: str, chapter_index: int, revision: int) -> ChapterSnapshot:
    with audiobooks.publication_lock(book_id), audiobooks._LOCK:
        passages = audiobook_workflows.get_passages(book_id, chapter_index)
        if passages.revision != revision:
            raise ReadingMediaError('reading_source_changed')
        jobs = audiobooks.list_jobs(book_id=book_id)
        job = next((item for item in jobs if item.chapter_index == chapter_index), None)
        if job is None or job.status != 'done' or not passages.passages or any(
            passage.status != 'done' or passage.end_ms <= passage.start_ms for passage in passages.passages):
            raise ReadingMediaError('reading_chapter_not_ready')
        path = audiobooks.chapter_audio_path(book_id, chapter_index)
        if path.is_symlink():
            raise ReadingMediaError('reading_chapter_not_ready')
        try:
            with wave.open(str(path), 'rb') as source:
                if source.getcomptype() != 'NONE' or source.getsampwidth() != 2 or not 1 <= source.getnchannels() <= 2 \
                    or not 8000 <= source.getframerate() <= 192000:
                    raise ReadingMediaError('reading_chapter_not_ready')
                duration_ms = round(source.getnframes() * 1000 / source.getframerate())
        except (OSError, EOFError, wave.Error, ZeroDivisionError) as error:
            raise ReadingMediaError('reading_chapter_not_ready') from error
        if not 1 <= duration_ms <= 7200000 or path.stat().st_size > 1024 * 1024 * 1024:
            raise ReadingMediaError('reading_chapter_too_large')
        profile_ids = list(dict.fromkeys(passage.profile_id for passage in passages.passages))
        require_consent(profile_ids)
        cues: list[ReadingCue] = []
        fallback = False
        components: list[ProvenanceComponent] = []
        for passage in passages.passages:
            if passage.start_ms >= min(duration_ms, passage.end_ms) or cues and passage.start_ms < cues[-1].end_ms:
                raise ReadingMediaError('reading_timing_unavailable')
            # Newly retained mapping is authoritative. Legacy PCM without that
            # mapping uses spoken wording and is explicitly labelled as such.
            display = passage.display_text
            fallback = fallback or display is None
            cues.append(ReadingCue(passage_id=passage.id, start_ms=passage.start_ms,
                end_ms=min(duration_ms, passage.end_ms), display_text=display or passage.text, spoken_text=passage.text))
            audio = audiobook_workflows.passage_audio_path(book_id, passage.id, revision)
            cloud = passage.cloud_provenance
            try:
                rendered = None if passage.render_identity is None else audiobook_workflows.passage_render_snapshot(
                    book_id, chapter_index, passage.id, revision)
            except audiobooks.AudiobookError as error:
                if error.code != 'passage_snapshot_unavailable':
                    raise
                rendered = None
            content: Literal['generated', 'unknown'] = 'unknown' if rendered is None or rendered.engine_identity == 'openfabric-mock-pcm-v1' else 'generated'
            components.append(ProvenanceComponent(role='audio', content_origin=content,
                source_id=f'passage:{passage.id}:take:{passage.render_identity or "legacy"}',
                source_sha256=export_provenance.digest(audio), engine=passage.renderer,
                model_id=cloud.model if cloud else 'gpt-sovits',
                engine_fingerprint=cloud.model_fingerprint if cloud else rendered.engine_identity if rendered else None,
                provider_receipt_id=cloud.receipt_id if cloud else None,
                provider_job_id=cloud.generation_id if cloud else None))
        return ChapterSnapshot(book_id, chapter_index, revision, job.chapter_title,
            path, export_provenance.digest(path), duration_ms, cues, profile_ids, components,
            'spoken_fallback' if fallback else 'original_mapping')


def _current(stored: _StoredExport) -> bool:
    try:
        source = chapter_snapshot(stored.export.book_id, stored.export.chapter_index, stored.export.source_revision)
        return source.audio_sha256 == stored.export.source_sha256 and _cues_hash(source.cues) == stored.cues_sha256
    except (ReadingMediaError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError, export_provenance.ProvenanceError, OSError):
        return False


def get(identifier: str) -> ReadAlongExport:
    return _load(identifier).export


def list_exports(book_id: str, chapter_index: int) -> ReadAlongExportsResponse:
    if not _ID.fullmatch(book_id) or not 0 <= chapter_index <= 99:
        raise ReadingMediaError('reading_export_not_found', 404)
    parent = directory('0' * 32).parent
    exports: list[ReadAlongExport] = []
    if parent.is_dir():
        for path in parent.iterdir():
            if _ID.fullmatch(path.name) and not path.is_symlink():
                try:
                    public = get(path.name)
                    if public.book_id == book_id and public.chapter_index == chapter_index:
                        exports.append(public)
                except ReadingMediaError:
                    _LOG.warning('Skipping invalid read-along export %s', path.name)
    return ReadAlongExportsResponse(exports=sorted(exports, key=lambda item: item.created_at, reverse=True))


def _time(milliseconds: int, separator: str) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}'


def subtitles(cues: list[ReadingCue], format: Literal['srt', 'vtt'], duration_ms: int) -> str:
    lines = ['WEBVTT', ''] if format == 'vtt' else []
    index = 0
    for cue in cues:
        start, end = cue.start_ms, min(cue.end_ms, duration_ms)
        if start >= end:
            continue
        index += 1
        separator = ',' if format == 'srt' else '.'
        # Escape markup and separators so uploaded prose cannot add cue timing
        # or subtitle tags. A blank line always belongs to our formatter.
        text = html.escape(cue.display_text, quote=False).replace('-->', '—›')
        text = '\n'.join(line for line in text.splitlines() if line.strip())
        lines.extend([str(index), f'{_time(start, separator)} --> {_time(end, separator)}', text, ''])
    return '\n'.join(lines) + '\n'


def _card(path: Path, text: str, width: int, height: int, *, generated: bool) -> None:
    with Image.new('RGB', (width, height), '#12131e') as image:
        draw = ImageDraw.Draw(image)
        if text:
            for size in range(32, 13, -2):
                font = _font(size, text)
                missing = font.getmask('\U0010ffff')
                for char in set(text):
                    if not char.isspace():
                        glyph = font.getmask(char)
                        if glyph.size == missing.size and bytes(glyph) == bytes(missing):
                            raise ReadingMediaError('caption_glyph_unavailable', 422)
                lines: list[str] = []
                for paragraph in text.splitlines() or [text]:
                    current = ''
                    for char in paragraph:
                        if draw.textlength(current + char, font=font) > width * .84:
                            lines.append(current)
                            current = ''
                        current += char
                    lines.append(current)
                wrapped = '\n'.join(lines)
                box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=8)
                if box[3] - box[1] <= height * .76:
                    draw.multiline_text((width / 2, height / 2 - (box[3] - box[1]) / 2 - box[1]),
                        wrapped, font=font, fill='#fafaff', anchor='ma', align='center', spacing=8)
                    break
            else:
                raise ReadingMediaError('reading_passage_too_large', 422)
        if generated:
            draw.text((width / 2, height - 28), 'AI-generated narration', font=_font(14),
                      fill='#b1b2cc', anchor='ma')
        image.save(path, 'PNG')


async def _encode(stored: _StoredExport) -> None:
    from .video_media import tool
    root = directory(stored.export.id)
    width, height = (360, 640) if stored.export.aspect == 'portrait' else (640, 360)
    duration = stored.export.duration_ms
    cursor, slide_index, previous_text = 0, 0, ''
    rows = ['ffconcat version 1.0']
    generated = any(component.content_origin in {'generated', 'mixed'} for component in stored.components)
    for cue in stored.cues:
        if cue.start_ms >= duration:
            break
        for text, start, end in ((previous_text, cursor, cue.start_ms),
                                 (cue.display_text, cue.start_ms, min(cue.end_ms, duration))):
            if end <= start:
                continue
            filename = f'card-{slide_index:05}.png'
            _card(artifact(stored.export.id, filename), text, width, height, generated=generated)
            rows.extend([f"file '{filename}'", f'duration {(end - start) / 1000:.6f}'])
            slide_index += 1
        cursor = min(cue.end_ms, duration)
        previous_text = cue.display_text
        # Yield between bounded rasterizations so cancellation and shutdown can
        # run during a long chapter, before an encoder is even spawned.
        await asyncio.sleep(0)
    if cursor < duration:
        filename = f'card-{slide_index:05}.png'
        _card(artifact(stored.export.id, filename), previous_text, width, height, generated=generated)
        rows.extend([f"file '{filename}'", f'duration {(duration - cursor) / 1000:.6f}'])
    if len(rows) < 3:
        raise ReadingMediaError('reading_chapter_not_ready')
    rows.append(next(row for row in reversed(rows) if row.startswith('file ')))
    atomic_text(artifact(stored.export.id, 'slides.ffconcat'), '\n'.join(rows) + '\n')
    result = await audiobook_publish._run_owned([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
        '-safe', '1', '-f', 'concat', '-i', str(artifact(stored.export.id, 'slides.ffconcat')),
        '-i', str(artifact(stored.export.id, 'source.wav')), '-map', '0:v:0', '-map', '1:a:0',
        '-t', f'{duration / 1000:.6f}', '-vf', f'fps=12,tpad=stop_mode=clone:stop_duration={duration / 1000:.6f}',
        '-r', '12', '-c:v', 'libx264', '-preset', 'veryfast',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
        str(artifact(stored.export.id, 'movie.mp4'))], timeout=max(120, min(14400, duration // 100)))
    if result.returncode != 0:
        raise ReadingMediaError('reading_encoder_failed', 503)


async def _validate(stored: _StoredExport) -> None:
    from fractions import Fraction
    from .video_media import Probe, duration, tool
    media = artifact(stored.export.id, 'movie.mp4')
    expected = stored.export.duration_ms / 1000
    size = (360, 640) if stored.export.aspect == 'portrait' else (640, 360)
    result = await audiobook_publish._run_owned([tool('ffprobe'), '-v', 'error', '-show_streams',
        '-show_format', '-of', 'json', str(media)], timeout=30)
    try:
        probe = Probe.model_validate_json(result.stdout)
        video = next(stream for stream in probe.streams if stream.codec_type == 'video')
        audio = next(stream for stream in probe.streams if stream.codec_type == 'audio')
        fps = float(Fraction(video.avg_frame_rate))
        if result.returncode != 0 or not math.isfinite(fps) or abs(fps - 12) > .01 or (video.width, video.height) != size \
            or abs(duration(video.duration) - expected) > .1 or abs(duration(audio.duration) - expected) > .1:
            raise ValueError('media mismatch')
    except (ValueError, ValidationError, ZeroDivisionError, StopIteration) as error:
        raise ReadingMediaError('reading_output_invalid', 503) from error
    decoded = await audiobook_publish._run_owned([tool('ffmpeg'), '-v', 'error', '-xerror',
        '-i', str(media), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], timeout=max(120, min(14400, round(expected * 4))))
    if decoded.returncode != 0:
        raise ReadingMediaError('reading_output_invalid', 503)


def _state(identifier: str, status: Literal['queued', 'running', 'done', 'failed', 'cancelled'], detail: str = '') -> None:
    with _LOCK:
        stored = _load(identifier)
        stored.export.status, stored.export.detail, stored.export.updated_at = status, detail, _stamp()
        if status != 'done':
            stored.export.video_url = stored.export.srt_url = stored.export.vtt_url = stored.export.manifest_url = None
        _save(stored)


async def _run(identifier: str) -> None:
    try:
        _state(identifier, 'running')
        stored = _load(identifier)
        if not _current(stored):
            raise ReadingMediaError('reading_source_changed')
        font_identity()
        await _encode(stored)
        await _validate(stored)
        with audiobooks.publication_lock(stored.export.book_id):
            if not _current(stored):
                raise ReadingMediaError('reading_source_changed')
            if export_provenance.digest(artifact(identifier, 'source.wav')) != stored.export.source_sha256:
                raise ReadingMediaError('reading_source_changed')
            require_consent(stored.profile_ids)
            for format in ('srt', 'vtt'):
                atomic_text(artifact(identifier, f'captions.{format}'), subtitles(stored.cues, format, stored.export.duration_ms))
            # No chapter text or private paths in the workflow manifest.
            from .contracts import JsonObject
            manifest: JsonObject = {'schema_version': 1, 'source_sha256': stored.export.source_sha256,
                'artifact_sha256': export_provenance.digest(artifact(identifier, 'movie.mp4')),
                'source_revision': stored.export.source_revision, 'timing': 'passage',
                'text_basis': stored.export.text_basis, 'font_identity': font_identity(),
                'components': [component.model_dump(mode='json') for component in stored.components]}
            import json
            atomic_text(artifact(identifier, 'manifest.json'), json.dumps(manifest, ensure_ascii=False, indent=2))
            stored.artifact_hashes = {name: export_provenance.digest(artifact(identifier, name))
                for name in ('movie.mp4', 'captions.srt', 'captions.vtt', 'manifest.json')}
            stored.export.status, stored.export.detail, stored.export.updated_at = 'done', '', _stamp()
            base = f'/api/reading-media/exports/{identifier}'
            stored.export.video_url, stored.export.srt_url = f'{base}/movie.mp4', f'{base}/captions.srt'
            stored.export.vtt_url, stored.export.manifest_url = f'{base}/captions.vtt', f'{base}/manifest.json'
            _save(stored)
    except asyncio.CancelledError:
        _state(identifier, 'cancelled', 'reading_cancelled')
    except (ReadingMediaError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as error:
        _state(identifier, 'failed', error.code)
    except Exception:
        _LOG.exception('Read-along export failed: %s', identifier)
        _state(identifier, 'failed', 'reading_export_failed')
    finally:
        try:
            if get(identifier).status != 'done' and not audiobook_publish.cleanup_pending():
                for name in ('movie.mp4', 'captions.srt', 'captions.vtt', 'manifest.json'):
                    artifact(identifier, name).unlink(missing_ok=True)
        except (OSError, ReadingMediaError):
            _LOG.warning('Read-along partial artifacts retained after storage failure: %s', identifier, exc_info=True)
        finally:
            if _TASKS.get(identifier) is asyncio.current_task():
                _TASKS.pop(identifier, None)


async def create(book_id: str, chapter_index: int, body: ReadAlongRequest) -> ReadAlongExport:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        require_setup_idle()
        if _STOPPING or audiobook_publish.cleanup_pending():
            raise ReadingMediaError('reading_export_busy', 503)
        if len(_TASKS) >= 2:
            raise ReadingMediaError('reading_export_busy', 409)
        with audiobooks.publication_lock(book_id):
            snapshot = chapter_snapshot(book_id, chapter_index, body.revision)
            identifier = uuid.uuid4().hex
            root = directory(identifier)
            root.mkdir(parents=True)
            try:
                shutil.copyfile(snapshot.audio_path, artifact(identifier, 'source.wav'))
                if export_provenance.digest(artifact(identifier, 'source.wav')) != snapshot.audio_sha256:
                    raise ReadingMediaError('reading_source_changed')
                duration = snapshot.duration_ms if body.preview_seconds is None else min(snapshot.duration_ms, body.preview_seconds * 1000)
                stamp = _stamp()
                public = ReadAlongExport(id=identifier, book_id=book_id, chapter_index=chapter_index,
                    source_revision=body.revision, source_sha256=snapshot.audio_sha256, status='queued',
                    aspect=body.aspect, preview_seconds=body.preview_seconds, duration_ms=duration,
                    text_basis=snapshot.text_basis, created_at=stamp, updated_at=stamp)
                _save(_StoredExport(export=public, title=snapshot.title, cues=snapshot.cues,
                    profile_ids=snapshot.profile_ids, components=snapshot.components, cues_sha256=_cues_hash(snapshot.cues)))
            except BaseException:
                shutil.rmtree(root)
                raise
        _TASKS[identifier] = asyncio.create_task(_run(identifier))
        return public


async def wait(identifier: str) -> None:
    task = _TASKS.get(identifier)
    if task is not None:
        await asyncio.shield(task)


async def cancel(identifier: str) -> ReadAlongExport:
    task = _TASKS.get(identifier)
    if task is not None:
        task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        _TASKS.pop(identifier, None)
        if get(identifier).status in {'queued', 'running'}:
            _state(identifier, 'cancelled', 'reading_cancelled')
    return get(identifier)


def work_busy() -> bool:
    return bool(_TASKS)


async def resume(identifier: str) -> ReadAlongExport:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        stored = _load(identifier)
        if stored.export.status not in {'failed', 'cancelled'} or identifier in _TASKS:
            raise ReadingMediaError('reading_export_busy')
        require_setup_idle()
        if _STOPPING or audiobook_publish.cleanup_pending() or len(_TASKS) >= 2:
            raise ReadingMediaError('reading_export_busy', 503)
        if not _current(stored) or export_provenance.digest(artifact(identifier, 'source.wav')) != stored.export.source_sha256:
            raise ReadingMediaError('reading_source_changed')
        _state(identifier, 'queued')
        _TASKS[identifier] = asyncio.create_task(_run(identifier))
    return get(identifier)


async def start() -> None:
    global _STOPPING
    _STOPPING = False
    parent = directory('0' * 32).parent
    if parent.is_dir():
        for path in parent.iterdir():
            if _ID.fullmatch(path.name) and not path.is_symlink():
                try:
                    if get(path.name).status in {'queued', 'running'}:
                        _state(path.name, 'failed', 'reading_interrupted')
                except ReadingMediaError:
                    _LOG.warning('Read-along recovery skipped invalid record %s', path.name)


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    tasks = tuple(_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))


def serve(identifier: str, name: str) -> Path:
    stored = _load(identifier)
    if name not in {'movie.mp4', 'captions.srt', 'captions.vtt', 'manifest.json'} or stored.export.status != 'done':
        raise ReadingMediaError('reading_export_not_found', 404)
    require_consent(stored.profile_ids)
    path = artifact(identifier, name)
    if not path.is_file():
        raise ReadingMediaError('reading_export_not_found', 404)
    try:
        if export_provenance.digest(path) != stored.artifact_hashes.get(name):
            raise ReadingMediaError('reading_artifact_changed')
    except (OSError, export_provenance.ProvenanceError) as error:
        raise ReadingMediaError('reading_export_not_found', 404) from error
    return path
