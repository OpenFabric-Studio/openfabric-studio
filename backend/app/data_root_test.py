"""Library folder moves, without touching the real OpenFabric library."""
from __future__ import annotations

import json
import errno
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from collections.abc import Callable
from unittest.mock import patch

from app import data_root

from app.data_root import (
    DataDirError,
    adopt_legacy_logs,
    ensure_layout,
    has_library,
    library_status,
    migrate_library,
    parse_chosen_folder,
    place_seed_models,
    resolve_data_dir,
    rewrite_library_paths,
    save_data_dir,
    validate_data_dir,
)


class DataRootTests(unittest.TestCase):
    def test_library_move_rewrites_repair_exports_and_captured_reference_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp).resolve() / 'old', Path(tmp).resolve() / 'new'
            dest.mkdir()
            database = dest / 'audiobooks.db'
            snapshot = {'reference_audio_path': str(src / 'audiobooks' / 'references' / 'input.wav'), 'prompt_text': 'Story mentions ' + str(src)}
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE audiobook_books(export_path TEXT, mp3_export_path TEXT, m4b_export_path TEXT, cover_path TEXT)')
                connection.execute('INSERT INTO audiobook_books VALUES(?,?,?,?)', tuple(str(src / 'audiobooks' / name) for name in ('repaired.wav','repaired.mp3','repaired.m4b','cover.png')))
                connection.execute('CREATE TABLE audiobook_sections(output_path TEXT, snapshot_json TEXT)')
                connection.execute('INSERT INTO audiobook_sections VALUES(?,?)', (str(src / 'audiobooks' / 'section.wav'), json.dumps(snapshot)))
                connection.execute('CREATE TABLE audiobook_workflows(payload TEXT)')
                connection.execute('INSERT INTO audiobook_workflows VALUES(?)', (json.dumps({'snapshot': snapshot}),))
            rewrite_library_paths(database, src, dest)
            with sqlite3.connect(database) as connection:
                paths = connection.execute('SELECT * FROM audiobook_books').fetchone()
                self.assertEqual(paths, tuple(str(dest / 'audiobooks' / name) for name in ('repaired.wav','repaired.mp3','repaired.m4b','cover.png')))
                output, captured = connection.execute('SELECT * FROM audiobook_sections').fetchone()
                self.assertEqual(output, str(dest / 'audiobooks' / 'section.wav'))
                restored = json.loads(captured)
                self.assertEqual(restored['reference_audio_path'], str(dest / 'audiobooks' / 'references' / 'input.wav'))
                self.assertEqual(restored['prompt_text'], snapshot['prompt_text'])
                workflow = json.loads(connection.execute('SELECT payload FROM audiobook_workflows').fetchone()[0])
                self.assertEqual(workflow['snapshot'], restored)

    def test_cloud_snapshots_and_empty_presets_survive_path_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src,dest=Path(tmp).resolve()/'old',Path(tmp).resolve()/'new';dest.mkdir()
            database=dest/'profiles.db'
            cloud={'renderer':'openrouter','reference_audio_path':str(src/'audiobooks'/'_cloud_speech'/'references'/'sample.wav'),
                'cloud':{'model':'fish-audio/s2.1-pro','voice':None,'clone_reference':True},'cloud_authorization_id':'a'*32}
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE voice_profiles(reference_audio_path TEXT)')
                connection.execute('INSERT INTO voice_profiles VALUES(?)',('',))
                connection.execute('CREATE TABLE audiobook_sections(output_path TEXT,snapshot_json TEXT)')
                connection.execute('INSERT INTO audiobook_sections VALUES(?,?)',(str(src/'audiobooks'/'passage.wav'),json.dumps(cloud)))
            rewrite_library_paths(database,src,dest)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT reference_audio_path FROM voice_profiles').fetchone()[0],'')
                _,payload=connection.execute('SELECT * FROM audiobook_sections').fetchone()
                restored=json.loads(payload)
                self.assertEqual(restored['reference_audio_path'],str(dest/'audiobooks'/'_cloud_speech'/'references'/'sample.wav'))
                self.assertEqual(restored['cloud'],cloud['cloud']);self.assertEqual(restored['cloud_authorization_id'],cloud['cloud_authorization_id'])

    def _speech_library(self, src: Path) -> None:
        for folder in ('audiobooks', 'voice-profiles', 'speech-clone-trials'):
            (src / folder).mkdir(parents=True, exist_ok=True)
            (src / folder / 'sample.wav').write_bytes(b'original audio')
        with sqlite3.connect(src / 'voice-profiles' / 'profiles.db') as connection:
            connection.execute('CREATE TABLE voice_profiles(reference_audio_path TEXT, notes TEXT)')
            connection.execute('INSERT INTO voice_profiles VALUES(?,?)',
                (str(src / 'voice-profiles' / 'sample.wav'), 'Mention ' + str(src)))
        with sqlite3.connect(src / 'audiobooks' / 'audiobooks.db') as connection:
            connection.execute('CREATE TABLE audiobook_books(export_path TEXT)')
            connection.execute('INSERT INTO audiobook_books VALUES(?)', (str(src / 'audiobooks' / 'sample.wav'),))
            connection.execute('CREATE TABLE audiobook_jobs(output_path TEXT, chapter_text TEXT)')
            connection.execute('INSERT INTO audiobook_jobs VALUES(?,?)',
                (str(src / 'audiobooks' / 'sample.wav'), 'Story mentions ' + str(src)))

    def test_speech_only_library_is_moved_and_absolute_paths_are_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp).resolve() / 'old', Path(tmp).resolve() / 'new'
            self._speech_library(src)
            self.assertTrue(has_library(src))
            migrate_library(src, dest)
            for folder in ('audiobooks', 'voice-profiles', 'speech-clone-trials'):
                self.assertEqual((dest / folder / 'sample.wav').read_bytes(), b'original audio')
                self.assertFalse((src / folder).exists())
            with sqlite3.connect(dest / 'voice-profiles' / 'profiles.db') as connection:
                path, notes = connection.execute('SELECT reference_audio_path, notes FROM voice_profiles').fetchone()
            self.assertEqual(path, str(dest / 'voice-profiles' / 'sample.wav'))
            self.assertEqual(notes, 'Mention ' + str(src))
            with sqlite3.connect(dest / 'audiobooks' / 'audiobooks.db') as connection:
                export = connection.execute('SELECT export_path FROM audiobook_books').fetchone()[0]
                path, text = connection.execute('SELECT output_path, chapter_text FROM audiobook_jobs').fetchone()
            self.assertEqual(export, str(dest / 'audiobooks' / 'sample.wav'))
            self.assertEqual(path, export)
            self.assertEqual(text, 'Story mentions ' + str(src))

    def test_failed_speech_catalog_rewrite_restores_all_databases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp).resolve() / 'old', Path(tmp).resolve() / 'new'
            self._library(src)
            self._speech_library(src)
            rewrite = data_root.rewrite_library_paths
            def fail_after_speech(path: Path, old: Path, new: Path) -> None:
                rewrite(path, old, new)
                if path.name == 'audiobooks.db':
                    raise OSError('interrupted speech metadata update')
            with patch.object(data_root, 'rewrite_library_paths', side_effect=fail_after_speech):
                with self.assertRaises(OSError):
                    migrate_library(src, dest)
            with sqlite3.connect(src / 'voice-profiles' / 'profiles.db') as connection:
                path = connection.execute('SELECT reference_audio_path FROM voice_profiles').fetchone()[0]
            self.assertEqual(path, str(src / 'voice-profiles' / 'sample.wav'))
            with sqlite3.connect(src / 'audiobooks' / 'audiobooks.db') as connection:
                path = connection.execute('SELECT export_path FROM audiobook_books').fetchone()[0]
            self.assertEqual(path, str(src / 'audiobooks' / 'sample.wav'))
            self.assertFalse(has_library(dest))

    def test_migration_rejects_a_speech_database_symlink_outside_the_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); src, dest = root / 'old', root / 'new'
            self._speech_library(src)
            private = root / 'private.db'
            (src / 'voice-profiles' / 'profiles.db').replace(private)
            original = private.read_bytes()
            (src / 'voice-profiles' / 'profiles.db').symlink_to(private)
            with self.assertRaises(DataDirError):
                migrate_library(src, dest)
            self.assertEqual(private.read_bytes(), original)
            self.assertTrue((src / 'audiobooks' / 'sample.wav').is_file())

    def test_interrupted_speech_migration_can_be_recovered_without_losing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); src, dest = root / 'old', root / 'new'
            self._speech_library(src)
            with patch.object(data_root, 'rewrite_text_prefixes', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    migrate_library(src, dest)
            migrate_library(src, dest)
            with sqlite3.connect(dest / 'voice-profiles' / 'profiles.db') as connection:
                path = connection.execute('SELECT reference_audio_path FROM voice_profiles').fetchone()[0]
            self.assertEqual(path, str(dest / 'voice-profiles' / 'sample.wav'))
            self.assertEqual((dest / 'speech-clone-trials' / 'sample.wav').read_bytes(), b'original audio')

    def test_migration_rewrites_version_catalog_and_export_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / 'old', root / 'new'
            song, _voice = self._library(src)
            version = src / 'files' / '_versions' / '1' / 'original.wav'
            version.parent.mkdir(parents=True)
            version.write_bytes(b'version')
            with sqlite3.connect(src / 'aicollector.db') as connection:
                connection.execute('CREATE TABLE audio_versions(audio_path TEXT,captured_source_path TEXT)')
                connection.execute('INSERT INTO audio_versions VALUES(?,?)', (str(version), str(song)))
            directory = src / 'files' / '_exports' / '1' / ('a' * 32)
            directory.mkdir(parents=True)
            metadata = directory / 'export.json'
            metadata.write_text(json.dumps({'source_relative':'_versions/1/original.wav',
                'expected_primary':str(song), 'worker':{'pid':100,'token':'token','receipt':str(directory/'worker.json')},
                'audio_url':'/api/tracks/1/versions/v/exports/e/audio'}))
            migrate_library(src, dest)
            with sqlite3.connect(dest / 'aicollector.db') as connection:
                audio, captured = connection.execute('SELECT audio_path,captured_source_path FROM audio_versions').fetchone()
            self.assertEqual(audio, str(dest / 'files' / '_versions' / '1' / 'original.wav'))
            self.assertEqual(captured, str(dest / 'files' / 'song.wav'))
            export = json.loads((dest / metadata.relative_to(src)).read_text())
            self.assertEqual(export['expected_primary'], str(dest / 'files' / 'song.wav'))
            self.assertEqual(export['worker']['receipt'], str(dest / directory.relative_to(src) / 'worker.json'))
            self.assertEqual(export['source_relative'], '_versions/1/original.wav')
            self.assertEqual(export['audio_url'], '/api/tracks/1/versions/v/exports/e/audio')

    def test_failed_export_metadata_rewrite_restores_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / 'old', root / 'new'
            song, _voice = self._library(src)
            directory = src / 'files' / '_exports' / '1' / ('a' * 32)
            directory.mkdir(parents=True)
            metadata = directory / 'export.json'
            metadata.write_text(json.dumps({'expected_primary':str(song)}))
            before = metadata.read_bytes()
            rewrite = data_root.rewrite_text_prefixes
            def fail_exports(path: Path, old: Path, new: Path) -> None:
                rewrite(path, old, new)
                if path.name == '_exports':
                    raise OSError('export metadata failure')
            with patch.object(data_root, 'rewrite_text_prefixes', side_effect=fail_exports):
                with self.assertRaises(OSError):
                    migrate_library(src, dest)
            self.assertEqual(metadata.read_bytes(), before)
            self.assertTrue(song.exists())
            self.assertFalse(has_library(dest))

    def _library(self, path: Path) -> tuple[Path, Path]:
        song = path / "files" / "song.wav"
        song.parent.mkdir(parents=True)
        song.write_bytes(b"audio")
        voice = path / "voices" / "abc" / "voice.json"
        voice.parent.mkdir(parents=True)
        voice.write_text(json.dumps({"checkpoint": str(path / "voices" / "abc" / "model.pth")}), encoding="utf-8")
        with sqlite3.connect(path / "aicollector.db") as connection:
            connection.execute("CREATE TABLE tracks (audio_path TEXT, stems_json TEXT)")
            connection.execute("INSERT INTO tracks VALUES (?, ?)", (str(song), json.dumps({"vocal": str(song)})))
        return song, voice

    def test_a_second_folder_change_migrates_from_the_active_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            original, first, second = (root / name for name in ("default", "first", "second"))
            self._library(original)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(first)}), encoding="utf-8")
            with patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(cfg)}):
                self.assertEqual(resolve_data_dir(original), first)
                save_data_dir(second)
                self.assertEqual(resolve_data_dir(original), second)
                self.assertTrue((second / "files" / "song.wav").is_file())
                self.assertFalse((first / "files").exists())
                saved = json.loads(cfg.read_text(encoding="utf-8"))
                self.assertEqual(saved["active_data_dir"], str(second))

    def test_failed_metadata_rewrite_restores_catalog_and_voice_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            song, voice = self._library(src)
            original_voice = voice.read_bytes()
            rewrite = data_root.rewrite_text_prefixes

            def fail_after_rewriting(root: Path, old: Path, new: Path) -> None:
                rewrite(root, old, new)
                raise OSError("metadata write failed")

            with patch.object(data_root, "rewrite_text_prefixes", side_effect=fail_after_rewriting):
                with self.assertRaises(OSError):
                    migrate_library(src, dest)
            self.assertTrue(song.is_file())
            self.assertEqual(voice.read_bytes(), original_voice)
            with sqlite3.connect(src / "aicollector.db") as connection:
                audio, stems = connection.execute("SELECT audio_path, stems_json FROM tracks").fetchone()
            self.assertEqual(audio, str(song))
            self.assertEqual(json.loads(stems)["vocal"], str(song))
            self.assertFalse(has_library(dest))

    def test_interrupted_rewrite_is_recovered_before_destination_is_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            self._library(src)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(dest)}), encoding="utf-8")
            with patch.object(data_root, "rewrite_text_prefixes", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    migrate_library(src, dest)
            with patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(cfg)}):
                self.assertEqual(resolve_data_dir(src), dest)
            with sqlite3.connect(dest / "aicollector.db") as connection:
                audio = connection.execute("SELECT audio_path FROM tracks").fetchone()[0]
            self.assertEqual(audio, str(dest / "files" / "song.wav"))
            metadata = json.loads((dest / "voices" / "abc" / "voice.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["checkpoint"], str(dest / "voices" / "abc" / "model.pth"))
            self.assertFalse((src / "files").exists())

    def test_json_paths_are_decoded_and_only_complete_root_prefixes_are_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old\\library", root / "new\\library"
            self._library(src)
            song = str(src / "files" / "song.wav")
            unrelated = str(src) + "-other/files/song.wav"
            with sqlite3.connect(src / "aicollector.db") as connection:
                connection.execute("UPDATE tracks SET stems_json = ?", (json.dumps({"vocal": song, "other": unrelated}),))
                connection.execute("CREATE TABLE projects (data_json TEXT)")
                connection.execute("INSERT INTO projects VALUES (?)", (json.dumps({"paths": [song, unrelated], "note": "Mention " + song}),))
            migrate_library(src, dest)
            with sqlite3.connect(dest / "aicollector.db") as connection:
                stems = json.loads(connection.execute("SELECT stems_json FROM tracks").fetchone()[0])
                project = json.loads(connection.execute("SELECT data_json FROM projects").fetchone()[0])
            self.assertEqual(stems["vocal"], str(dest / "files" / "song.wav"))
            self.assertEqual(stems["other"], unrelated)
            self.assertEqual(project["paths"], [str(dest / "files" / "song.wav"), unrelated])
            self.assertEqual(project["note"], "Mention " + song)

    def test_interruption_while_backing_up_metadata_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            self._library(src)
            with patch.object(data_root.shutil, "copy2", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    migrate_library(src, dest)
            migrate_library(src, dest)
            self.assertTrue((dest / "files" / "song.wav").is_file())
            self.assertFalse((src / "files").exists())

    def test_cross_volume_partial_copy_is_recovered_without_losing_originals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            song, _ = self._library(src)
            rename = Path.rename

            def cross_volume(path: Path, target: Path) -> Path:
                if path.parent == src and target.parent == dest:
                    raise OSError(errno.EXDEV, "different volumes")
                return rename(path, target)

            def partial_copy(source: Path, target: Path, *, symlinks: bool, copy_function: Callable[[str | Path, str | Path], None]) -> None:
                target.mkdir()
                (target / "incomplete.wav").write_bytes(b"partial")
                raise KeyboardInterrupt()

            with patch.object(Path, "rename", autospec=True, side_effect=cross_volume):
                with patch.object(data_root.shutil, "copytree", side_effect=partial_copy):
                    with self.assertRaises(KeyboardInterrupt):
                        migrate_library(src, dest)
                self.assertEqual(song.read_bytes(), b"audio")
                migrate_library(src, dest)
            self.assertEqual((dest / "files" / "song.wav").read_bytes(), b"audio")
            self.assertFalse((dest / "files" / "incomplete.wav").exists())
            self.assertFalse((src / "files").exists())

    def test_interruption_after_commit_keeps_the_destination_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            self._library(src)
            with patch.object(data_root, "_finish_migration", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    migrate_library(src, dest)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(dest), "active_data_dir": str(src)}), encoding="utf-8")
            with patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(cfg)}):
                self.assertEqual(resolve_data_dir(src), dest)
            self.assertTrue((dest / "files" / "song.wav").is_file())
            self.assertFalse((dest / data_root._JOURNAL).exists())

    def test_selecting_an_existing_library_preserves_both_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "existing"
            self._library(src)
            self._library(dest)
            (dest / "files" / "song.wav").write_bytes(b"existing")
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(dest)}), encoding="utf-8")
            with patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(cfg)}):
                self.assertEqual(resolve_data_dir(src), dest)
            self.assertEqual((src / "files" / "song.wav").read_bytes(), b"audio")
            self.assertEqual((dest / "files" / "song.wav").read_bytes(), b"existing")

    def test_an_untrusted_destination_journal_cannot_delete_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            song, _ = self._library(src)
            (dest / "files").mkdir(parents=True)
            (dest / data_root._JOURNAL).write_text(json.dumps({
                "source": str(src), "destination": str(dest),
                "names": ["files", "voices", "aicollector.db"], "phase": "complete", "token": "0" * 32,
            }), encoding="utf-8")
            with self.assertRaises(DataDirError) as rejected:
                migrate_library(src, dest)
            self.assertEqual(rejected.exception.code, "migration_incomplete")
            self.assertEqual(song.read_bytes(), b"audio")

    def test_changing_the_pending_destination_after_interruption_recovers_the_active_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, first, second = (root / name for name in ("old", "first", "second"))
            self._library(src)
            with patch.object(data_root, "rewrite_text_prefixes", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    migrate_library(src, first)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(second), "active_data_dir": str(src)}), encoding="utf-8")
            with patch.dict(os.environ, {"OPENFABRIC_CONFIG": str(cfg)}):
                self.assertEqual(resolve_data_dir(src), second)
            self.assertTrue((second / "files" / "song.wav").is_file())
            self.assertFalse(has_library(first))

    def test_migration_does_not_overwrite_an_existing_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            self._library(src)
            dest.mkdir()
            (dest / "models").symlink_to(root / "missing", target_is_directory=True)
            with self.assertRaises(DataDirError) as rejected:
                migrate_library(src, dest)
            self.assertEqual(rejected.exception.code, "destination_not_empty")
            self.assertTrue((dest / "models").is_symlink())
            self.assertTrue((src / "files" / "song.wav").is_file())

    def test_disk_full_during_cross_volume_copy_preserves_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src, dest = root / "old", root / "new"
            song, voice = self._library(src)
            original_voice = voice.read_bytes()
            rename = Path.rename

            def cross_volume(path: Path, target: Path) -> Path:
                if path.parent == src and target.parent == dest:
                    raise OSError(errno.EXDEV, "different volumes")
                return rename(path, target)

            def disk_full(source: Path, target: Path, *, symlinks: bool, copy_function: Callable[[str | Path, str | Path], None]) -> None:
                target.mkdir()
                raise OSError(errno.ENOSPC, "disk full")

            with patch.object(Path, "rename", autospec=True, side_effect=cross_volume):
                with patch.object(data_root.shutil, "copytree", side_effect=disk_full):
                    with self.assertRaises(DataDirError) as rejected:
                        migrate_library(src, dest)
            self.assertEqual(rejected.exception.code, "insufficient_space")
            self.assertEqual(song.read_bytes(), b"audio")
            self.assertEqual(voice.read_bytes(), original_voice)
            self.assertFalse(has_library(dest))

    def test_validate_rejects_a_relative_path_and_an_engine_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = root / "engine"
            engine.mkdir()
            with self.assertRaises(DataDirError) as relative:
                validate_data_dir("library", [engine])
            self.assertEqual(relative.exception.code, "absolute")
            with self.assertRaises(DataDirError) as inside:
                validate_data_dir(str(engine / "nested"), [engine])
            self.assertEqual(inside.exception.code, "inside_engine")
            self.assertFalse((engine / "nested").exists())

    def test_validate_does_not_create_a_rejected_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp) / "engine"
            engine.mkdir()
            missing = engine / "new" / "library"
            with self.assertRaises(DataDirError) as rejected:
                validate_data_dir(str(missing), [engine])
            self.assertEqual(rejected.exception.code, "inside_engine")
            self.assertFalse(missing.exists())
            self.assertFalse((engine / "new").exists())

    def test_migrate_moves_songs_and_rewrites_their_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            dest = root / "new"
            (src / "files" / "ace_step").mkdir(parents=True)
            song = src / "files" / "ace_step" / "song.mp3"
            song.write_bytes(b"audio")
            (src / "voices").mkdir()
            db_path = src / "aicollector.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, audio_path TEXT, abc_path TEXT)")
            connection.execute(
                "INSERT INTO tracks (audio_path, abc_path) VALUES (?, ?)",
                (str(song.resolve()), None),
            )
            connection.commit()
            connection.close()

            migrate_library(src, dest)

            moved = dest / "files" / "ace_step" / "song.mp3"
            self.assertTrue(moved.is_file())
            self.assertFalse(song.exists())
            self.assertTrue(has_library(dest))
            self.assertFalse((src / "aicollector.db").exists())
            connection = sqlite3.connect(dest / "aicollector.db")
            stored = connection.execute("SELECT audio_path FROM tracks").fetchone()[0]
            connection.close()
            self.assertEqual(stored, str(moved.resolve()))

    def test_rewrite_is_a_prefix_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "src"
            dest = root / "dest"
            src.mkdir()
            dest.mkdir()
            db_path = dest / "aicollector.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE tracks (audio_path TEXT)")
            connection.execute(
                "INSERT INTO tracks (audio_path) VALUES (?)",
                (str((src / "files" / "a.wav").resolve()),),
            )
            connection.commit()
            connection.close()
            rewrite_library_paths(db_path, src, dest)
            connection = sqlite3.connect(db_path)
            stored = connection.execute("SELECT audio_path FROM tracks").fetchone()[0]
            connection.close()
            self.assertEqual(stored, str((dest / "files" / "a.wav").resolve()))

    def test_migrate_rejects_a_nested_folder_and_a_partial_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            (src / "files").mkdir(parents=True)
            with self.assertRaises(DataDirError) as nested:
                migrate_library(src, src / "files" / "deeper")
            self.assertEqual(nested.exception.code, "nested")
            self.assertTrue((src / "files").is_dir())

            dest = root / "partial"
            (dest / "models").mkdir(parents=True)
            with self.assertRaises(DataDirError) as occupied:
                migrate_library(src, dest)
            self.assertEqual(occupied.exception.code, "destination_not_empty")
            self.assertTrue((src / "files").is_dir())
            self.assertFalse((dest / "files").exists())

    def test_migrate_rewrites_voice_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            dest = root / "new"
            voice = src / "voices" / "abc"
            voice.mkdir(parents=True)
            checkpoint = (voice / "runs" / "model.pth").resolve()
            (voice / "voice.json").write_text(
                json.dumps({"checkpoint": str(checkpoint)}),
                encoding="utf-8",
            )
            runs = (voice / "runs").resolve().as_posix()
            (voice / "config.yml").write_text(f'log_dir: "{runs}"\n', encoding="utf-8")
            (src / "files").mkdir()
            migrate_library(src, dest)
            meta = json.loads((dest / "voices" / "abc" / "voice.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["checkpoint"], str((dest / "voices" / "abc" / "runs" / "model.pth").resolve()))
            config = (dest / "voices" / "abc" / "config.yml").read_text(encoding="utf-8")
            self.assertIn((dest / "voices" / "abc" / "runs").resolve().as_posix(), config)
            self.assertNotIn(str(src.resolve()), config)

    def test_resolve_uses_a_temp_config_and_leaves_the_home_config_alone(self) -> None:
        home = Path.home() / ".openfabric" / "config.json"
        existed = home.exists()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            dest = root / "new"
            (src / "files").mkdir(parents=True)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(dest)}), encoding="utf-8")
            previous = os.environ.get("OPENFABRIC_CONFIG")
            os.environ["OPENFABRIC_CONFIG"] = str(cfg)
            try:
                chosen = resolve_data_dir(src)
            finally:
                if previous is None:
                    os.environ.pop("OPENFABRIC_CONFIG", None)
                else:
                    os.environ["OPENFABRIC_CONFIG"] = previous
            self.assertEqual(chosen.resolve(), dest.resolve())
            self.assertTrue((dest / "files").is_dir())
            self.assertFalse((src / "files").exists())
            self.assertEqual(home.exists(), existed)

    def test_resolve_keeps_the_old_library_when_the_destination_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            dest = root / "new"
            (src / "files").mkdir(parents=True)
            (dest / "models").mkdir(parents=True)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(dest), "error": ""}), encoding="utf-8")
            previous = os.environ.get("OPENFABRIC_CONFIG")
            os.environ["OPENFABRIC_CONFIG"] = str(cfg)
            try:
                chosen = resolve_data_dir(src)
            finally:
                if previous is None:
                    os.environ.pop("OPENFABRIC_CONFIG", None)
                else:
                    os.environ["OPENFABRIC_CONFIG"] = previous
            self.assertEqual(chosen.resolve(), src.resolve())
            self.assertTrue((src / "files").is_dir())
            saved = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(saved["error"], "destination_not_empty")

    def test_place_seed_models_preserves_an_absent_optional_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / 'not-installed'
            with patch('app.data_root.logger.exception') as log:
                dest = place_seed_models(root / 'library', seed)
            self.assertTrue(dest.is_dir())
            self.assertFalse(seed.exists())
            log.assert_not_called()

    def test_place_seed_models_moves_weights_and_retargets_a_stale_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            seed = root / "seed"
            legacy = seed / "checkpoints"
            (legacy / "hf").mkdir(parents=True)
            (legacy / "hf" / "weights.bin").write_bytes(b"w")
            data = root / "data"
            dest = place_seed_models(data, seed)
            self.assertEqual(dest, data / "models" / "seed-vc")
            self.assertTrue((dest / "hf" / "weights.bin").is_file())
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(), dest.resolve())

            legacy.unlink()
            legacy.symlink_to(root / "missing-old")
            place_seed_models(data, seed)
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(), dest.resolve())
            self.assertTrue((dest / "hf" / "weights.bin").is_file())

    def test_place_seed_models_does_not_replace_a_real_checkpoint_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            seed = root / "seed"
            legacy = seed / "checkpoints"
            legacy.mkdir(parents=True)
            (legacy / "keep.txt").write_text("keep", encoding="utf-8")
            data = root / "data"
            (data / "models" / "seed-vc").mkdir(parents=True)
            place_seed_models(data, seed)
            self.assertFalse(legacy.is_symlink())
            self.assertEqual((legacy / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_layout_creates_videos_and_adopts_old_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data = root / "data"
            legacy = root / "old-logs"
            legacy.mkdir()
            (legacy / "ace_step_api.log").write_text("log", encoding="utf-8")
            (data / "logs").mkdir(parents=True)
            (data / "logs" / "kept.log").write_text("kept", encoding="utf-8")
            ensure_layout(data, legacy)
            self.assertTrue((data / "videos").is_dir())
            self.assertTrue((data / "voices").is_dir())
            self.assertEqual((data / "logs" / "ace_step_api.log").read_text(encoding="utf-8"), "log")
            self.assertEqual((data / "logs" / "kept.log").read_text(encoding="utf-8"), "kept")
            self.assertFalse(legacy.exists())

    def test_adopt_leaves_a_log_that_is_already_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            legacy = root / "old"
            dest = root / "logs"
            legacy.mkdir()
            dest.mkdir()
            (legacy / "same.log").write_text("old", encoding="utf-8")
            (dest / "same.log").write_text("new", encoding="utf-8")
            adopt_legacy_logs(legacy, dest)
            self.assertEqual((dest / "same.log").read_text(encoding="utf-8"), "new")
            self.assertTrue((legacy / "same.log").is_file())

    def test_migrate_moves_videos_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "old"
            dest = root / "new"
            (src / "videos").mkdir(parents=True)
            (src / "videos" / "clip.mp4").write_bytes(b"v")
            (src / "logs").mkdir()
            (src / "logs" / "run.log").write_text("x", encoding="utf-8")
            (src / "files").mkdir()
            migrate_library(src, dest)
            self.assertTrue((dest / "videos" / "clip.mp4").is_file())
            self.assertEqual((dest / "logs" / "run.log").read_text(encoding="utf-8"), "x")
            self.assertFalse((src / "videos").exists())

    def test_status_lists_the_whole_app_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cfg = root / "config.json"
            cfg.write_text("{}", encoding="utf-8")
            previous = os.environ.get("OPENFABRIC_CONFIG")
            os.environ["OPENFABRIC_CONFIG"] = str(cfg)
            try:
                status = library_status(root / "data")
            finally:
                if previous is None:
                    os.environ.pop("OPENFABRIC_CONFIG", None)
                else:
                    os.environ["OPENFABRIC_CONFIG"] = previous
            self.assertEqual(
                [folder["key"] for folder in status["folders"]],
                ["tracks", "voices", "videos", "audiobooks", "speechProfiles", "speechTrials", "models", "logs", "database"],
            )

    def test_parse_chosen_folder_strips_the_dialog_slash(self) -> None:
        self.assertEqual(parse_chosen_folder("/Users/example/Music/OpenFabric/\n"), "/Users/example/Music/OpenFabric")
        self.assertEqual(parse_chosen_folder("/"), "/")
