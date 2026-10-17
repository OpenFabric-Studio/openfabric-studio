"""Retained-source ACE preflight over the existing queue and managed native run."""
from __future__ import annotations

from dataclasses import replace
import json

from . import config, db, music_conditioning, song_capabilities, song_sources
from .beat_song_mix_contracts import BeatSongMixError
from .job_lifecycle import run_owned_thread
from .module_catalog import ModuleEnvironment, available_bytes, configured_environment, engine_python
from .music_conditioning_contracts import MusicConditioningPreview
from .music_queue_contracts import MusicQueueError
from .module_music_models import ace_conditioning_artifacts
from .queued_music_source_contracts import QueuedMusicSource
from .song_from_beat_contracts import AceMusicConditioning


def launch_specs(environment: ModuleEnvironment | None = None) -> tuple[config.ProcessSpec, ...]:
    env = environment or configured_environment()
    definition = config.MODELS['ace_step']
    root = env.paths['ace_step'].resolve()
    python = engine_python(root, env.platform)
    if len(definition.processes) != 1 or definition.processes[0].cwd.resolve() != root or not python.is_file():
        raise MusicQueueError('conditioning_runtime_unavailable')
    original = definition.processes[0]
    try:
        arguments = original.cmd[original.cmd.index('acestep-api') + 1:]
    except ValueError as error:
        raise MusicQueueError('conditioning_runtime_changed') from error
    bootstrap = song_capabilities.bootstrap_path()
    if not bootstrap.is_file():
        raise MusicQueueError('conditioning_runtime_changed')
    return (replace(original, cmd=[str(python), str(bootstrap), *arguments], env={**original.env,
        'ACESTEP_CONFIG_PATH': song_capabilities.BASE_MODEL, 'ACESTEP_NO_INIT': 'true',
        'ACESTEP_CHECKPOINTS_DIR': str(root / 'checkpoints'), 'ACESTEP_PROJECT_ROOT': str(root),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'OPENFABRIC_ACE_LOCAL_ARTIFACTS': json.dumps([item.path for item in ace_conditioning_artifacts()], separators=(',', ':'))}),)


async def preview(options: AceMusicConditioning, *, retained_input: QueuedMusicSource | None = None,
                  environment: ModuleEnvironment | None = None) -> MusicConditioningPreview:
    env = environment or configured_environment()
    try:
        if retained_input is None:
            # Reject oversized files before the general shared source inspector hashes them.
            projection = music_conditioning._resolve(options.source)
            if projection.path.stat().st_size > 512 * 1024**2:
                raise MusicQueueError('conditioning_source_invalid', 422)
            facts = await run_owned_thread(music_conditioning._facts, projection.path, max_seconds=300)
            source = await song_sources.inspect_source(options.source, expected_sha256=options.source_sha256)
        else:
            from .queued_music_sources import resolve
            path = await resolve(retained_input, options)
            facts = await run_owned_thread(music_conditioning._facts, path, max_seconds=300)
            source = retained_input.snapshot
        if source.duration_sec < 10 or source.duration_sec > 300 or source.source_bytes > 512 * 1024**2:
            raise MusicQueueError('conditioning_source_invalid', 422)
    except BeatSongMixError as error:
        raise MusicQueueError('music_queue_cleanup_failed' if error.code == 'song_cleanup_failed'
            else 'conditioning_source_changed') from error
    state = song_capabilities.capabilities(env).modes[0]
    # Native upload, decoded source, latent/decode working copies, retained output and reserve.
    required = source.source_bytes * (2 if retained_input is None else 1) + int((source.duration_sec + 10) * 48000 * 2 * 4 * 6) + 64 * 1024**2
    free = available_bytes(db.FILES_DIR)
    enough = free is not None and free >= required
    failure_code = state.reason if not state.ready else None if enough else 'insufficient_disk'
    return MusicConditioningPreview(source=options.source, source_sha256=options.source_sha256,
        source_bytes=source.source_bytes, source_rate_hz=facts.rate, source_channels=facts.channels,
        source_frames=facts.frames, duration_sec=source.duration_sec, required_free_bytes=required,
        free_bytes=free, can_start=state.ready and enough, error_code=failure_code)
