"""Contained immutable kits and the deterministic, block-based CPU beat sampler.

Samples stay in the local kit store. Render bundles contain authored documents,
audio and public declarations, never a redistributable copy of an imported kit.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import errno
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
from typing import BinaryIO
import uuid
import zipfile

import numpy as np
from numpy.typing import NDArray
from pydantic import TypeAdapter, ValidationError
import soundfile as sf

from . import db, export_provenance
from .atomic_files import document_lock, read_object, write_object
from .beat_compile import CompiledBeat, CompiledEvent, compile_events, compiled_events_json, round_half_up
from .beat_contracts import (BLOCK_FRAMES, MAX_POLYPHONY, BeatContract, BeatDocument, BeatError,
    BeatId, BeatInstrument, BeatInstrumentManifest, BeatJobRequest, BeatKitInfo, BeatRenderManifest,
    BeatArtifact, BeatErrorCode, BeatRenderWarning, BeatSampleRegion, BeatSha256, canonical_json, manifest_sha256)
from .beat_midi import write_midi
from .export_provenance_contracts import MusicComponentEvidence, MusicEvidence, ProvenanceComponent
from .module_contracts import ModuleLicenseDeclaration
from .music_evidence import shareable_music_evidence

RATE = 48000
MAX_KIT_BYTES = 512 * 1024 * 1024
_id = TypeAdapter(BeatId)
_sha = TypeAdapter(BeatSha256)


def _root() -> Path:
    base = db.FILES_DIR.resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / '_beats'
    if root.is_symlink():
        raise BeatError('beat_storage_unavailable')
    root.mkdir(exist_ok=True)
    return root


def _contained(path: Path) -> Path:
    root = _root()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BeatError('beat_storage_unavailable') from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BeatError('beat_storage_unavailable')
    if not path.resolve().is_relative_to(root):
        raise BeatError('beat_storage_unavailable')
    return path


def workspace(render_id: str, *, create: bool = False) -> Path:
    try:
        _id.validate_python(render_id)
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc
    path = _contained(_root() / 'renders' / render_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _kits() -> Path:
    path = _contained(_root() / 'kits')
    path.mkdir(exist_ok=True)
    return path


def _asset_name(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode('utf-8')).hexdigest() + '.wav'


def _kit_directory(manifest: BeatInstrumentManifest) -> Path:
    return _contained(_kits() / manifest_sha256(manifest))


def resolve_sample(manifest: BeatInstrumentManifest, asset_id: str) -> Path:
    if not any(region.asset_id == asset_id for instrument in manifest.instruments for region in instrument.regions):
        raise BeatError('beat_instrument_missing')
    path = _contained(_kit_directory(manifest) / _asset_name(asset_id))
    if not path.is_file():
        raise BeatError('beat_instrument_missing')
    return path


def _bytes(path: Path, data: bytes) -> None:
    _contained(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalize_wav(stream: BinaryIO) -> None:
    """Remove libsndfile's PEAK wall-clock timestamp from app-generated WAVs."""
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE':
        raise BeatError('beat_worker_failed')
    size = os.fstat(stream.fileno()).st_size
    while stream.tell() + 8 <= size:
        chunk = stream.read(8)
        tag, length = struct.unpack('<4sI', chunk)
        start = stream.tell()
        if start + length > size:
            raise BeatError('beat_worker_failed')
        if tag == b'PEAK' and length >= 8:
            stream.seek(start + 4)
            stream.write(b'\0' * 4)
        stream.seek(start + length + length % 2)
    stream.flush()
    os.fsync(stream.fileno())


def _validate_sample(path: Path, region: BeatSampleRegion) -> None:
    try:
        if path.stat().st_size != region.byte_size or export_provenance.digest(path) != region.sha256:
            raise BeatError('beat_sample_changed')
        info = sf.info(path)
        if (info.format != 'WAV' or (info.samplerate, info.channels, info.frames) !=
                (region.sample_rate_hz, region.channels, region.frames)):
            raise BeatError('beat_kit_invalid')
        with sf.SoundFile(path) as audio:
            frames = 0
            while len(block := audio.read(BLOCK_FRAMES, dtype='float32', always_2d=True)):
                frames += len(block)
                if not np.isfinite(block).all():
                    raise BeatError('beat_kit_invalid')
            if frames != region.frames:
                raise BeatError('beat_kit_invalid')
    except (OSError, RuntimeError, export_provenance.ProvenanceError) as exc:
        raise BeatError('beat_kit_invalid') from exc


def _zip_directory(archive: BinaryIO, expected: int) -> None:
    """Bound central-directory allocation before ZipFile constructs its list.

    The supported kit format needs neither ZIP64 nor multi-volume archives.
    Assets and their small directory are bounded independently of compression.
    """
    archive.seek(0, os.SEEK_END)
    size = archive.tell()
    if size > MAX_KIT_BYTES + 8 * 1024 * 1024:
        raise BeatError('beat_render_limit')
    archive.seek(max(0, size - 65557))
    tail = archive.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or offset + 22 > len(tail):
        raise BeatError('beat_kit_invalid')
    disk, directory_disk, disk_entries, entries, directory_bytes, directory_offset, comment = struct.unpack_from('<4H2IH', tail, offset + 4)
    if (disk != 0 or directory_disk != 0 or disk_entries != entries or entries != expected or entries == 65535
            or directory_bytes > max(4096, expected * 256) or directory_offset + directory_bytes > size
            or offset + 22 + comment != len(tail)):
        raise BeatError('beat_kit_invalid')
    archive.seek(0)


def import_kit(manifest: BeatInstrumentManifest, archive: BinaryIO) -> BeatKitInfo:
    """Import exact declared WAV assets; archive names are identifiers, not paths."""
    manifest = BeatInstrumentManifest.model_validate(manifest.model_dump())
    regions = {region.asset_id: region for instrument in manifest.instruments for region in instrument.regions}
    expected = {asset + '.wav': region for asset, region in regions.items()}
    if sum(item.byte_size for item in regions.values()) > MAX_KIT_BYTES:
        raise BeatError('beat_render_limit')
    _zip_directory(archive, len(expected))
    try:
        required = math.ceil(sum(item.byte_size for item in regions.values()) * 1.25) + 256 * 1024 * 1024
        if shutil.disk_usage(_kits()).free < required:
            raise BeatError('beat_disk_full')
    except OSError as exc:
        raise BeatError('beat_storage_unavailable') from exc
    temporary = _kits() / ('.import-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            if len(entries) != len(expected) or len({entry.filename for entry in entries}) != len(entries):
                raise BeatError('beat_kit_invalid')
            for entry in entries:
                if (entry.filename not in expected or '/' in entry.filename or '\\' in entry.filename or
                        entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise BeatError('beat_kit_invalid')
                region = expected[entry.filename]
                if entry.file_size != region.byte_size:
                    raise BeatError('beat_sample_changed')
                path = temporary / _asset_name(region.asset_id)
                size = 0
                with zipped.open(entry) as source, path.open('xb') as target:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        if size > region.byte_size:
                            raise BeatError('beat_sample_changed')
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                _validate_sample(path, region)
        _bytes(temporary / 'manifest.json', canonical_json(manifest))
        destination = _kit_directory(manifest)
        catalog = _contained(_kits() / 'catalog.json')
        with document_lock(catalog):
            if destination.exists():
                # Re-import still validates every input before accepting immutable reuse.
                for region in regions.values():
                    _validate_sample(resolve_sample(manifest, region.asset_id), region)
            else:
                temporary.replace(destination)
            values = read_object(catalog) if catalog.exists() else {}
            if manifest.kit_id not in values and len(values) >= 100:
                raise BeatError('beat_render_limit')
            values[manifest.kit_id] = manifest_sha256(manifest)
            write_object(catalog, values)
        return BeatKitInfo(manifest=manifest, sha256=manifest_sha256(manifest))
    except OSError as exc:
        raise BeatError('beat_disk_full' if exc.errno == errno.ENOSPC else 'beat_storage_unavailable') from exc
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise BeatError('beat_kit_invalid') from exc
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _procedural() -> None:
    """General app-owned synthesis, without claims of regional authenticity."""
    catalog = _contained(_kits() / 'catalog.json')
    with document_lock(catalog):
        values = read_object(catalog) if catalog.exists() else {}
        if 'procedural-core' in values:
            return
        temporary = _kits() / ('.procedural-' + uuid.uuid4().hex)
        temporary.mkdir()
        instruments: list[BeatInstrument] = []
        definitions = [('kick', 'Kick', 36, .30), ('snare', 'Snare', 38, .22),
            ('closed-hat', 'Closed hat', 42, .07), ('open-hat', 'Open hat', 46, .40),
            ('clap', 'Clap', 39, .16), ('rim', 'Rim', 37, .06),
            ('tom', 'Tom', 45, .24), ('shaker', 'Shaker', 70, .10)]
        try:
            for index, (asset, label, note, duration) in enumerate(definitions):
                t = np.arange(round(RATE * duration), dtype=np.float64) / RATE
                if asset in ('kick', 'tom'):
                    phase = 2 * np.pi * (45 * t + (80 if asset == 'kick' else 160) * .04 * (1 - np.exp(-t / .04)))
                    signal = .65 * np.sin(phase) * np.exp(-t * 22)
                else:
                    frequencies = [1871 + 113 * index, 3271 + 127 * index, 5147 + 109 * index]
                    signal = np.zeros(len(t), dtype=np.float64)
                    for frequency in frequencies:
                        signal += np.sin(2 * np.pi * frequency * t) / 8
                    signal *= np.exp(-t * (7 / duration))
                path = temporary / _asset_name(asset)
                with path.open('x+b') as handle:
                    with sf.SoundFile(handle.fileno(), 'w', samplerate=RATE, channels=1,
                            subtype='FLOAT', format='WAV', closefd=False) as audio:
                        audio.write(signal.astype(np.float32))
                    _normalize_wav(handle)
                terms = ModuleLicenseDeclaration(component_id='openfabric-procedural-' + asset,
                    name='OpenFabric procedural ' + label, scope='samples', status='unknown',
                    notes='App-owned mathematical synthesis. No third-party recording; no source-rights clearance inferred from application licensing.',
                    reviewed_at='2026-10-17')
                region = BeatSampleRegion(region_id=asset, asset_id=asset,
                    sha256=export_provenance.digest(path), byte_size=path.stat().st_size,
                    sample_rate_hz=RATE, channels=1, frames=len(signal), root_note_midi=note,
                    choke_group='hi-hat' if asset in ('open-hat', 'closed-hat') else None,
                    terms=terms, attribution='OpenFabric Studio procedural synthesis; general starter kit, not a regional instrument recording.')
                instruments.append(BeatInstrument(instrument_id=asset, label=label, role='drum', midi_note=note, regions=[region]))
            manifest = BeatInstrumentManifest(kit_id='procedural-core', revision='beat-procedural-v1', instruments=instruments)
            _bytes(temporary / 'manifest.json', canonical_json(manifest))
            destination = _kit_directory(manifest)
            if not destination.exists():
                temporary.replace(destination)
            values['procedural-core'] = manifest_sha256(manifest)
            write_object(catalog, values)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def load_kit(kit_id: str, digest: str | None = None) -> BeatInstrumentManifest:
    if kit_id == 'procedural-core' and digest is None:
        _procedural()
    try:
        if digest is None:
            value = read_object(_contained(_kits() / 'catalog.json')).get(kit_id)
            digest = _sha.validate_python(value)
        else:
            digest = _sha.validate_python(digest)
        path = _contained(_kits() / digest / 'manifest.json')
        if path.stat().st_size > 4 * 1024 * 1024:
            raise BeatError('beat_kit_invalid')
        manifest = BeatInstrumentManifest.model_validate_json(path.read_bytes())
        if manifest.kit_id != kit_id or manifest_sha256(manifest) != digest:
            raise BeatError('beat_sample_changed')
        return manifest
    except (OSError, ValidationError, ValueError) as exc:
        raise BeatError('beat_instrument_missing') from exc


def list_kits() -> list[BeatKitInfo]:
    _procedural()
    values = read_object(_contained(_kits() / 'catalog.json'))
    return [BeatKitInfo(manifest=(manifest := load_kit(identifier)), sha256=manifest_sha256(manifest))
        for identifier in sorted(values)]


@dataclass
class _Voice:
    event: CompiledEvent
    data: NDArray[np.float32]
    phase: float = 0
    ended: bool = False


def render_frame_bound(compiled: CompiledBeat) -> int:
    """Conservative actual voice ends, rather than always reserving 30s silence."""
    frames = compiled.duration_frames
    frames_per_tick = Fraction(60 * RATE, 960) / Fraction(str(compiled.bpm))
    for event in compiled.events:
        minimum_curve = min((point.cents for point in event.pitch_curve), default=0)
        delta = ((event.note_midi - event.root_note_midi) * 100 + event.cents - event.root_cents + minimum_curve
            if event.pitched else 0)
        natural_end = event.onset_frame + math.ceil(event.sample_frames * RATE / event.sample_rate_hz / 2 ** (delta / 1200))
        release_start = None if event.one_shot else event.onset_frame + event.gate_frames
        if event.choke_ticks is not None:
            choke = round_half_up(event.choke_ticks * frames_per_tick)
            release_start = min(release_start, choke) if release_start is not None else choke
        end = min(natural_end, release_start + math.ceil(event.release_ms * RATE / 1000)) if release_start is not None else natural_end
        frames = max(frames, end)
    if frames > compiled.maximum_render_frames:
        raise BeatError('beat_render_limit')
    return frames


def _voice_block(voice: _Voice, start: int, count: int, bpm: float) -> NDArray[np.float32]:
    event = voice.event
    absolute = np.arange(start, start + count, dtype=np.float64)
    age = absolute - event.onset_frame
    active = age >= 0
    curve = np.zeros(count, dtype=np.float64)
    if event.pitch_curve:
        curve = np.interp(age / event.gate_frames, [float(point.position) for point in event.pitch_curve],
            [point.cents for point in event.pitch_curve])
    ratio = np.full(count, event.sample_rate_hz / RATE, dtype=np.float64)
    if event.pitched:
        ratio *= np.exp2(((event.note_midi - event.root_note_midi) * 100 + event.cents - event.root_cents + curve) / 1200)
    ratio[~active] = 0
    positions = voice.phase + np.cumsum(ratio) - ratio
    voice.phase += float(np.sum(ratio))
    valid = active & (positions < len(voice.data))
    output = np.zeros((count, 2), dtype=np.float32)
    if not valid.any():
        voice.ended = bool(active.any())
        return output
    release_start: float | None = None if event.one_shot else event.onset_frame + event.gate_frames
    if event.choke_ticks is not None:
        choke = float(event.choke_ticks) * 60 * RATE / (960 * bpm)
        release_start = min(release_start, choke) if release_start is not None else choke
    envelope = np.ones(count, dtype=np.float64)
    attack = event.attack_ms * RATE / 1000
    if attack > 0:
        envelope *= np.minimum(1, np.maximum(0, age / attack))
    if release_start is not None:
        release_frames = event.release_ms * RATE / 1000
        envelope *= np.clip(1 - (absolute - release_start) / release_frames, 0, 1)
        if start + count >= release_start + release_frames:
            voice.ended = True
    if positions[-1] >= len(voice.data) - 1:
        voice.ended = True
    indices = np.flatnonzero(valid)
    source_positions = positions[valid]
    left = np.floor(source_positions).astype(np.int64)
    right = np.minimum(left + 1, len(voice.data) - 1)
    fraction = source_positions - left
    amplitude = envelope[valid] * (event.velocity / 127) * 10 ** ((event.gain_db + event.sample_gain_db) / 20)
    mono = voice.data.shape[1] == 1
    for channel in range(2):
        balance = math.sqrt((1 + (-event.pan if channel == 0 else event.pan)) / (2 if mono else 1))
        channel_data = voice.data[:, 0 if mono else channel]
        values = channel_data[left] * (1 - fraction) + channel_data[right] * fraction
        output[indices, channel] = (values * amplitude * balance).astype(np.float32)
    return output


def _artifact(render_id: str, kind: str, path: Path, *, lane_id: str | None = None) -> BeatArtifact:
    # Pydantic validates the deliberately narrow kind union at this boundary.
    return BeatArtifact.model_validate({'id': hashlib.sha256((render_id + ':' + path.name).encode()).hexdigest()[:32],
        'kind': kind, 'lane_id': lane_id, 'sha256': export_provenance.digest(path),
        'byte_size': path.stat().st_size, 'media_type': 'audio/wav' if path.suffix == '.wav' else
            'audio/midi' if path.suffix == '.mid' else 'application/zip' if path.suffix == '.zip' else
            'text/plain' if path.suffix == '.txt' else 'application/json', 'filename': path.name})


def sample_evidence(kit: BeatInstrumentManifest, compiled: CompiledBeat) -> MusicEvidence:
    used = {event.asset_id for event in compiled.events}
    components: list[MusicComponentEvidence] = []
    attributions: list[str] = []
    for instrument in kit.instruments:
        for region in instrument.regions:
            if region.asset_id not in used:
                continue
            terms = region.terms
            identifier = 'beat-sample:' + hashlib.sha256((kit.kit_id + ':' + region.asset_id).encode()).hexdigest()
            item = MusicComponentEvidence(component_id=identifier, scope='samples',
                restriction=terms.restriction, redistribution=terms.redistribution, copyleft=terms.copyleft,
                declared_license=terms.declared_license, source_url=terms.source_url,
                evidence=terms.evidence, identities=terms.identities)
            if item not in components:
                components.append(item)
            if region.attribution and region.attribution not in attributions:
                attributions.append(region.attribution)
    # Known terms are retained; uploaded source rights are not inferred as cleared.
    if len(components) > 1000 or len(attributions) > 1000:
        raise BeatError('beat_render_limit')
    return shareable_music_evidence(MusicEvidence(components=components, lineage_complete=True,
        public_attributions=attributions))


def render(document: BeatDocument, request: BeatJobRequest, kit: BeatInstrumentManifest,
    render_id: str, output_dir: Path, *, cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
    sample_resolver: Callable[[BeatInstrumentManifest, str], Path] | None = None) -> BeatRenderManifest:
    """Render exact compiler events into aligned 48kHz stereo FLOAT WAVs."""
    expected = workspace(render_id, create=True) / 'output'
    if output_dir != expected:
        raise BeatError('beat_storage_unavailable')
    _contained(output_dir).mkdir(exist_ok=True)
    if (output_dir / 'manifest.json').exists():
        return validate_output(render_id)
    compiled = compile_events(document, RATE, kit, preview_pattern_id=request.preview_pattern_id,
        occurrence_index=request.occurrence_index)
    used_events = {event.asset_id: event for event in compiled.events}
    if len(used_events) > 998:
        # Existing export provenance has at most 1000 exact source components.
        raise BeatError('beat_render_limit')
    evidence = sample_evidence(kit, compiled)
    samples: dict[str, NDArray[np.float32]] = {}
    for instrument in kit.instruments:
        for region in instrument.regions:
            if region.asset_id in samples or region.asset_id not in used_events:
                continue
            path = (sample_resolver or resolve_sample)(kit, region.asset_id)
            _validate_sample(path, region)
            samples[region.asset_id] = sf.read(path, dtype='float32', always_2d=True)[0]
    lanes = [lane.lane_id for lane in document.patterns[0].lanes]
    paths = [output_dir / 'mix.wav', *[output_dir / ('stem-' + hashlib.sha256(lane.encode()).hexdigest()[:20] + '.wav') for lane in lanes]]
    temporary = [path.with_suffix('.partial.wav') for path in paths]
    events = iter(compiled.events)
    waiting = next(events, None)
    voices: list[_Voice] = []
    peak, cursor = 0.0, 0
    try:
        with ExitStack() as stack:
            handles = [stack.enter_context(_contained(path).open('x+b')) for path in temporary]
            audio_stack = stack.enter_context(ExitStack())
            streams = [audio_stack.enter_context(sf.SoundFile(handle.fileno(), 'w', samplerate=RATE, channels=2,
                subtype='FLOAT', format='WAV', closefd=False)) for handle in handles]
            while cursor < compiled.duration_frames or voices or waiting is not None:
                if cancelled():
                    raise BeatError('beat_cancelled')
                count = min(BLOCK_FRAMES, compiled.maximum_render_frames - cursor)
                if count <= 0:
                    raise BeatError('beat_render_limit')
                while waiting is not None and waiting.onset_frame < cursor + count:
                    voices.append(_Voice(waiting, samples[waiting.asset_id]))
                    waiting = next(events, None)
                # Compiler has already bounded simultaneous voices, including tails.
                # A block can contain many short non-overlapping voices.
                blocks = {lane: np.zeros((count, 2), dtype=np.float32) for lane in lanes}
                active_counts = np.zeros(count, dtype=np.int32)
                last_nonzero_voice = 0
                for voice in voices:
                    block = _voice_block(voice, cursor, count, compiled.bpm)
                    blocks[voice.event.lane_id] += block
                    nonzero = np.any(block != 0, axis=1)
                    active_counts += nonzero
                    indexes = np.flatnonzero(nonzero)
                    if len(indexes):
                        last_nonzero_voice = max(last_nonzero_voice, int(indexes[-1]) + 1)
                if int(np.max(active_counts, initial=0)) > MAX_POLYPHONY:
                    raise BeatError('beat_render_limit')
                voices = [voice for voice in voices if not voice.ended]
                if not voices and waiting is None:
                    count = min(count, max(0, compiled.duration_frames - cursor, last_nonzero_voice))
                if not count:
                    break
                mix = np.zeros((count, 2), dtype=np.float32)
                for lane in lanes:
                    mix += blocks[lane][:count]
                if not np.isfinite(mix).all():
                    raise BeatError('beat_worker_failed')
                peak = max(peak, float(np.max(np.abs(mix), initial=0)))
                streams[0].write(mix)
                for stream, lane in zip(streams[1:], lanes, strict=True):
                    stream.write(blocks[lane][:count])
                cursor += count
                progress(cursor, max(compiled.duration_frames, cursor))
            audio_stack.close()
            for handle in handles:
                _normalize_wav(handle)
        if cancelled():
            raise BeatError('beat_cancelled')
        for source, destination in zip(temporary, paths, strict=True):
            source.replace(destination)
        artifacts = [_artifact(render_id, 'mix', paths[0])]
        artifacts.extend(_artifact(render_id, 'stem', path, lane_id=lane) for lane, path in zip(lanes, paths[1:], strict=True))
        warnings: list[BeatRenderWarning] = list(compiled.warnings)
        if peak == 0:
            warnings.append('beat_silent')
        if peak > 1:
            warnings.append('beat_peak_over_one')
        for kind, filename, body in [('pattern', 'beat.json', canonical_json(document)),
                ('events', 'events.json', compiled_events_json(compiled))]:
            path = output_dir / filename
            _bytes(path, body)
            artifacts.append(_artifact(render_id, kind, path))
        midi_error = None
        try:
            midi = write_midi(compiled, approximation=request.midi_approximation)
            for kind, filename, body in [('midi', 'beat.mid', midi.data), ('tuning', 'tuning.json', canonical_json(midi.tuning))]:
                path = output_dir / filename
                _bytes(path, body)
                artifacts.append(_artifact(render_id, kind, path))
            warnings.extend(warning for warning in midi.tuning.warnings if warning not in warnings)
        except BeatError as exc:
            if not exc.code.startswith('beat_midi_'):
                raise
            midi_error = exc.code
            warnings.append('beat_midi_unavailable')
        components = [ProvenanceComponent(role='audio', content_origin='unknown', source_id='beat:' + document.id,
            source_sha256=artifacts[0].sha256, engine='beat_cpu_sampler', model_id=document.renderer_version,
            music_evidence=evidence)]
        for asset_id in sorted(samples):
            event = used_events[asset_id]
            components.append(ProvenanceComponent(role='conditioning_reference', content_origin='unknown',
                source_id='sample:' + hashlib.sha256(asset_id.encode()).hexdigest(), source_sha256=event.sample_sha256))
        proof = export_provenance.manifest(render_id, 'track_audio', paths[0], components,
            transformations=['Deterministic CPU sample scheduling; equal-power pan; explicit lane and sample gains; master gain 1; no limiter.',
                'Recorded sample terms are declarations, not verification of source rights.'])
        if document.preset_origin is not None:
            origin = document.preset_origin
            proof.transformations.append('Unverified preset candidate metadata: ' + origin.preset_id +
                '; SHA-256=' + origin.preset_sha256 + '; catalog=' + origin.catalog_version +
                '; review=unreviewed; limitations=' + ', '.join(origin.warnings))
        export_provenance.write(paths[0], proof)
        for format in ('json', 'txt'):
            artifacts.append(_artifact(render_id, 'provenance', export_provenance.path_for(paths[0], format)))
        manifest = BeatRenderManifest(beat_id=document.id, beat_revision=document.revision,
            document_sha256=compiled.document_sha256, render_id=render_id, seed=document.seed,
            renderer_version=compiled.renderer_version, algorithm_version=compiled.algorithm_version,
            frames=cursor, musical_frames=compiled.duration_frames, peak=peak,
            kit_manifest_sha256=compiled.kit_manifest_sha256, events_sha256=compiled.events_sha256,
            artifacts=artifacts, warnings=warnings, midi_error_code=midi_error)
        bundle = output_dir / 'beat-bundle.zip'
        with _contained(bundle.with_suffix('.partial.zip')).open('xb') as bundle_stream, \
                zipfile.ZipFile(bundle_stream, 'w', compression=zipfile.ZIP_STORED) as zipped:
            for artifact in artifacts:
                info = zipfile.ZipInfo(artifact.filename, date_time=(1980, 1, 1, 0, 0, 0))
                with zipped.open(info, 'w', force_zip64=True) as target, (output_dir / artifact.filename).open('rb') as reader:
                    shutil.copyfileobj(reader, target, 1024 * 1024)
            zipped.writestr('manifest.json', canonical_json(manifest))
        bundle.with_suffix('.partial.zip').replace(bundle)
        manifest = manifest.model_copy(update={'artifacts': [*artifacts, _artifact(render_id, 'bundle', bundle)]})
        # Prepared state permits publication retry without running the sampler again.
        _bytes(output_dir / 'prepared-manifest.json', canonical_json(manifest))
        _bytes(output_dir / 'manifest.json', canonical_json(manifest))
        return manifest
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def validate_output(render_id: str, *, prepared: bool = False) -> BeatRenderManifest:
    output = workspace(render_id) / 'output'
    path = _contained(output / ('prepared-manifest.json' if prepared else 'manifest.json'))
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise BeatError('beat_save_failed')
        value = BeatRenderManifest.model_validate_json(path.read_bytes())
        if value.render_id != render_id:
            raise BeatError('beat_save_failed')
        for artifact in value.artifacts:
            resolve_artifact(render_id, artifact)
        mix = next(item for item in value.artifacts if item.kind == 'mix')
        info = sf.info(output / mix.filename)
        if (info.samplerate, info.channels, info.frames, info.subtype) != (RATE, 2, value.frames, 'FLOAT'):
            raise BeatError('beat_save_failed')
        return value
    except (OSError, ValidationError, StopIteration, RuntimeError) as exc:
        raise BeatError('beat_save_failed') from exc


def resolve_artifact(render_id: str, artifact: BeatArtifact) -> Path:
    """Confine and hash one published artifact; no arbitrary serving path."""
    path = _contained(workspace(render_id) / 'output' / artifact.filename)
    try:
        if path.stat().st_size != artifact.byte_size or export_provenance.digest(path) != artifact.sha256:
            raise BeatError('beat_sample_changed')
    except (OSError, export_provenance.ProvenanceError) as exc:
        raise BeatError('beat_sample_changed') from exc
    return path


def recover_manifest(render_id: str) -> BeatRenderManifest:
    value = validate_output(render_id, prepared=True)
    _bytes(workspace(render_id) / 'output' / 'manifest.json', canonical_json(value))
    return value


class BeatWorkerInput(BeatContract):
    render_id: BeatId
    document: BeatDocument
    request: BeatJobRequest
    kit: BeatInstrumentManifest


class BeatWorkerResult(BeatContract):
    render_id: BeatId
    manifest_sha256: BeatSha256


class BeatWorkerFailure(BeatContract):
    error_code: BeatErrorCode


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--files-dir', type=Path, required=True)
    arguments = parser.parse_args()
    try:
        # Private supervisor argument, never a request-schema path. The selected
        # library config can override environment defaults, so inherit the actual
        # parent's root rather than re-resolving a potentially different library.
        db.FILES_DIR = arguments.files_dir.resolve()
        source = arguments.request
        if source.stat().st_size > 32 * 1024 * 1024:
            raise BeatError('beat_pattern_invalid')
        value = BeatWorkerInput.model_validate_json(source.read_bytes())
        if source != workspace(value.render_id) / 'request.json':
            raise BeatError('beat_storage_unavailable')
        def progress(done: int, total: int) -> None:
            if done % (BLOCK_FRAMES * 16) == 0 or done == total:
                # Exact total includes sample tails and is known only at close.
                write_object(workspace(value.render_id) / 'progress.json', {'done': done, 'total': None})
        manifest = render(value.document, value.request, value.kit, value.render_id,
            workspace(value.render_id) / 'output', progress=progress)
        result = BeatWorkerResult(render_id=value.render_id,
            manifest_sha256=hashlib.sha256(canonical_json(manifest)).hexdigest())
        print(result.model_dump_json(), flush=True)
        return 0
    except BeatError as exc:
        print(json.dumps({'error_code': exc.code}), flush=True)
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        code = 'beat_disk_full' if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else 'beat_worker_failed'
        print(json.dumps({'error_code': code}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(_main())
