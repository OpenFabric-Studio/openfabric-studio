from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import db, music_terms
from app.module_contracts import ModuleLicenseDeclaration
from app.music_terms_contracts import MusicTermsAcceptRequest, MusicTermsSettingsRequest


class MusicTermsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.enterContext(patch.object(db, 'DB_PATH', root / 'library.db'))
        self.enterContext(patch.object(db, 'FILES_DIR', root / 'files'))
        self.enterContext(patch.object(db, 'DATA_DIR', root))
        self.previous = db._db
        db._db = None
        self.addCleanup(self.close_database)

    def close_database(self) -> None:
        if db._db is not None:
            db._db.close()
        db._db = self.previous

    def accept(self, engine: str = 'yue2', operation: str = 'generation', *, allow: bool = True) -> None:
        before = music_terms.snapshot(engine, operation)
        body = MusicTermsAcceptRequest.model_validate({'engine': engine, 'operation': operation,
            'expected_revision': before.revision, 'allow_noncommercial': allow,
            'terms_digests': [item.terms_digest for item in before.requirements]})
        music_terms.accept(body)

    def test_new_library_disables_noncommercial_but_keeps_permissive_work_available(self) -> None:
        state = music_terms.snapshot('yue2', 'generation')
        self.assertFalse(state.allowed)
        self.assertFalse(state.noncommercial_enabled)
        self.assertTrue(state.requirements)
        self.assertTrue(all(item.restriction == 'noncommercial' and not item.acknowledged
                            for item in state.requirements))
        self.assertTrue(music_terms.snapshot('ace_step', 'generation').allowed)

    def test_additive_migration_preserves_library_and_copied_backup_restores(self) -> None:
        connection = db.get_db()
        db.FILES_DIR.mkdir(exist_ok=True)
        source = db.FILES_DIR / 'original.wav'
        source.write_bytes(b'original recording fixture')
        identifier = db.insert_track(model='upload', title='Original', lyrics='', seed=None,
            duration_ms=1000, wall_ms=None, params={'source': 'original'}, audio_path=source, abc_path=None)
        row_before = dict(connection.execute('SELECT * FROM tracks WHERE id=?', (identifier,)).fetchone())
        backup = db.DATA_DIR / 'before-terms.db'
        with sqlite3.connect(backup) as saved:
            connection.backup(saved)
        music_terms.snapshot('yue2', 'generation')
        self.assertEqual(dict(connection.execute('SELECT * FROM tracks WHERE id=?', (identifier,)).fetchone()), row_before)
        self.assertEqual(source.read_bytes(), b'original recording fixture')
        with sqlite3.connect(backup) as saved, sqlite3.connect(db.DATA_DIR / 'restored.db') as restored:
            saved.backup(restored)
            self.assertEqual(restored.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 1)
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='music_terms_schema'").fetchone()[0], 0)

    def test_noncommercial_consent_is_explicit_and_failed_acceptance_writes_nothing(self) -> None:
        before = music_terms.snapshot('yue2', 'generation')
        with self.assertRaises(music_terms.MusicTermsError) as failure:
            self.accept(allow=False)
        self.assertEqual(failure.exception.code, 'music_noncommercial_disabled')
        self.assertEqual(music_terms.snapshot('yue2', 'generation'), before)

    def test_acceptance_is_persistent_and_scoped_to_operation(self) -> None:
        self.accept()
        music_terms.require('yue2', 'generation')
        self.assertTrue(music_terms.snapshot('yue2', 'generation').allowed)
        self.assertFalse(music_terms.snapshot('yue2', 'training').allowed)
        with self.assertRaises(music_terms.MusicTermsError) as failure:
            music_terms.require('yue2', 'training')
        self.assertEqual(failure.exception.code, 'music_terms_required')

    def test_interrupted_acceptance_rolls_back_rows_and_policy_together(self) -> None:
        before = music_terms.snapshot('yue2', 'generation')
        with patch.object(music_terms, '_advance', side_effect=OSError('interrupted write')):
            with self.assertRaises(OSError):
                self.accept()
        self.assertEqual(music_terms.snapshot('yue2', 'generation'), before)
        with sqlite3.connect(db.DB_PATH) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM music_terms_acknowledgements').fetchone()[0], 0)

    def test_yue_training_acknowledges_the_actual_bf16_training_base(self) -> None:
        state = music_terms.snapshot('yue2', 'training')
        self.assertEqual({item.component_id for item in state.requirements}, {'yue-training-base'})
        self.assertFalse(state.allowed)
        self.accept(operation='training')
        self.assertTrue(music_terms.snapshot('yue2', 'training').allowed)
        self.assertFalse(music_terms.snapshot('yue2', 'generation').allowed)

    def test_global_disable_preserves_acknowledgements(self) -> None:
        self.accept()
        accepted = music_terms.snapshot('yue2', 'generation')
        music_terms.update_settings(MusicTermsSettingsRequest(expected_revision=accepted.revision,
            noncommercial_enabled=False))
        disabled = music_terms.snapshot('yue2', 'generation')
        self.assertFalse(disabled.allowed)
        self.assertTrue(all(item.acknowledged for item in disabled.requirements))
        with self.assertRaises(music_terms.MusicTermsError) as failure:
            music_terms.require('yue2', 'generation')
        self.assertEqual(failure.exception.code, 'music_noncommercial_disabled')

    def test_conditional_terms_do_not_enable_noncommercial_globally(self) -> None:
        self.accept('stable_audio3', allow=False)
        state = music_terms.snapshot('stable_audio3', 'generation')
        self.assertTrue(state.allowed)
        self.assertFalse(state.noncommercial_enabled)
        self.assertTrue(all(item.restriction == 'conditional' for item in state.requirements))

    def test_stale_revision_does_not_change_settings_or_acceptance(self) -> None:
        before = music_terms.snapshot('yue2', 'generation')
        self.accept()
        with self.assertRaises(music_terms.MusicTermsError) as failure:
            music_terms.update_settings(MusicTermsSettingsRequest(expected_revision=before.revision,
                noncommercial_enabled=False))
        self.assertEqual(failure.exception.code, 'music_terms_revision_conflict')
        self.assertTrue(music_terms.snapshot('yue2', 'generation').allowed)

    def test_changed_terms_cannot_be_accepted_using_old_digests(self) -> None:
        before = music_terms.snapshot('stable_audio3', 'generation')
        original = music_terms.declarations('stable_audio3')
        changed = [item.model_copy(update={'notes': item.notes + ' Revised terms evidence.'}) for item in original]
        with patch.object(music_terms, 'declarations', return_value=changed):
            with self.assertRaises(music_terms.MusicTermsError) as failure:
                music_terms.accept(MusicTermsAcceptRequest(engine='stable_audio3', operation='generation',
                    expected_revision=before.revision,
                    terms_digests=[item.terms_digest for item in before.requirements]))
            self.assertEqual(failure.exception.code, 'music_terms_changed')
            self.assertEqual(music_terms.snapshot('stable_audio3', 'generation').revision, before.revision)

    def test_unknown_declarations_are_not_turned_into_permission(self) -> None:
        unknown = ModuleLicenseDeclaration(component_id='unreviewed', name='Unreviewed local source',
            scope='model', status='unknown', notes='No publisher evidence.', reviewed_at='2026-10-17')
        with patch.object(music_terms, 'declarations', return_value=[unknown]):
            state = music_terms.snapshot('ace_step', 'generation')
        self.assertEqual(state.requirements, [])
        self.assertEqual(unknown.restriction, 'unknown')
        self.assertNotIn('commercial_eligible', state.model_dump())

    def test_concurrent_acceptances_use_compare_and_swap(self) -> None:
        before = music_terms.snapshot('yue2', 'generation')
        body = MusicTermsAcceptRequest(engine='yue2', operation='generation', expected_revision=before.revision,
            allow_noncommercial=True, terms_digests=[item.terms_digest for item in before.requirements])

        def attempt() -> str:
            try:
                music_terms.accept(body)
                return 'accepted'
            except music_terms.MusicTermsError as failure:
                return failure.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(results, ['accepted', 'music_terms_revision_conflict'])

    def test_unknown_schema_is_preserved_and_refused(self) -> None:
        db.get_db()
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute('CREATE TABLE music_terms_schema(id INTEGER PRIMARY KEY, version INTEGER NOT NULL)')
            connection.execute('INSERT INTO music_terms_schema VALUES(1,2)')
        with self.assertRaises(music_terms.MusicTermsError) as failure:
            music_terms.snapshot('ace_step', 'generation')
        self.assertEqual(failure.exception.code, 'music_terms_unavailable')
        with sqlite3.connect(db.DB_PATH) as connection:
            self.assertEqual(connection.execute('SELECT version FROM music_terms_schema').fetchone(), (2,))

    def test_transcription_and_generation_show_only_relevant_components(self) -> None:
        generated = music_terms.snapshot('yue2', 'generation')
        transcribed = music_terms.snapshot('yue2', 'transcription')
        self.assertFalse(any(item.component_id.startswith(('sheetsage', 'muscriptor')) for item in generated.requirements))
        self.assertTrue(transcribed.requirements)
        self.assertTrue(all(item.component_id.startswith(('sheetsage', 'muscriptor')) for item in transcribed.requirements))


if __name__ == '__main__':
    unittest.main()
