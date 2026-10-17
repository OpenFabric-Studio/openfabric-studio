"""Bounded beat authoring and immutable instrument identities, without I/O."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.beat_contracts import (BeatDocument, BeatInstrument, BeatInstrumentManifest, BeatLane,
    BeatMeter, BeatPattern, BeatPitchPoint, BeatRenderRequest, BeatSampleRegion, BeatScale,
    BeatStep, PatternPlacement, manifest_sha256)
from app.module_contracts import ModuleLicenseDeclaration


def manifest_fixture(*, pitched: bool = False) -> BeatInstrumentManifest:
    terms = ModuleLicenseDeclaration(component_id='synthetic-test', name='Synthetic test fixture', scope='samples',
        status='unknown', notes='No distribution permission inferred.', reviewed_at='2026-10-17')
    region = BeatSampleRegion(region_id='sample', asset_id='sample', sha256='b' * 64, byte_size=200,
        sample_rate_hz=8000, channels=1, frames=800, root_note_midi=60, terms=terms,
        attribution='Synthetic test signal')
    instrument = BeatInstrument(instrument_id='fixture', label='Fixture', role='pitched' if pitched else 'drum',
        pitched=pitched, supports_pitch_curve=pitched, midi_note=60 if pitched else 36, regions=[region])
    return BeatInstrumentManifest(kit_id='fixture-kit', revision='1', instruments=[instrument])


def document_fixture(manifest: BeatInstrumentManifest | None = None) -> BeatDocument:
    kit = manifest or manifest_fixture()
    role = kit.instruments[0].role
    lanes = [BeatLane(lane_id=f'lane-{index}', label=f'Lane {index}', instrument_id='fixture', role=role,
        steps=[BeatStep(index=0)] if index == 0 else []) for index in range(8)]
    return BeatDocument(id='a' * 32, revision=0, title='Fixture', bpm=120, seed=4471,
        kit_id=kit.kit_id, kit_manifest_sha256=manifest_sha256(kit),
        patterns=[BeatPattern(id='A', meter=BeatMeter(numerator=4, denominator=4), lanes=lanes)],
        chain=[PatternPlacement(pattern_id='A')])


class BeatContractsTests(unittest.TestCase):
    def test_preset_origin_retains_limitations_and_cannot_claim_review(self) -> None:
        raw = document_fixture().model_dump()
        origin = {'preset_id': 'candidate', 'preset_sha256': 'a' * 64, 'catalog_version': 'catalog-v1',
            'review_status': 'unreviewed', 'warnings': ['candidate_arrangement_unreviewed']}
        value = BeatDocument.model_validate({**raw, 'preset_origin': origin})
        self.assertEqual(value.preset_origin.preset_id if value.preset_origin else None, 'candidate')
        with self.assertRaises(ValidationError):
            BeatDocument.model_validate({**raw, 'preset_origin': {**origin, 'review_status': 'verified'}})

    def test_compound_and_asymmetric_grids_use_quarter_note_clock(self) -> None:
        for numerator, expected in ((12, 24), (9, 18), (10, 20), (18, 36)):
            meter = BeatMeter(numerator=numerator, denominator=8)
            self.assertEqual(BeatPattern.grid_size(meter, 4), expected)
        self.assertEqual(BeatMeter(numerator=12, denominator=8).quarters_per_bar, 6)
        with self.assertRaises(ValueError):
            BeatPattern.grid_size(BeatMeter(numerator=1, denominator=16), 2)

    def test_step_bounds_and_strict_numbers(self) -> None:
        for patch in ({'probability': float('nan')}, {'probability': float('inf')}, {'probability': -0.1},
                      {'ratchet': 5}, {'index': '0'}, {'index': True}, {'offset_steps': 0.51},
                      {'note_midi': 128}, {'cents': 101}, {'gate_steps': 0.04}, {'velocity': 0}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatStep.model_validate({'index': 0, **patch})

    def test_curve_knots_require_both_endpoints_and_increasing_positions(self) -> None:
        for points in ([{'position': 0.2, 'cents': 0}, {'position': 1, 'cents': 0}],
                       [{'position': 0, 'cents': 0}, {'position': 0, 'cents': 5}, {'position': 1, 'cents': 0}],
                       [{'position': 0, 'cents': 2401}, {'position': 1, 'cents': 0}]):
            with self.subTest(points=points), self.assertRaises(ValidationError):
                BeatStep.model_validate({'index': 0, 'pitch_curve': points})
        self.assertEqual(len(BeatStep(index=0, pitch_curve=[BeatPitchPoint(position=0, cents=0),
            BeatPitchPoint(position=1, cents=20)]).pitch_curve), 2)

    def test_pattern_lane_count_ids_indexes_and_grid_are_bounded(self) -> None:
        raw = document_fixture().patterns[0].model_dump(mode='json')
        for lanes in (raw['lanes'][:7], raw['lanes'] + raw['lanes'] + [raw['lanes'][0]],
                      [raw['lanes'][0], raw['lanes'][0], *raw['lanes'][2:]]):
            with self.subTest(lanes=len(lanes)), self.assertRaises(ValidationError):
                BeatPattern.model_validate({**raw, 'lanes': lanes})
        for patch in ({'bars': 5}, {'steps_per_quarter': 5},
                      {'meter': {'numerator': 1, 'denominator': 16}, 'steps_per_quarter': 2}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatPattern.model_validate({**raw, **patch})
        lane = raw['lanes'][0]
        for steps in ([{'index': 0}, {'index': 0}], [{'index': 16}]):
            with self.subTest(steps=steps), self.assertRaises(ValidationError):
                BeatPattern.model_validate({**raw, 'lanes': [{**lane, 'steps': steps}, *raw['lanes'][1:]]})

    def test_document_references_same_ordered_lane_identity_and_chain_budgets(self) -> None:
        raw = document_fixture().model_dump(mode='json')
        pattern = raw['patterns'][0]
        for patch in ({'bpm': '120'}, {'bpm': float('inf')}, {'chain': [{'pattern_id': 'B'}]},
                      {'patterns': [pattern, pattern]},
                      {'patterns': [pattern, {**pattern, 'id': 'B', 'lanes': list(reversed(pattern['lanes']))}]},
                      {'bpm': 20, 'chain': [{'pattern_id': 'A', 'repeats': 64}]}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatDocument.model_validate({**raw, **patch})
        dense = {**pattern, 'bars': 4, 'steps_per_quarter': 8, 'lanes': [
            {**lane, 'lane_id': f'lane-{lane_index}', 'steps': [{'index': index, 'ratchet': 4} for index in range(128)]}
            for lane_index, lane in enumerate(pattern['lanes'] * 2)]}
        with self.assertRaises(ValidationError):
            BeatDocument.model_validate({**raw, 'bpm': 300, 'patterns': [dense], 'chain': [{'pattern_id': 'A', 'repeats': 64}]})

    def test_scales_preserve_non_western_degrees_without_key_labels(self) -> None:
        scale = BeatScale(scale_id='fixture-scale', root_note_midi=60, degrees_cents=[0, 150, 350, 550, 700, 850, 1050])
        self.assertEqual(scale.degrees_cents[1], 150)
        for patch in ({'degrees_cents': [0, 150, 150]}, {'degrees_cents': [100, 0]},
                      {'degrees_cents': [0, 1200]}, {'reference_hz': 481}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatScale.model_validate({**scale.model_dump(), **patch})

    def test_preview_occurrence_is_explicit_and_full_has_no_preview_coordinates(self) -> None:
        request = BeatRenderRequest(beat_id='a' * 32, expected_revision=0, mode='preview',
            preview_pattern_id='A', occurrence_index=3, submission_key='b' * 32)
        self.assertEqual(request.occurrence_index, 3)
        for patch in ({'occurrence_index': 4096}, {'preview_pattern_id': None}, {'mode': 'full'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatRenderRequest.model_validate({**request.model_dump(), **patch})

    def test_instrument_metadata_bounds_unknown_terms_and_region_ambiguity(self) -> None:
        raw = manifest_fixture().model_dump(mode='json')
        instrument = raw['instruments'][0]
        region = instrument['regions'][0]
        self.assertEqual(region['terms']['restriction'], 'unknown')
        for patch in ({'asset_id': '../private.wav'}, {'sample_rate_hz': 7999}, {'channels': 3},
                      {'frames': 240001}, {'terms': {**region['terms'], 'scope': 'code'}}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatSampleRegion.model_validate({**region, **patch})
        with self.assertRaises(ValidationError):
            BeatInstrument.model_validate({**instrument, 'regions': [region, {**region, 'region_id': 'other'}]})
        with self.assertRaises(ValidationError):
            BeatInstrumentManifest.model_validate({**raw, 'instruments': [instrument, instrument]})

    def test_literal_versions_and_sample_channels_reject_booleans(self) -> None:
        kit = manifest_fixture().model_dump(mode='json')
        document = document_fixture().model_dump(mode='json')
        with self.assertRaises(ValidationError):
            BeatDocument.model_validate({**document, 'schema_version': True})
        with self.assertRaises(ValidationError):
            BeatSampleRegion.model_validate({**kit['instruments'][0]['regions'][0], 'channels': True})
        for value in (4.0, '4'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                BeatMeter.model_validate({'numerator': 4, 'denominator': value})

    def test_kit_total_decoded_budget_counts_unique_assets_and_requires_scoped_terms(self) -> None:
        raw = manifest_fixture().model_dump(mode='json')
        instrument = raw['instruments'][0]
        region = instrument['regions'][0]
        regions = [{**region, 'region_id': f'region-{index}', 'asset_id': f'asset-{index}',
            'sample_rate_hz': 192000, 'frames': 5760000, 'channels': 2, 'note_min': index, 'note_max': index}
            for index in range(12)]
        with self.assertRaises(ValidationError):
            BeatInstrumentManifest.model_validate({**raw, 'instruments': [{**instrument, 'regions': regions}]})
        conflicting = [{**region, 'note_max': 60},
            {**region, 'region_id': 'other', 'note_min': 61, 'byte_size': region['byte_size'] + 1}]
        with self.assertRaises(ValidationError):
            BeatInstrumentManifest.model_validate({**raw, 'instruments': [{**instrument, 'regions': conflicting}]})


if __name__ == '__main__':
    unittest.main()
