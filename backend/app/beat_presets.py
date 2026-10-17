"""Bounded report candidates compiled only against an explicitly supplied sample kit."""
from __future__ import annotations

from collections.abc import Sequence
import hashlib
import math
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .beat_compile import compile_events
from .beat_contracts import (BeatDocument, BeatError, BeatInstrumentManifest, BeatLane, BeatPattern,
    BeatPresetOrigin, BeatScale, BeatStep, PatternPlacement, canonical_json, manifest_sha256)
from .beat_preset_contracts import (BeatPresetCatalog, BeatPresetCompilation, BeatPresetCompileRequest,
    BeatPresetDefinition, BeatPresetDefinitions, BeatPresetInfo, PresetAvailability, PresetErrorCode, PresetWarning)
from .contracts import JsonObject

_JSON = TypeAdapter(JsonObject)
_DATA = Path(__file__).with_name('data')
_MAX_CATALOG_BYTES = 1024 * 1024


class BeatPresetError(Exception):
    def __init__(self, code: PresetErrorCode) -> None:
        self.code = code
        super().__init__(code)


def load_definitions() -> BeatPresetDefinitions:
    """Read app-owned bounded JSON; never resolve a request-supplied path or execute data."""
    body: JsonObject = {}
    try:
        for filename in ('beat_presets.json', 'beat_scales.json', 'beat_rhythms.json', 'beat_tunings.json'):
            path = _DATA / filename
            if path.is_symlink() or path.stat().st_size > _MAX_CATALOG_BYTES:
                raise BeatPresetError('beat_preset_catalog_invalid')
            with path.open('rb') as handle:
                raw = handle.read(_MAX_CATALOG_BYTES + 1)
            if len(raw) > _MAX_CATALOG_BYTES:
                raise BeatPresetError('beat_preset_catalog_invalid')
            part = _JSON.validate_json(raw)
            if body.keys() & part.keys():
                raise BeatPresetError('beat_preset_catalog_invalid')
            body.update(part)
        return BeatPresetDefinitions.model_validate(body)
    except (OSError, ValueError, ValidationError) as exc:
        raise BeatPresetError('beat_preset_catalog_invalid') from exc


def _preset_hash(preset: BeatPresetDefinition, definitions: BeatPresetDefinitions) -> str:
    digest = hashlib.sha256(definitions.version.encode('ascii'))
    digest.update(canonical_json(preset))
    scale = next((item for item in definitions.scales if item.id == preset.scale_id), None)
    tuning = next(item for item in definitions.tunings if item.id == preset.tuning_id)
    rhythm = next((item for item in definitions.rhythms if item.id == preset.rhythm_id), None)
    for item in (scale, tuning, rhythm):
        if item is not None:
            digest.update(canonical_json(item))
    return digest.hexdigest()


def _admission(preset: BeatPresetDefinition, definitions: BeatPresetDefinitions,
               manifest: BeatInstrumentManifest | None, *, tonic_midi: int = 60) -> tuple[list[PresetAvailability], list[str]]:
    instruments = {item.instrument_id: item for item in manifest.instruments} if manifest is not None else {}
    missing = sorted({lane.instrument_id for lane in preset.lanes if lane.instrument_id not in instruments
        or instruments[lane.instrument_id].role != lane.role
        or instruments[lane.instrument_id].pitched != (lane.role != 'drum')})
    reasons: list[PresetAvailability] = ['pack_missing'] if missing else []
    if not missing:
        unresolved = unsupported = False
        scale = next((item for item in definitions.scales if item.id == preset.scale_id), None)
        for lane in preset.lanes:
            instrument = instruments[lane.instrument_id]
            for region in instrument.regions:
                terms = region.terms
                if (terms.status != 'declared' or terms.redistribution != 'declared_allowed'
                        or not any(identity.sha256 == region.sha256 for identity in terms.identities)
                        or not any(evidence.kind == 'license_file' and evidence.sha256 is not None
                            for evidence in terms.evidence)):
                    unresolved = True
            for hit in lane.hits:
                # Every seeded motif rotation must fit the declared sample mapping, without pitch clamping.
                notes = [math.floor((tonic_midi + hit.octave * 12) + degree / 100 + 0.5)
                    for degree in scale.degrees_cents] if hit.degree is not None and scale else [instrument.midi_note]
                if any(not 0 <= note <= 127 or not any(region.articulation_id == hit.articulation_id
                        and region.note_min <= note <= region.note_max
                        and region.velocity_min <= hit.velocity <= region.velocity_max for region in instrument.regions)
                        for note in notes):
                    unsupported = True
        if unresolved:
            reasons.append('license_unresolved')
        if unsupported:
            reasons.append('unsupported_articulation')
    tuning = next(item for item in definitions.tunings if item.id == preset.tuning_id)
    if tuning.kind == 'sample_specific_required':
        reasons.append('tuning_unresolved')
    if preset.timing in ('tempo_envelope_required', 'cycle_progression_required'):
        reasons.append('timing_unsupported')
    return reasons, missing


def catalog(*, manifests: Sequence[BeatInstrumentManifest] = ()) -> BeatPresetCatalog:
    """Availability describes supplied kits only; it never changes the review record."""
    if len(manifests) > 100 or len({item.kit_id for item in manifests}) != len(manifests):
        raise BeatPresetError('beat_preset_invalid')
    definitions = load_definitions()
    rows: list[BeatPresetInfo] = []
    for preset in definitions.presets:
        compatible: list[str] = []
        best, missing = _admission(preset, definitions, None)
        for manifest in sorted(manifests, key=lambda item: item.kit_id):
            reasons, absent = _admission(preset, definitions, manifest)
            if not reasons:
                compatible.append(manifest.kit_id)
            if (len(absent), len(reasons)) < (len(missing), len(best)):
                best, missing = reasons, absent
        rows.append(BeatPresetInfo(preset=preset, preset_sha256=_preset_hash(preset, definitions),
            availability='available' if compatible else best[0], compatible_kit_ids=compatible,
            missing_instrument_ids=[] if compatible else missing, reasons=[] if compatible else best))
    return BeatPresetCatalog(version=definitions.version, presets=rows, scales=definitions.scales,
        tunings=definitions.tunings, rhythms=definitions.rhythms)


def compile_preset(request: BeatPresetCompileRequest, *, manifest: BeatInstrumentManifest | None) -> BeatPresetCompilation:
    """Produce a candidate document; callers own explicit save/render and real sample verification."""
    definitions = load_definitions()
    preset = next((item for item in definitions.presets if item.id == request.preset_id), None)
    if preset is None:
        raise BeatPresetError('beat_preset_not_found')
    reasons, _ = _admission(preset, definitions, manifest, tonic_midi=request.tonic_midi)
    if reasons:
        codes: dict[PresetAvailability, PresetErrorCode] = {
            'available': 'beat_preset_invalid', 'pack_missing': 'beat_preset_pack_missing',
            'license_unresolved': 'beat_preset_license_unresolved',
            'unsupported_articulation': 'beat_preset_unsupported_articulation',
            'tuning_unresolved': 'beat_preset_tuning_unresolved', 'timing_unsupported': 'beat_preset_timing_unsupported'}
        raise BeatPresetError(codes[reasons[0]])
    if manifest is None or request.kit_id != manifest.kit_id:
        raise BeatPresetError('beat_preset_pack_missing')
    bpm = preset.bpm_default if request.bpm is None else request.bpm
    if not preset.bpm_min <= bpm <= preset.bpm_max:
        raise BeatPresetError('beat_preset_invalid')
    scale = next((item for item in definitions.scales if item.id == preset.scale_id), None)
    tuning = next(item for item in definitions.tunings if item.id == preset.tuning_id)
    lanes: list[BeatLane] = []
    for lane in preset.lanes:
        steps: list[BeatStep] = []
        motif_key = f'preset-motif-v1:{request.seed}:{preset.id}:{lane.id}'.encode('ascii')
        motif_shift = int.from_bytes(hashlib.sha256(motif_key).digest()[:4], 'big')
        for hit in lane.hits:
            note: int | None = None
            cents = 0.0
            if hit.degree is not None:
                if scale is None:
                    raise BeatPresetError('beat_preset_catalog_invalid')
                degree = (hit.degree + motif_shift) % len(scale.degrees_cents)
                total = (request.tonic_midi + hit.octave * 12) * 100 + scale.degrees_cents[degree]
                note = math.floor(total / 100 + 0.5)
                cents = total - note * 100
                if not 0 <= note <= 127:
                    raise BeatPresetError('beat_preset_invalid')
            steps.append(BeatStep(index=hit.index, velocity=hit.velocity, note_midi=note, cents=cents,
                gate_steps=hit.gate_steps, articulation_id=hit.articulation_id))
        lanes.append(BeatLane(lane_id=lane.id, label=lane.label, instrument_id=lane.instrument_id,
            role=lane.role, steps=steps))
    # The common editor requires 8 lanes; extra empty lanes reuse an explicit admitted instrument.
    for index in range(len(lanes), 8):
        first = lanes[0]
        lanes.append(BeatLane(lane_id=f'unused-{index + 1}', label=f'Empty part {index + 1}',
            instrument_id=first.instrument_id, role=first.role))
    document = BeatDocument(id=request.document_id, revision=0, title=preset.title, bpm=bpm,
        seed=request.seed, kit_id=manifest.kit_id, kit_manifest_sha256=manifest_sha256(manifest),
        patterns=[BeatPattern(id='A', bars=preset.bars, meter=preset.meter,
            steps_per_quarter=preset.steps_per_quarter, lanes=lanes)],
        chain=[PatternPlacement(pattern_id='A', section_label='Unreviewed candidate')], global_swing=preset.swing,
        scale=BeatScale(scale_id=scale.id, root_note_midi=request.tonic_midi,
            degrees_cents=scale.degrees_cents, reference_hz=tuning.reference_hz) if scale is not None else None)
    try:
        compile_events(document, 48000, manifest)
    except BeatError as exc:
        raise BeatPresetError('beat_preset_unsupported_articulation') from exc
    warnings: list[PresetWarning] = ['candidate_arrangement_unreviewed']
    if scale is not None:
        warnings.append('scale_is_report_approximation')
    if preset.timing == 'fixed_grid_free_time_approximation':
        warnings.append('fixed_grid_free_time_approximation')
    if preset.id == 'raga_dusk':
        warnings.append('raga_phrase_rules_not_implemented')
    checksum = _preset_hash(preset, definitions)
    origin = BeatPresetOrigin(preset_id=preset.id, preset_sha256=checksum,
        catalog_version=definitions.version, warnings=warnings)
    document = BeatDocument.model_validate({**document.model_dump(), 'preset_origin': origin.model_dump()})
    return BeatPresetCompilation(preset_id=preset.id, preset_sha256=checksum,
        catalog_version=definitions.version, warnings=warnings, document=document)
