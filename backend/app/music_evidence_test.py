"""Recorded component evidence survives transformations without clearing rights."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

from app import db, music_evidence
from app.contracts import JsonObject
from app.export_provenance_contracts import MusicComponentEvidence, MusicEvidence


class MusicEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'catalog.db'))
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(db, '_db', None))
        self.media = self.root / 'fixture.wav'
        self.media.write_bytes(b'fixture')

    def tearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    def track(self, model: str, params: JsonObject) -> int:
        return db.insert_track(model=model, title='fixture', lyrics='', seed=None, duration_ms=None,
            wall_ms=None, params=params, audio_path=self.media, abc_path=None)

    def test_capture_is_a_detached_publisher_snapshot_without_transcription_auxiliaries(self) -> None:
        captured = music_evidence.capture_music_evidence('yue2')
        self.assertEqual(captured.restriction, 'noncommercial')
        self.assertEqual(captured.identity_basis, 'publisher_declared')
        ids = {item.component_id for item in captured.components}
        self.assertNotIn('sheetsage2_orig', ids)
        self.assertNotIn('muscriptor_small_f32', ids)
        self.assertIn('native-source-rights', ids)
        serialized = music_evidence.serialize_music_evidence(captured)
        captured.components.clear()
        self.assertTrue(serialized['components'])

    def test_old_tracks_do_not_acquire_current_catalog_permissions_or_restrictions(self) -> None:
        with patch('app.music_evidence.capture_music_evidence', side_effect=AssertionError('No current-catalog inference')):
            captured = music_evidence.track_music_evidence(self.track('yue2', {}))
        self.assertEqual(captured.restriction, 'unknown')
        self.assertFalse(captured.lineage_complete)
        self.assertEqual(captured.components, [])

    def test_two_track_mix_and_converted_voice_preserve_nc_and_exact_adapter_hashes(self) -> None:
        nc = music_evidence.serialize_music_evidence(music_evidence.capture_music_evidence('yue2'))
        adapter: JsonObject = {'id': 'a' * 32, 'engine': 'yue2', 'target': 'ar', 'name': '/private/singer-name',
            'sha256': 'b' * 64, 'created_at': '2026-10-17', 'source_job_id': 'c' * 32, 'strength': .7}
        first = self.track('yue2', {'_music_evidence': nc, '_music_adapters': [adapter]})
        second = self.track('upload', {})
        mix = self.track('editor', {'source_track_ids': [first, second], 'source_ancestry_complete': True})
        converted = self.track('upload', {'source': 'voice_replacement', 'source_track_id': mix})
        captured = music_evidence.track_music_evidence(converted)
        self.assertEqual(captured.restriction, 'noncommercial')
        self.assertFalse(captured.lineage_complete)
        self.assertEqual(captured.adapters[0].sha256, 'b' * 64)
        self.assertEqual(captured.adapters[0].strength, .7)
        self.assertNotIn('singer-name', captured.model_dump_json())

    def test_cycles_missing_parents_and_invalid_snapshots_keep_known_nc_without_claiming_complete_lineage(self) -> None:
        nc = music_evidence.serialize_music_evidence(music_evidence.capture_music_evidence('yue2'))
        first = self.track('yue2', {'_music_evidence': nc})
        mix = self.track('editor', {'source_track_ids': [first, 999999], 'source_ancestry_complete': True})
        db.get_db().execute('UPDATE tracks SET params_json=? WHERE id=?', ('{"source_track_ids":[' + str(first) + ',' + str(mix) + '],"source_ancestry_complete":true,"_music_evidence":{"invalid":true}}', mix))
        db.get_db().commit()
        captured = music_evidence.track_music_evidence(mix)
        self.assertEqual(captured.restriction, 'noncommercial')
        self.assertFalse(captured.lineage_complete)

    def test_generic_sample_and_conditioning_components_cannot_erase_a_known_restriction(self) -> None:
        sample = MusicEvidence(components=[MusicComponentEvidence(component_id='sample-bank', scope='samples', restriction='noncommercial')], lineage_complete=True)
        conditioning = MusicEvidence(components=[MusicComponentEvidence(component_id='conditioning-model', scope='model', restriction='conditional')], lineage_complete=True)
        unknown = MusicEvidence(components=[MusicComponentEvidence(component_id='source-recording', scope='source_data')])
        combined = music_evidence.merge_music_evidence([sample, conditioning, unknown])
        self.assertEqual(combined.restriction, 'noncommercial')
        self.assertFalse(combined.lineage_complete)
        self.assertEqual(len(combined.components), 3)
        permissive = MusicEvidence(components=[MusicComponentEvidence(component_id='mit-code', scope='code', restriction='unrestricted_declared')])
        self.assertEqual(music_evidence.merge_music_evidence([permissive, unknown]).restriction, 'unknown')

    def test_malformed_adapter_records_stay_unknown_and_cannot_export_paths(self) -> None:
        captured = music_evidence.track_music_evidence(self.track('stable_audio3', {'_music_adapters': [{'path': '/private/model.bin', 'sha256': 'bad'}]}))
        self.assertEqual(captured.adapters, [])
        self.assertFalse(captured.lineage_complete)
        self.assertNotIn('/private', captured.model_dump_json())

    def test_missing_parent_remains_unknown_and_does_not_discard_other_known_components(self) -> None:
        parent = self.track('yue2', {'_music_evidence': music_evidence.serialize_music_evidence(music_evidence.capture_music_evidence('yue2'))})
        derived = self.track('editor', {'source_track_ids': [parent, 999999], 'source_ancestry_complete': True})
        captured = music_evidence.track_music_evidence(derived)
        self.assertEqual(captured.restriction, 'noncommercial')
        self.assertFalse(captured.lineage_complete)

    def test_lineage_budget_does_not_claim_a_large_graph_was_fully_observed(self) -> None:
        parent = self.track('yue2', {'_music_evidence': music_evidence.serialize_music_evidence(music_evidence.capture_music_evidence('yue2'))})
        derived = self.track('editor', {'source_track_ids': [parent], 'source_ancestry_complete': True})
        with patch.object(music_evidence, '_MAX_TRACKS', 1):
            captured = music_evidence.track_music_evidence(derived)
        self.assertFalse(captured.lineage_complete)
        self.assertEqual(captured.restriction, 'unknown')

    def test_untrusted_evidence_requires_bounded_attribution_and_a_boolean_lineage_claim(self) -> None:
        with self.assertRaises(ValidationError):
            MusicEvidence.model_validate({'public_attributions': ['x' * 2001]})
        with self.assertRaises(ValidationError):
            MusicEvidence.model_validate({'lineage_complete': 'yes'})

    def test_opaque_generation_params_cannot_forge_editor_or_voice_ancestry(self) -> None:
        parent = self.track('yue2', {'_music_evidence': music_evidence.serialize_music_evidence(music_evidence.capture_music_evidence('yue2'))})
        forged = self.track('ace_step', {'source_track_ids': [parent], 'source_ancestry_complete': True,
            'source': 'voice_replacement', 'source_track_id': parent})
        captured = music_evidence.track_music_evidence(forged)
        self.assertEqual(captured.restriction, 'unknown')
        self.assertFalse(captured.lineage_complete)


if __name__ == '__main__':
    unittest.main()
