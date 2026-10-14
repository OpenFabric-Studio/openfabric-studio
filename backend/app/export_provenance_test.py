"""Manifest capacity covers the supported chapter/model bounds before writing."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from app import export_provenance as provenance
from app.export_provenance_contracts import ProvenanceComponent,GenerationIdentity


class ExportProvenanceCapacityTests(unittest.TestCase):
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
