"""Resolve retained media by backend identities, never client filesystem paths."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import wave
from . import export_provenance, reading_media, speech_clone, voice_profiles
from .export_provenance_contracts import ProvenanceComponent
from .reading_media_contracts import RetainedAudioSource, RetainedAudioInfo, RetainedAudioIdentity


@dataclass(frozen=True)
class RetainedSnapshot:
    path: Path
    duration_ms: int
    sha256: str
    profile_ids: list[str]
    components: list[ProvenanceComponent]


def snapshot(source: RetainedAudioSource) -> RetainedSnapshot:
    if source.kind == 'chapter':
        if source.chapter_index is None or source.revision is None:
            raise reading_media.ReadingMediaError('reading_source_changed')
        chapter = reading_media.chapter_snapshot(source.source_id, source.chapter_index, source.revision)
        return RetainedSnapshot(chapter.audio_path, chapter.duration_ms, chapter.audio_sha256,
                                chapter.profile_ids, chapter.components)
    root = speech_clone.TRIALS_ROOT
    path = root / f'{source.source_id}.wav'
    if root.is_symlink() or path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
        raise reading_media.ReadingMediaError('retained_audio_not_found', 404)
    try:
        provenance = export_provenance.read(path)
        profile_ids: list[str] = []
        for component in provenance.components:
            if component.role != 'audio':
                continue
            match = re.fullmatch(r'trial:([0-9a-f]{32}):profile:([0-9a-f]{32})', component.source_id)
            if match is None or match[1] != source.source_id:
                raise reading_media.ReadingMediaError('retained_audio_unverified')
            profile_ids.append(match[2])
        if not profile_ids:
            raise reading_media.ReadingMediaError('retained_audio_unverified')
        reading_media.require_consent(profile_ids)
        with wave.open(str(path), 'rb') as audio:
            duration = round(audio.getnframes() * 1000 / audio.getframerate())
        return RetainedSnapshot(path, duration, provenance.artifact_sha256,
                                list(dict.fromkeys(profile_ids)), provenance.components)
    except (OSError, ValueError, wave.Error, export_provenance.ProvenanceError) as error:
        raise reading_media.ReadingMediaError('retained_audio_unverified') from error


def info(source: RetainedAudioSource) -> RetainedAudioInfo:
    value = snapshot(source)
    return RetainedAudioInfo(source=source, duration_ms=value.duration_ms,
        source_sha256=value.sha256, content_origin=export_provenance.origin(value.components))


def require_video_consent(identity: RetainedAudioIdentity | None, clip_sha256: str | None) -> None:
    if identity is None:
        return
    if identity.clip_sha256 != clip_sha256:
        raise reading_media.ReadingMediaError('retained_audio_changed')
    # The retained PCM is independent of later book edits. Live profile consent
    # still applies to that retained recording, including its copies/history.
    try:
        reading_media.require_consent(identity.profile_ids)
    except voice_profiles.VoiceProfileError as error:
        raise reading_media.ReadingMediaError(error.code, error.status) from error


def video_components(identity: RetainedAudioIdentity, clip_sha256: str) -> list[ProvenanceComponent]:
    require_video_consent(identity, clip_sha256)
    components = [component.model_copy(deep=True) for component in identity.components]
    content = export_provenance.origin(components)
    components.append(ProvenanceComponent(role='audio', content_origin=content,
        source_id=f'retained:{identity.source.kind}:{identity.source.source_id}:clip:{identity.clip_start_ms}-{identity.clip_end_ms}',
        source_sha256=clip_sha256, classification_basis='unverified' if content == 'unknown' else 'app_workflow'))
    return components
