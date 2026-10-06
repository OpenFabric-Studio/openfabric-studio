"""Manifest capacity covers the supported chapter/model bounds before writing."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from app import export_provenance as provenance
from app.export_provenance_contracts import ProvenanceComponent,GenerationIdentity


class ExportProvenanceCapacityTests(unittest.TestCase):
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
