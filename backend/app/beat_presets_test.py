"""Report inventory, honest asset admission and deterministic BeatDocument handoff."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.beat_compile import compile_events
from app.beat_contracts import BeatInstrumentManifest, canonical_json, manifest_sha256
from app.beat_contracts_test import manifest_fixture
from app.beat_preset_contracts import BeatPresetCompileRequest
from app.beat_presets import BeatPresetError, catalog, compile_preset, load_definitions


def compatible_kit(preset_id: str = 'lofi_kyoto') -> BeatInstrumentManifest:
    preset = next(item for item in load_definitions().presets if item.id == preset_id)
    base = manifest_fixture(pitched=True).model_dump()['instruments'][0]
    instruments = []
    for lane in preset.lanes:
        if any(item['instrument_id'] == lane.instrument_id for item in instruments):
            continue
        region = {**base['regions'][0], 'region_id': lane.instrument_id,
            'asset_id': lane.instrument_id, 'articulation_id': None}
        region['terms'] = {**region['terms'], 'status': 'declared', 'declared_license': 'CC0-1.0',
            'source_url': 'https://example.invalid/test-fixture-license', 'redistribution': 'declared_allowed',
            'evidence': [{'kind': 'license_file', 'source_url': 'https://example.invalid/test-fixture-license',
                'sha256': 'c' * 64, 'observed_at': '2026-10-17', 'reviewer': 'Automated synthetic fixture'}],
            'identities': [{'sha256': region['sha256']}]}
        instruments.append({**base, 'instrument_id': lane.instrument_id, 'label': lane.label,
            'role': lane.role, 'pitched': lane.role != 'drum', 'supports_pitch_curve': lane.role != 'drum',
            'regions': [region]})
    return BeatInstrumentManifest.model_validate({'kit_id': 'private-compatible-fixture', 'revision': 'fixture-v1',
        'instruments': instruments})


class BeatPresetsTests(unittest.TestCase):
    def request(self, preset_id: str = 'lofi_kyoto', **changes: object) -> BeatPresetCompileRequest:
        return BeatPresetCompileRequest.model_validate({'preset_id': preset_id,
            'kit_id': 'private-compatible-fixture', 'document_id': 'a' * 32, 'seed': 4471, **changes})

    def test_exact_report_inventory_remains_provisional_without_bundled_assets(self) -> None:
        result = catalog()
        self.assertEqual((len(result.presets), len(result.rhythms)), (24, 15))
        self.assertEqual(len({item.preset.id for item in result.presets}), 24)
        self.assertEqual({item.preset.review_status for item in result.presets}, {'unreviewed'})
        self.assertEqual({item.availability for item in result.presets}, {'pack_missing'})
        self.assertTrue(all(item.preset.report_section == '6.4' for item in result.presets))
        self.assertTrue(all(item.report_section == '6.3' for item in result.rhythms))

    def test_supplied_compatible_pack_compiles_repeatable_real_document(self) -> None:
        kit = compatible_kit()
        result = compile_preset(self.request(), manifest=kit)
        self.assertEqual(result.document.kit_manifest_sha256, manifest_sha256(kit))
        self.assertEqual(result.document.kit_id, kit.kit_id)
        self.assertEqual(result.review_status, 'unreviewed')
        self.assertIn('candidate_arrangement_unreviewed', result.warnings)
        self.assertIsNotNone(result.document.preset_origin)
        assert result.document.preset_origin is not None
        self.assertEqual(result.document.preset_origin.preset_sha256, result.preset_sha256)
        self.assertEqual(result.document.preset_origin.warnings, result.warnings)
        self.assertEqual(canonical_json(result), canonical_json(compile_preset(self.request(), manifest=kit)))
        compiled = compile_events(result.document, 48000, kit)
        self.assertTrue(compiled.events)
        scale = result.document.scale
        self.assertIsNotNone(scale)
        assert scale is not None
        for event in compiled.events:
            if event.pitched:
                pitch_class = ((event.note_midi - scale.root_note_midi) * 100 + event.cents) % 1200
                self.assertIn(pitch_class, scale.degrees_cents)
        projection = catalog(manifests=[kit])
        row = next(item for item in projection.presets if item.preset.id == 'lofi_kyoto')
        self.assertEqual((row.availability, row.compatible_kit_ids), ('available', [kit.kit_id]))

    def test_missing_pack_wrong_instrument_and_unresolved_terms_never_substitute(self) -> None:
        for kit in (None, manifest_fixture()):
            with self.subTest(kit=kit), self.assertRaises(BeatPresetError) as error:
                compile_preset(self.request(), manifest=kit)
            self.assertEqual(error.exception.code, 'beat_preset_pack_missing')
        kit = compatible_kit()
        value = kit.model_dump()
        for instrument in value['instruments']:
            instrument['regions'][0]['terms']['redistribution'] = 'unknown'
        unresolved = BeatInstrumentManifest.model_validate(value)
        with self.assertRaises(BeatPresetError) as error:
            compile_preset(self.request(), manifest=unresolved)
        self.assertEqual(error.exception.code, 'beat_preset_license_unresolved')

    def test_unmeasured_ensemble_tuning_and_unrepresented_timing_are_explicitly_blocked(self) -> None:
        for preset_id, code in (('javanese_lancaran', 'beat_preset_tuning_unresolved'),
                                ('lion_dance', 'beat_preset_timing_unsupported')):
            with self.subTest(preset_id=preset_id), self.assertRaises(BeatPresetError) as error:
                compile_preset(self.request(preset_id), manifest=compatible_kit(preset_id))
            self.assertEqual(error.exception.code, code)

    def test_bpm_range_negative_octave_and_catalog_integrity_are_validated(self) -> None:
        kit = compatible_kit()
        with self.assertRaises(BeatPresetError) as error:
            compile_preset(self.request(bpm=299.0), manifest=kit)
        self.assertEqual(error.exception.code, 'beat_preset_invalid')
        result = compile_preset(self.request(tonic_midi=48), manifest=kit)
        self.assertTrue(all(0 <= event.note_midi <= 127 for event in compile_events(result.document, 48000, kit).events))
        value = load_definitions().model_dump()
        value['presets'][0]['scale_id'] = 'missing-scale'
        with self.assertRaises(ValidationError):
            type(load_definitions()).model_validate(value)

    def test_every_representable_candidate_compiles_five_seeds_without_fake_tuning(self) -> None:
        compiled_ids = []
        for preset in load_definitions().presets:
            kit = compatible_kit(preset.id)
            for seed in (0, 1, 4471, 9001, 2147483647):
                with self.subTest(preset=preset.id, seed=seed):
                    try:
                        result = compile_preset(self.request(preset.id, seed=seed), manifest=kit)
                    except BeatPresetError as error:
                        self.assertIn(error.code, ('beat_preset_tuning_unresolved', 'beat_preset_timing_unsupported'))
                    else:
                        compiled_ids.append(preset.id)
                        events = compile_events(result.document, 48000, kit)
                        self.assertTrue(events.events)
                        self.assertTrue(all(0 <= event.onset_frame < events.duration_frames for event in events.events))
        self.assertGreaterEqual(len(set(compiled_ids)), 16)

    def test_seed_changes_bounded_motif_without_changing_source_definition(self) -> None:
        kit = compatible_kit()
        before = canonical_json(load_definitions())
        first = compile_preset(self.request(seed=1), manifest=kit)
        second = compile_preset(self.request(seed=2), manifest=kit)
        self.assertNotEqual(first.document.patterns, second.document.patterns)
        self.assertEqual(canonical_json(load_definitions()), before)

    def test_wrong_sample_identity_and_missing_articulation_do_not_become_available(self) -> None:
        kit = compatible_kit()
        value = kit.model_dump()
        value['instruments'][0]['regions'][0]['terms']['identities'] = [{'sha256': 'f' * 64}]
        with self.assertRaises(BeatPresetError) as error:
            compile_preset(self.request(), manifest=BeatInstrumentManifest.model_validate(value))
        self.assertEqual(error.exception.code, 'beat_preset_license_unresolved')
        value = kit.model_dump()
        value['instruments'][0]['regions'][0]['articulation_id'] = 'unsupported-special-stroke'
        altered = BeatInstrumentManifest.model_validate(value)
        row = next(item for item in catalog(manifests=[altered]).presets if item.preset.id == 'lofi_kyoto')
        self.assertEqual(row.availability, 'unsupported_articulation')

    def test_available_requires_candidate_pitch_and_velocity_coverage(self) -> None:
        value = compatible_kit().model_dump()
        value['instruments'][0]['regions'][0]['note_min'] = 60
        value['instruments'][0]['regions'][0]['note_max'] = 60
        kit = BeatInstrumentManifest.model_validate(value)
        row = next(item for item in catalog(manifests=[kit]).presets if item.preset.id == 'lofi_kyoto')
        self.assertEqual(row.availability, 'unsupported_articulation')
