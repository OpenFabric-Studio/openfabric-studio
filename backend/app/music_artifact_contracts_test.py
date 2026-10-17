from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.music_artifact_contracts import ConfirmMusicArtifactCleanup, MusicArtifactCleanupRequest, MusicArtifactGroup


class MusicArtifactContractsTests(unittest.TestCase):
    def test_cleanup_requires_an_explicit_bounded_unique_selection(self) -> None:
        self.assertEqual(MusicArtifactCleanupRequest(analysis_job_ids=['a' * 32]).analysis_job_ids, ['a' * 32])
        for identifiers in ([], ['a' * 32] * 2, ['a' * 32 + '\n'], ['../source'], [f'{index:032x}' for index in range(21)]):
            with self.subTest(identifiers=identifiers), self.assertRaises(ValidationError):
                MusicArtifactCleanupRequest(analysis_job_ids=identifiers)
        with self.assertRaises(ValidationError):
            MusicArtifactCleanupRequest.model_validate({'analysis_job_ids': ['a' * 32], 'path': '/private/source'})

    def test_confirmation_and_inventory_never_accept_paths_or_coerced_truth(self) -> None:
        for confirm in (False, 1, 'true'):
            with self.subTest(confirm=confirm), self.assertRaises(ValidationError):
                ConfirmMusicArtifactCleanup.model_validate({'plan_sha256': 'b' * 64, 'confirm_cleanup': confirm})
        group = {'id': 'models', 'kind': 'model', 'root_alias': 'library_models', 'disposition': 'retained', 'reasons': ['protected_model']}
        for path in ('../outside', '/private/source', 'C:/private/source', 'a\\b'):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                MusicArtifactGroup.model_validate({**group, 'relative_path': path})
        with self.assertRaises(ValidationError):
            MusicArtifactGroup.model_validate({**group, 'size_bytes': 9007199254740992})


if __name__ == '__main__':
    unittest.main()
