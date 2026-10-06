"""Bounded dialogue reels copy reviewed cast PCM; video inference stays silent."""
from __future__ import annotations

import array
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import math
import logging
import os
import shutil
import sys
import uuid
import wave
from pathlib import Path
from typing import Literal

from . import audiobooks, audiobook_workflows, video_projects as store, voice_profiles
from .video_characters import require_voice
from .video_media import VideoMediaError
from .video_contracts import CreateDialogueReelRequest, RefreshDialogueCueRequest, VideoDialogueCue, VideoExportSettings, VideoOverlay, VideoProject, VideoProjectSettings, VideoProjectShot, VideoSpeechClip, VideoProjectJob


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CueCopy:
    path: Path
    parameters: tuple[int, int, int]
    frames: int
    seconds: int


@dataclass(frozen=True)
class _Work:
    owner_id: str
    task: asyncio.Task[VideoProject]


_tasks: dict[str, _Work] = {}


def work_busy() -> bool:
    return bool(_tasks)


def _cancel(task: asyncio.Task[VideoProject]) -> None:
    if task is not asyncio.current_task() and not task.done() and not task.cancelling():
        task.cancel()


def request_cancel(project_id: str) -> tuple[asyncio.Task[VideoProject], ...]:
    tasks = tuple(work.task for work in _tasks.values() if work.owner_id == project_id)
    for task in tasks:
        _cancel(task)
    return tasks


def request_shutdown() -> None:
    for work in tuple(_tasks.values()):
        _cancel(work.task)


async def shutdown() -> None:
    from .job_lifecycle import await_cleanup
    request_shutdown()
    pending = tuple(work.task for work in _tasks.values() if work.task is not asyncio.current_task())
    if pending:
        await await_cleanup(asyncio.gather(*pending, return_exceptions=True))


async def _track(owner_id: str, stage_id: str, operation: Callable[[], Awaitable[VideoProject]]) -> VideoProject:
    from .resource_admission import admission_lock, require_setup_idle
    from .job_lifecycle import await_cleanup
    async def run() -> VideoProject:
        return await operation()
    async with admission_lock:
        require_setup_idle()
        task = asyncio.create_task(run())
        _tasks[stage_id] = _Work(owner_id, task)
    try:
        # Browser teardown does not abandon a completed local copy. App shutdown
        # cancels the registered task itself and drains its owned subprocesses.
        return await await_cleanup(task)
    finally:
        if task.done() and _tasks.get(stage_id) == _Work(owner_id, task):
            _tasks.pop(stage_id, None)


def _discard_stage(project_id: str) -> None:
    root = store.project_dir(project_id)
    if (root / 'project.json').exists():
        from . import video_render
        try:
            if store.load(project_id).worker is not None:
                video_render._unverified.add(project_id)
                logger.warning('Retaining dialogue staging with unverified worker %s', project_id)
                return
        except store.VideoProjectError:
            video_render._unverified.add(project_id)
            return
    _cleanup(root)


async def recover() -> None:
    from . import video_render
    from .video_process import terminate_verified
    root = store.projects_root()
    if not root.is_dir():
        return
    for path in tuple(root.iterdir()):
        if len(path.name) != 32 or any(char not in '0123456789abcdef' for char in path.name):
            continue
        try:
            document = store.load(path.name)
        except store.VideoProjectError:
            continue
        if not document.preparing or path.name in _tasks:
            continue
        if document.worker is not None:
            identity = document.worker.model_copy(update={'receipt': str(store.artifact(path.name, document.worker.receipt))})
            if not await terminate_verified(identity):
                video_render._unverified.add(path.name)
                continue
        video_render._unverified.discard(path.name)
        _cleanup(path)


def _cleanup(root: Path) -> None:
    try:
        shutil.rmtree(root)
    except OSError:
        # Unpublished staging has no project.json and cannot be served/listed.
        # Preserve the committed result (or primary typed error), and retain
        # the owned directory for operator cleanup rather than hiding failure.
        logger.warning("Could not remove unpublished dialogue staging %s", root, exc_info=True)


def require_consent(project: VideoProject) -> None:
    for profile_id in {cue.profile_id for cue in project.dialogue_cues}:
        require_voice(profile_id)


def _peaks(pcm: bytes) -> list[float]:
    values = array.array('h')
    values.frombytes(pcm)
    if sys.byteorder != 'little':
        values.byteswap()
    step = max(1, math.ceil(len(values) / 160))
    return [max(abs(sample) for sample in values[index:index+step]) / 32768 for index in range(0, len(values), step)]


async def _prepare(body: CreateDialogueReelRequest, project_id: str, *, target: tuple[int, int, int] | None = None) -> store.StoredVideoProject:
    root = store.project_dir(project_id)
    stamp = store.now()
    shots: list[VideoProjectShot] = []
    cues: list[VideoDialogueCue] = []
    overlays: list[VideoOverlay] = []
    copies: list[_CueCopy] = []
    cursor = 0
    speech_id = uuid.uuid4().hex
    speech_path = f'speech/{speech_id}.wav'
    output = store.artifact(project_id, speech_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Completed source paths are immutable. The book publication lock and
    # catalog lock serialize selection snapshots with chapter repair/edits.
    with audiobooks.publication_lock(body.book_id), audiobooks._LOCK:
        source = audiobook_workflows.get_passages(body.book_id, body.chapter_index)
        if body.revision != source.revision:
            raise store.VideoProjectError('passage_changed')
        available = {passage.id: passage for passage in source.passages}
        for index, selection in enumerate(body.selections):
            passage = available.get(selection.passage_id)
            if passage is None or passage.status != 'done' or passage.render_identity is None:
                raise store.VideoProjectError('passage_not_ready')
            require_voice(passage.profile_id)
            path = audiobook_workflows.passage_audio_path(body.book_id, passage.id, body.revision)
            if path.is_symlink() or not path.is_file():
                raise store.VideoProjectError('passage_not_ready')
            with wave.open(str(path), 'rb') as original:
                actual = (original.getnchannels(), original.getsampwidth(), original.getframerate())
                channels, width, rate = actual
                if original.getcomptype() != 'NONE' or width != 2 or not 1 <= channels <= 2 or not 8000 <= rate <= 192000:
                    raise store.VideoProjectError('audio_format_mismatch')
                source_duration_ms = round(original.getnframes() * 1000 / rate)
                start = round(selection.clip_start_ms * rate / 1000)
                end = original.getnframes() if selection.clip_end_ms is None else round(selection.clip_end_ms * rate / 1000)
                frames = end - start
                if start < 0 or end > original.getnframes() or frames < rate / 5 or frames > rate * 6:
                    raise store.VideoProjectError('dialogue_clip_bounds')
                if (start != 0 or end != original.getnframes()) and selection.caption is None:
                    raise store.VideoProjectError('dialogue_clip_caption_required')
                original.setpos(start)
                pcm = original.readframes(frames)
                if len(pcm) != frames * channels * width:
                    raise store.VideoProjectError('passage_not_ready')
            seconds: Literal[2, 4, 6] = 2 if frames <= 2 * rate else 4 if frames <= 4 * rate else 6
            if cursor + seconds > 15:
                raise store.VideoProjectError('dialogue_reel_limit')
            shot_id = uuid.uuid4().hex
            clip_duration = frames / rate
            shots.append(VideoProjectShot(id=shot_id, start_sec=cursor, seconds=seconds, prompt=selection.prompt, seed=index))
            cues.append(VideoDialogueCue(shot_id=shot_id, book_id=body.book_id, chapter_index=body.chapter_index,
                passage_id=passage.id, source_revision=body.revision, render_identity=passage.render_identity,
                profile_id=passage.profile_id, speaker=passage.speaker, text=passage.text, language=passage.language,
                source_duration_ms=source_duration_ms, source_start_ms=selection.clip_start_ms, source_end_ms=round(end * 1000 / rate),
                start_sec=cursor, end_sec=cursor + clip_duration, audio_sha256=hashlib.sha256(pcm).hexdigest(), waveform_peaks=_peaks(pcm),
                renderer=passage.renderer,cloud_provenance=passage.cloud_provenance))
            caption = selection.caption if selection.caption is not None else passage.text
            if len(caption) > 500:
                raise store.VideoProjectError('dialogue_caption_limit')
            overlays.append(VideoOverlay(id=shot_id, kind='lyric', text=caption,
                start_sec=cursor, end_sec=cursor + clip_duration))
            copied = store.artifact(project_id, f'cue-copies/{shot_id}.wav')
            copied.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(copied), 'wb') as selected:
                selected.setnchannels(channels); selected.setsampwidth(width); selected.setframerate(rate)
                selected.writeframes(pcm)
            copies.append(_CueCopy(copied, actual, frames, seconds))
            cursor += seconds
    project = VideoProject(id=project_id, revision=1, track_id=None, track_title='', name=body.name,
        preset='reel', duration_sec=cursor, source_fingerprint='', created_at=stamp, updated_at=stamp,
        settings=VideoProjectSettings(width=704, height=1280),
        export_settings=VideoExportSettings(aspect='portrait', attach_speech=True),
        shots=shots, dialogue_cues=cues, overlays=overlays, warnings=['dialogue_video_is_silent'],
        job=VideoProjectJob(id=uuid.uuid4().hex, operation='preview', status='running'))
    store._enforce_reel(project)
    document = store.StoredVideoProject(project=project, speech_path=speech_path, preparing=True)
    # Hidden staging still persists the worker identity used by _command.
    store.save(document)
    common = target or (max(copy.parameters[0] for copy in copies), 2, max(copy.parameters[2] for copy in copies))
    channels, width, rate = common
    from .video_render import _command
    from .video_media import tool
    with wave.open(str(output), 'wb') as destination:
        destination.setnchannels(channels); destination.setsampwidth(width); destination.setframerate(rate)
        for index, copy in enumerate(copies):
            count = round(copy.frames * rate / copy.parameters[2])
            normalized = copy.path
            if copy.parameters != common:
                normalized = copy.path.with_name(copy.path.stem + '.normalized.wav')
                await _command(project_id, [tool('ffmpeg'), '-v', 'error', '-xerror', '-y', '-i', str(copy.path),
                    '-vn', '-ac', str(channels), '-ar', str(rate), '-af', f'aresample={rate},apad,atrim=end_sample={count}',
                    '-c:a', 'pcm_s16le', str(normalized)], 'dialogue_normalizing', timeout=120)
            with wave.open(str(normalized), 'rb') as selected:
                if (selected.getnchannels(), selected.getsampwidth(), selected.getframerate(), selected.getnframes()) != (*common, count):
                    raise store.VideoProjectError('audio_format_mismatch')
                pcm = selected.readframes(count)
            if len(pcm) != count * channels * width:
                raise store.VideoProjectError('audio_format_mismatch')
            destination.writeframesraw(pcm)
            destination.writeframesraw(bytes((copy.seconds * rate - count) * channels * width))
            # Captions follow the checked normalized copy; source bounds/hashes
            # continue to identify the untouched accepted dry recording.
            project.dialogue_cues[index].end_sec = project.dialogue_cues[index].start_sec + count / rate
            project.overlays[index].end_sec = project.dialogue_cues[index].end_sec
    with output.open('rb+') as handle:
        os.fsync(handle.fileno())
    project.speech_clip = VideoSpeechClip(id=speech_id, name=body.name, bytes=output.stat().st_size,
        duration_sec=cursor, sha256=store.file_hash(output))
    project.job = None
    return document

async def create(body: CreateDialogueReelRequest) -> VideoProject:
    project_id = uuid.uuid4().hex
    return await _track(project_id, project_id, lambda: _create(body, project_id))


async def _create(body: CreateDialogueReelRequest, project_id: str) -> VideoProject:
    root = store.project_dir(project_id)
    root.mkdir(parents=True, exist_ok=False)
    published = False
    try:
        document = await _prepare(body, project_id)
        # Recheck all participating voices at publication, in video→profile
        # order. Long PCM reads hold neither lock here.
        with store._lock, voice_profiles._LOCK:
            require_consent(document.project)
            document.preparing = False
            store.save(document)
        published = True
        return store.view(document)
    except audiobooks.AudiobookError as exc:
        raise store.VideoProjectError(exc.code) from exc
    except VideoMediaError as exc:
        raise store.VideoProjectError(exc.code) from exc
    except (wave.Error, EOFError) as exc:
        raise store.VideoProjectError('audio_format_mismatch') from exc
    except OSError as exc:
        raise store.VideoProjectError('storage_failed') from exc
    finally:
        if not published:
            _discard_stage(project_id)


async def refresh(project_id: str, shot_id: str, body: RefreshDialogueCueRequest) -> VideoProject:
    stage_id = uuid.uuid4().hex
    return await _track(project_id, stage_id, lambda: _refresh(project_id, shot_id, body, stage_id))


async def _refresh(project_id: str, shot_id: str, body: RefreshDialogueCueRequest, stage_id: str) -> VideoProject:
    document = store.load(project_id)
    if document.project.revision != body.revision:
        raise store.VideoProjectError('revision_conflict')
    old_cue = next((cue for cue in document.project.dialogue_cues if cue.shot_id == shot_id), None)
    shot = next((shot for shot in document.project.shots if shot.id == shot_id), None)
    if old_cue is None or shot is None or body.source.book_id != old_cue.book_id or body.source.chapter_index != old_cue.chapter_index or body.source.selections[0].passage_id != old_cue.passage_id:
        raise store.VideoProjectError('passage_not_ready')
    staging = store.project_dir(stage_id)
    staging.mkdir(parents=True, exist_ok=False)
    speech_id = uuid.uuid4().hex
    relative = f'speech/{speech_id}.wav'
    output = store.artifact(project_id, relative)
    published = False
    try:
        with wave.open(str(store.speech_file(project_id)), 'rb') as current:
            target = (current.getnchannels(), current.getsampwidth(), current.getframerate())
        replacement = await _prepare(body.source, stage_id, target=target)
        cue = replacement.project.dialogue_cues[0]
        clip_duration = cue.end_sec - cue.start_sec
        if clip_duration > shot.seconds:
            raise store.VideoProjectError('dialogue_clip_bounds')
        original_path = store.speech_file(project_id)
        if document.project.speech_clip is None or store.file_hash(original_path) != document.project.speech_clip.sha256:
            raise store.VideoProjectError('source_changed')
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(original_path), 'rb') as original, wave.open(str(store.artifact(stage_id, replacement.speech_path)), 'rb') as changed:
            params = original.getparams()
            if (params.nchannels, params.sampwidth, params.framerate) != (changed.getnchannels(), changed.getsampwidth(), changed.getframerate()):
                raise store.VideoProjectError('audio_format_mismatch')
            if params.nframes > params.framerate * 15:
                raise store.VideoProjectError('dialogue_reel_limit')
            start = round(shot.start_sec * params.framerate)
            slot = shot.seconds * params.framerate
            count = round(clip_duration * params.framerate)
            pcm = changed.readframes(count)
            with wave.open(str(output), 'wb') as destination:
                destination.setparams(params)
                destination.writeframesraw(original.readframes(start))
                destination.writeframesraw(pcm)
                destination.writeframesraw(bytes((slot-count)*params.nchannels*params.sampwidth))
                original.setpos(start+slot)
                destination.writeframesraw(original.readframes(params.nframes-start-slot))
        with output.open('rb+') as handle:
            os.fsync(handle.fileno())
        updated_cue = cue.model_copy(update={'shot_id':shot_id, 'start_sec':shot.start_sec, 'end_sec':shot.start_sec+clip_duration})
        caption = replacement.project.overlays[0].model_copy(update={'id':shot_id, 'start_sec':shot.start_sec, 'end_sec':shot.start_sec+clip_duration})
        def change(saved: store.StoredVideoProject) -> None:
            saved.project.dialogue_cues = [updated_cue if item.shot_id == shot_id else item for item in saved.project.dialogue_cues]
            saved.project.overlays = [caption if item.id == shot_id else item for item in saved.project.overlays]
            if not any(item.id == shot_id for item in saved.project.overlays):
                saved.project.overlays.append(caption)
            selected = next(item for item in saved.project.shots if item.id == shot_id)
            selected.prompt = body.source.selections[0].prompt
            selected.approved_variant_id = None
            saved.speech_path = relative
            saved.project.speech_clip = VideoSpeechClip(id=speech_id, name=saved.project.name, bytes=output.stat().st_size,
                duration_sec=saved.project.duration_sec, sha256=store.file_hash(output))
            saved.project.file_url = ''; saved.project.poster_url = ''
            require_consent(saved.project)
        with store._lock, voice_profiles._LOCK:
            result = store.mutate(project_id, change, revision=body.revision)
        published = True
        return result
    except audiobooks.AudiobookError as exc:
        raise store.VideoProjectError(exc.code) from exc
    except VideoMediaError as exc:
        raise store.VideoProjectError(exc.code) from exc
    except (wave.Error, EOFError) as exc:
        raise store.VideoProjectError('audio_format_mismatch') from exc
    except OSError as exc:
        raise store.VideoProjectError('storage_failed') from exc
    finally:
        _discard_stage(stage_id)
        if not published:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove unpublished dialogue audio %s", output, exc_info=True)
