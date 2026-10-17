"""Report-scoped regional candidates; asset availability never implies musical review."""
from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, Field, model_validator

from .beat_contracts import (BeatContract, BeatDocument, BeatId, BeatIdentifier, BeatMeter,
    BeatPattern, BeatPresetWarning, BeatRole, BeatSha256)

PresetRegion = Literal['china', 'japan', 'korea', 'indonesia', 'india', 'thailand']
PresetReviewStatus = Literal['unreviewed']
PresetWarning: TypeAlias = BeatPresetWarning
PresetAvailability = Literal['available', 'pack_missing', 'license_unresolved', 'unsupported_articulation',
    'tuning_unresolved', 'timing_unsupported']
PresetErrorCode = Literal['beat_preset_invalid', 'beat_preset_not_found', 'beat_preset_pack_missing',
    'beat_preset_license_unresolved', 'beat_preset_unsupported_articulation',
    'beat_preset_tuning_unresolved', 'beat_preset_timing_unsupported', 'beat_preset_catalog_invalid']
DegreeCents = Annotated[float, Field(ge=0, lt=1200)]


def _degrees(values: list[float]) -> bool:
    return values[0] == 0 and all(right > left for left, right in zip(values, values[1:]))


class BeatPresetScale(BeatContract):
    id: BeatIdentifier
    title: str = Field(min_length=1, max_length=120)
    region: PresetRegion
    degrees_cents: list[DegreeCents] = Field(min_length=1, max_length=24)
    notes: str = Field(min_length=1, max_length=2000)
    report_section: Literal['6.2'] = '6.2'
    review_status: PresetReviewStatus = 'unreviewed'

    @model_validator(mode='after')
    def ordered(self) -> Self:
        if not _degrees(self.degrees_cents):
            raise ValueError('beat_preset_invalid')
        return self


class BeatPresetTuning(BeatContract):
    id: BeatIdentifier
    title: str = Field(min_length=1, max_length=120)
    kind: Literal['twelve_tet_candidate', 'sample_specific_required', 'equal_division_experiment']
    degrees_cents: list[DegreeCents] | None = Field(default=None, min_length=1, max_length=24)
    reference_hz: float = Field(default=440, ge=400, le=480)
    notes: str = Field(min_length=1, max_length=2000)
    review_status: PresetReviewStatus = 'unreviewed'

    @model_validator(mode='after')
    def truthful_measurement(self) -> Self:
        if ((self.kind == 'sample_specific_required') != (self.degrees_cents is None)
                or self.degrees_cents is not None and not _degrees(self.degrees_cents)):
            raise ValueError('beat_preset_invalid')
        return self


class BeatRhythmMarker(BeatContract):
    index: int = Field(ge=0, le=1023)
    label: str = Field(min_length=1, max_length=80)


class BeatRhythmDefinition(BeatContract):
    id: BeatIdentifier
    title: str = Field(min_length=1, max_length=120)
    region: PresetRegion
    meter: BeatMeter
    bars: int = Field(ge=1, le=4)
    steps_per_quarter: Literal[2, 3, 4, 6, 8]
    grouping_steps: list[Annotated[int, Field(ge=1, le=1024)]] = Field(min_length=1, max_length=64)
    markers: list[BeatRhythmMarker] = Field(default_factory=list, max_length=64)
    bpm_min: float = Field(ge=20, le=300)
    bpm_max: float = Field(ge=20, le=300)
    notes: str = Field(min_length=1, max_length=2000)
    report_section: Literal['6.3'] = '6.3'
    review_status: PresetReviewStatus = 'unreviewed'

    @model_validator(mode='after')
    def coherent_cycle(self) -> Self:
        count = BeatPattern.grid_size(self.meter, self.steps_per_quarter) * self.bars
        if (self.bpm_min > self.bpm_max or sum(self.grouping_steps) != count
                or len({item.index for item in self.markers}) != len(self.markers)
                or any(item.index >= count for item in self.markers)):
            raise ValueError('beat_preset_invalid')
        return self


class BeatPresetHit(BeatContract):
    index: int = Field(ge=0, le=1023)
    velocity: int = Field(default=90, ge=1, le=127)
    degree: int | None = Field(default=None, ge=0, le=23)
    octave: int = Field(default=0, ge=-2, le=2)
    gate_steps: float = Field(default=1, ge=0.05, le=8)
    articulation_id: BeatIdentifier | None = None


class BeatPresetLane(BeatContract):
    id: BeatIdentifier
    instrument_id: BeatIdentifier
    label: str = Field(min_length=1, max_length=80)
    role: BeatRole
    hits: list[BeatPresetHit] = Field(default_factory=list, max_length=1024)

    @model_validator(mode='after')
    def coherent_notes(self) -> Self:
        if (len({hit.index for hit in self.hits}) != len(self.hits)
                or any((hit.degree is not None) == (self.role == 'drum') for hit in self.hits)):
            raise ValueError('beat_preset_invalid')
        return self


class BeatPresetDefinition(BeatContract):
    id: BeatIdentifier
    version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=120)
    region: PresetRegion
    tradition: str = Field(min_length=1, max_length=120)
    category: Literal['traditional', 'fusion', 'modern_pop']
    bpm_default: float = Field(ge=20, le=300)
    bpm_min: float = Field(ge=20, le=300)
    bpm_max: float = Field(ge=20, le=300)
    meter: BeatMeter
    bars: int = Field(ge=1, le=4)
    steps_per_quarter: Literal[2, 3, 4, 6, 8]
    scale_id: BeatIdentifier | None = None
    tuning_id: BeatIdentifier
    rhythm_id: BeatIdentifier | None = None
    lanes: list[BeatPresetLane] = Field(min_length=1, max_length=16)
    swing: float = Field(default=0.5, ge=0.5, le=0.75)
    timing: Literal['candidate_grid', 'fixed_grid_free_time_approximation',
        'tempo_envelope_required', 'cycle_progression_required'] = 'candidate_grid'
    prompt_tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=16)
    notes: str = Field(min_length=1, max_length=3000)
    report_section: Literal['6.4'] = '6.4'
    report_number: int = Field(ge=1, le=24)
    review_status: PresetReviewStatus = 'unreviewed'

    @model_validator(mode='after')
    def coherent_candidate(self) -> Self:
        count = BeatPattern.grid_size(self.meter, self.steps_per_quarter) * self.bars
        if (not self.bpm_min <= self.bpm_default <= self.bpm_max
                or len({lane.id for lane in self.lanes}) != len(self.lanes)
                or any(hit.index >= count for lane in self.lanes for hit in lane.hits)
                or self.swing != 0.5 and self.steps_per_quarter not in (4, 8)):
            raise ValueError('beat_preset_invalid')
        return self


class BeatPresetDefinitions(BeatContract):
    schema_version: Literal[1] = 1
    version: BeatIdentifier
    presets: list[BeatPresetDefinition] = Field(min_length=24, max_length=24)
    scales: list[BeatPresetScale] = Field(min_length=1, max_length=64)
    tunings: list[BeatPresetTuning] = Field(min_length=1, max_length=16)
    rhythms: list[BeatRhythmDefinition] = Field(min_length=15, max_length=15)

    @model_validator(mode='after')
    def resolved_catalog(self) -> Self:
        for rows in (self.presets, self.scales, self.tunings, self.rhythms):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError('beat_preset_catalog_invalid')
        scales = {scale.id: scale for scale in self.scales}
        tunings = {tuning.id: tuning for tuning in self.tunings}
        rhythms = {rhythm.id: rhythm for rhythm in self.rhythms}
        if {preset.report_number for preset in self.presets} != set(range(1, 25)):
            raise ValueError('beat_preset_catalog_invalid')
        for preset in self.presets:
            scale = scales.get(preset.scale_id) if preset.scale_id else None
            rhythm = rhythms.get(preset.rhythm_id) if preset.rhythm_id else None
            if (preset.tuning_id not in tunings or preset.scale_id is not None and scale is None
                    or scale is not None and scale.region != preset.region
                    or preset.rhythm_id is not None and rhythm is None
                    or rhythm is not None and (rhythm.region != preset.region or rhythm.meter != preset.meter
                        or rhythm.bars != preset.bars or rhythm.steps_per_quarter != preset.steps_per_quarter)
                    or any(hit.degree is not None and (scale is None or hit.degree >= len(scale.degrees_cents))
                        for lane in preset.lanes for hit in lane.hits)):
                raise ValueError('beat_preset_catalog_invalid')
        return self


class BeatPresetInfo(BeatContract):
    preset: BeatPresetDefinition
    preset_sha256: BeatSha256
    availability: PresetAvailability
    compatible_kit_ids: list[BeatIdentifier] = Field(default_factory=list, max_length=100)
    missing_instrument_ids: list[BeatIdentifier] = Field(default_factory=list, max_length=16)
    reasons: list[PresetAvailability] = Field(default_factory=list, max_length=5)


class BeatPresetCatalog(BeatContract):
    schema_version: Literal[1] = 1
    version: BeatIdentifier
    presets: list[BeatPresetInfo] = Field(min_length=24, max_length=24)
    scales: list[BeatPresetScale] = Field(min_length=1, max_length=64)
    tunings: list[BeatPresetTuning] = Field(min_length=1, max_length=16)
    rhythms: list[BeatRhythmDefinition] = Field(min_length=15, max_length=15)


class BeatPresetCompileRequest(BeatContract):
    preset_id: BeatIdentifier
    kit_id: BeatIdentifier
    document_id: BeatId
    seed: int = Field(ge=0, le=2147483647)
    bpm: float | None = Field(default=None, ge=20, le=300)
    tonic_midi: int = Field(default=60, ge=24, le=96)


class BeatPresetCompilation(BeatContract):
    preset_id: BeatIdentifier
    preset_sha256: BeatSha256
    catalog_version: BeatIdentifier
    review_status: PresetReviewStatus = 'unreviewed'
    warnings: list[PresetWarning] = Field(min_length=1, max_length=4)
    document: BeatDocument


BEAT_PRESET_CLIENT_MODELS: list[type[BaseModel]] = [BeatPresetCatalog, BeatPresetCompileRequest, BeatPresetCompilation]
