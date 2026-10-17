"""Only persisted publication owners may classify derived music exports."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app import export_provenance
from app.export_provenance_contracts import MusicEvidence, ProvenanceComponent


class MusicOwnerExportTests(unittest.TestCase):
    def test_authoritative_mashup_and_beat_song_components_preserve_classification_and_nc(self) -> None:
        for model, owner, selector in (('mashup', 'app.mashups', 'mashup_job_id'),
                ('beat_song', 'app.beat_song_mix', 'beat_song_mix_job_id')):
            with self.subTest(model=model):
                params = {'source': model, selector: 'a' * 32}
                component = ProvenanceComponent(role='audio', source_id=model + ':' + 'a' * 32,
                    source_sha256='b' * 64, content_origin='mixed', engine='openfabric-cpu-dsp',
                    music_evidence=MusicEvidence(restriction='noncommercial'))
                with patch('app.db.get_track', return_value={'model': model, 'params_json': json.dumps(params)}), \
                        patch(owner + '.publication_component', return_value=component) as publication:
                    actual = export_provenance.track_component(7, 'original', 'b' * 64)
                self.assertEqual(actual, component)
                self.assertEqual(actual.music_evidence.restriction, 'noncommercial')
                publication.assert_called_once_with(7, 'b' * 64, params)

    def test_forged_music_params_without_owner_association_cannot_assert_classification(self) -> None:
        for model, owner, selector in (('mashup', 'app.mashups', 'mashup_job_id'),
                ('beat_song', 'app.beat_song_mix', 'beat_song_mix_job_id')):
            with self.subTest(model=model):
                params = {'source': model, selector: 'a' * 32, 'origin': 'human_recorded'}
                with patch('app.db.get_track', return_value={'model': model, 'params_json': json.dumps(params)}), \
                        patch(owner + '.publication_component', return_value=None) as publication:
                    actual = export_provenance.track_component(7, 'original', 'b' * 64)
                self.assertEqual(actual.content_origin, 'unknown')
                self.assertEqual(actual.classification_basis, 'unverified')
                publication.assert_called_once_with(7, 'b' * 64, params)

    def test_generic_upload_source_label_is_not_a_publication_owner(self) -> None:
        with patch('app.db.get_track', return_value={'model': 'upload',
                'params_json': json.dumps({'source': 'mashup', 'mashup_job_id': 'a' * 32})}), \
                patch('app.mashups.publication_component') as publication:
            actual = export_provenance.track_component(7, 'original', 'b' * 64)
        self.assertEqual(actual.content_origin, 'unknown')
        publication.assert_not_called()
