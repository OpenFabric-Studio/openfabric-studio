"""Manifest capacity covers the supported chapter/model bounds before writing."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import export_provenance as provenance
from app.export_provenance_contracts import ProvenanceComponent,GenerationIdentity


class ExportProvenanceCapacityTests(unittest.TestCase):
    def test_music_evidence_round_trips_without_local_notes_and_preserves_public_attribution(self) -> None:
        from app.export_provenance_contracts import MusicComponentEvidence, MusicEvidence
        from app.module_contracts import ModuleComponentIdentity, ModuleLicenseEvidence
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'master.wav'; media.write_bytes(b'fixture')
            evidence = MusicEvidence(components=[MusicComponentEvidence(component_id='sample-bank', scope='samples', restriction='noncommercial',
                evidence=[ModuleLicenseEvidence(kind='publisher_terms', source_url='https://example.org/LICENSE', observed_at='2026-10-17', reviewer='/private/operator-note')],
                identities=[ModuleComponentIdentity(filename='personal-singer/checkpoint.bin', sha256='a' * 64)])],
                public_attributions=['Copyright Example Composer; CC BY-NC 4.0'])
            component = ProvenanceComponent(role='audio', content_origin='mixed', source_id='track:1', source_sha256='b' * 64, music_evidence=evidence)
            captured = provenance.manifest('fixture', 'track_audio', media, [component])
            provenance.write(media, captured)
            shared = provenance.read(media)
            for format in ('json', 'txt'):
                body = provenance.path_for(media, format).read_text()
                self.assertNotIn('/private', body)
                self.assertNotIn('personal-singer', body)
                self.assertIn('Copyright Example Composer', body)
                self.assertIn('noncommercial', body)
            self.assertEqual(shared.components[0].music_evidence.restriction, 'noncommercial')
            self.assertIn('publisher_declared', provenance.path_for(media, 'json').read_text())
            previous = provenance.path_for(media, 'json').read_bytes()
            provenance.write(media, shared)
            self.assertEqual(provenance.path_for(media, 'json').read_bytes(), previous)

    def test_shared_music_links_drop_query_tokens_without_changing_public_license_paths(self) -> None:
        from app.export_provenance_contracts import MusicComponentEvidence, MusicEvidence
        from app.module_contracts import ModuleComponentIdentity, ModuleLicenseEvidence
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'master.wav'; media.write_bytes(b'fixture')
            private_url = 'https://example.org/LICENSE?token=private-token#private-note'
            music = MusicEvidence(components=[MusicComponentEvidence(component_id='publisher-weights', scope='model', source_url=private_url,
                evidence=[ModuleLicenseEvidence(kind='publisher_terms', source_url=private_url, observed_at='2026-10-17', reviewer='local person')],
                identities=[ModuleComponentIdentity(repository_url=private_url, revision='v1', sha256='a' * 64)])])
            captured = provenance.manifest('fixture', 'track_audio', media,
                [ProvenanceComponent(role='audio', content_origin='generated', source_id='track:1', source_sha256='b' * 64, music_evidence=music)])
            provenance.write(media, captured)
            for format in ('json', 'txt'):
                body = provenance.path_for(media, format).read_text()
                self.assertNotIn('private-token', body)
                self.assertNotIn('private-note', body)
                self.assertIn('https://example.org/LICENSE', body)

    def test_changed_media_and_interrupted_manifest_writes_preserve_the_previous_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'master.wav'; media.write_bytes(b'fixture')
            component = ProvenanceComponent(role='audio', content_origin='unknown', source_id='fixture', source_sha256='b' * 64)
            captured = provenance.manifest('fixture', 'track_audio', media, [component])
            provenance.write(media, captured)
            previous = provenance.path_for(media, 'json').read_bytes()
            with patch.object(provenance, 'atomic_text', side_effect=OSError('interrupted write')):
                with self.assertRaises(OSError):
                    provenance.write(media, captured)
            self.assertEqual(provenance.path_for(media, 'json').read_bytes(), previous)
            media.write_bytes(b'changed after encoding and hashing')
            with self.assertRaises(provenance.ProvenanceError) as failure:
                provenance.write(media, captured)
            self.assertEqual(failure.exception.code, 'manifest_media_changed')
            self.assertEqual(provenance.path_for(media, 'json').read_bytes(), previous)

    def test_original_stable_audio_generation_is_classified_as_generated(self) -> None:
        with patch('app.db.get_track', return_value={'model': 'stable_audio3', 'params_json': '{}'}):
            self.assertEqual(provenance.track_component(1, 'original', 'a' * 64).content_origin, 'generated')

    def test_client_declared_replacement_origin_without_owned_render_stays_unknown(self) -> None:
        import json
        params = {'source': 'voice_replacement_v2', 'replacement_render_id': 'a' * 32,
            'replacement_plan_id': 'b' * 32, 'replacement_revision': 1}
        with patch('app.db.get_track', return_value={'model': 'upload', 'params_json': json.dumps(params)}), \
                patch('app.replacement_jobs.publication_component', return_value=None) as publication:
            component = provenance.track_component(1, 'original', 'c' * 64)
        self.assertEqual(component.content_origin, 'unknown')
        publication.assert_called_once()

    def test_voice_versions_preserve_nc_but_do_not_claim_the_voice_dependencies_are_known(self) -> None:
        import json
        from app.music_evidence import capture_music_evidence, serialize_music_evidence
        params = {'_music_evidence': serialize_music_evidence(capture_music_evidence('yue2'))}
        with patch('app.db.get_track', return_value={'model': 'yue2', 'params_json': json.dumps(params)}):
            captured = provenance.track_component(1, 'voice-version', 'a' * 64, is_voice=True)
        self.assertIsNotNone(captured.music_evidence)
        if captured.music_evidence is None:
            return
        self.assertEqual(captured.music_evidence.restriction, 'noncommercial')
        self.assertFalse(captured.music_evidence.lineage_complete)

    def test_shared_production_manifest_keeps_hashes_without_private_evidence_text(self) -> None:
        from app.artist_production_contracts import (ProductionProvenance, ProductionGate, SourceAsset,
            SourceDeclaration, RightsReview, VoiceModelSnapshot, ModelFileIdentity)
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'master.wav'
            media.write_bytes(b'fixture')
            private = '/private-artist/recordings/take-name.wav'
            asset = SourceAsset(id='a' * 32, sha256='b' * 64, role='guide_vocal', origin='human_recorded',
                provider=private, created_at='2026-10-14', classification_basis='user_declaration', reference_only=False,
                declaration=SourceDeclaration(role='guide_vocal', origin='human_recorded', provider=private,
                    original_filename=private, rights_holder=private, evidence_ref=private),
                reviews=[RightsReview(id='c' * 32, created_at='2026-10-14', status='approved',
                    reviewer_label=private, evidence_ref=private, note=private)])
            model = VoiceModelSnapshot(voice_id='d' * 32, model_revision_id=private, diffusion_steps=30,
                files=[ModelFileIdentity(role=role, name=private, sha256='e' * 64)
                    for role in ('checkpoint', 'configuration', 'voice_reference')])
            proof = ProductionProvenance(run_id='f' * 32, project_id=1, artist_id='d' * 32,
                consent_version_id='c' * 32, audio_version_id='a' * 32, graph_root_id=asset.id,
                graph_sha256='e' * 64, assets=[asset], model=model, gate=ProductionGate())
            captured = provenance.manifest('fixture', 'track_audio', media,
                [ProvenanceComponent(role='audio', content_origin='mixed', source_id='fixture', source_sha256='b' * 64)])
            captured.production = proof
            provenance.write(media, captured)
            shared = provenance.read(media)
            self.assertNotIn('private-artist', provenance.path_for(media, 'json').read_text())
            self.assertIsNotNone(shared.production)
            self.assertEqual(shared.production.evidence_visibility, 'shared_references')
            self.assertEqual(shared.production.assets[0].sha256, asset.sha256)
            self.assertEqual(shared.production.graph_sha256, proof.graph_sha256)
            self.assertTrue(shared.production.assets[0].reviews[0].evidence_ref.startswith('local-ref:'))
            self.assertEqual(captured.production.assets[0].declaration.evidence_ref, private)
            before = provenance.path_for(media, 'json').read_bytes()
            provenance.write(media, shared)
            self.assertEqual(provenance.path_for(media, 'json').read_bytes(), before)

    def test_supported_long_book_manifest_round_trips_and_rejects_oversized_replacement_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            media=Path(directory)/'book.wav';media.write_bytes(b'fixture')
            generations=[GenerationIdentity(engine='openrouter',model_id='provider/'+str(index)+'x'*180,engine_identity='a'*64) for index in range(32)]
            components=[ProvenanceComponent(role='audio',content_origin='generated',source_id=f'book:fixture:chapter:{index}',source_sha256='a'*64,generation_identities=generations,source_record_count=200) for index in range(100)]
            captured=provenance.manifest('book','audiobook',media,components)
            provenance.write(media,captured)
            self.assertGreater(provenance.path_for(media,'json').stat().st_size,1024*1024)
            self.assertEqual(provenance.read(media),captured)
            before=provenance.path_for(media,'json').read_bytes()
            oversized=provenance.manifest('too-large','audiobook',media,components*10)
            with self.assertRaises(provenance.ProvenanceError) as rejected:provenance.write(media,oversized)
            self.assertEqual(rejected.exception.code,'manifest_capacity_exceeded')
            self.assertEqual(provenance.path_for(media,'json').read_bytes(),before)


if __name__=='__main__':unittest.main()
