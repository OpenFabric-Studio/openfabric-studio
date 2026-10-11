"""Library relocation and damaged configuration use private, owned fixtures."""
from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, patch

from app import data_root


class ConfigurationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config = self.root / 'config.json'
        self.enterContext(patch.dict(os.environ, {
            'OPENFABRIC_CONFIG': str(self.config), 'OPENFABRIC_DATA_DIR': str(self.root / 'default'),
            'SEED_VC_DIR': str(self.root / 'engine'), 'OPENFABRIC_MODULE_ROOT': str(self.root / 'modules'),
        }))

    def test_malformed_config_keeps_original_bytes_and_does_not_open_default(self) -> None:
        for payload in (b'{"active_data_dir":"/lost-pointer"', b'[]', b'null', b'\xff',
                        b'{"active_data_dir":42}', b'{"data_dir":"relative/library"}', b'{"data_dir":null}'):
            with self.subTest(payload=payload):
                self.config.write_bytes(payload)
                with self.assertRaises(data_root.DataDirError) as rejected:
                    data_root.resolve_data_dir(self.root / 'default')
                self.assertEqual(rejected.exception.code, 'invalid_config')
                self.assertEqual(self.config.read_bytes(), payload)
                self.assertFalse((self.root / 'default').exists())

    def test_unreadable_config_keeps_original_and_rejects_setting_changes(self) -> None:
        self.config.write_text('{"active_data_dir":"/preserve-this"}')
        before = self.config.read_bytes()
        read_text = Path.read_text
        def unreadable(path: Path, *args: object, **kwargs: object) -> str:
            if path == self.config:
                raise PermissionError('injected inaccessible config')
            return read_text(path)
        with patch.object(Path, 'read_text', autospec=True, side_effect=unreadable):
            for action in (lambda: data_root.resolve_data_dir(self.root / 'default'),
                           lambda: data_root.save_data_dir(self.root / 'new')):
                with self.assertRaises(data_root.DataDirError) as rejected:
                    action()
                self.assertEqual(rejected.exception.code, 'config_unreadable')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.root / 'default').exists())

    def test_missing_config_still_creates_first_run_pointer(self) -> None:
        self.assertEqual(data_root.resolve_data_dir(self.root / 'default'), self.root / 'default')
        self.assertEqual(data_root.read_config()['active_data_dir'], str(self.root / 'default'))


class LibraryRelocationTests(unittest.IsolatedAsyncioTestCase):
    fixtures: ClassVar[tempfile.TemporaryDirectory[str]]
    native_bytes: ClassVar[bytes]

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = tempfile.TemporaryDirectory()
        path = Path(cls.fixtures.name) / 'fixture.avi'
        subprocess.run([shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=32x24:r=24:d=2', '-c:v', 'mpeg4', str(path)], check=True)
        cls.native_bytes = path.read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixtures.cleanup()

    async def asyncSetUp(self) -> None:
        from app import db, video_projects, video_scenes
        from app.video_contracts import CreateVideoProjectRequest
        from app.voice_profile_test_fixtures import wav_bytes
        self.db, self.projects, self.scenes = db, video_projects, video_scenes
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.old, self.new = self.root / 'old', self.root / 'new'
        self.enterContext(patch.dict(os.environ, {
            'OPENFABRIC_CONFIG': str(self.root / 'config.json'), 'OPENFABRIC_DATA_DIR': str(self.old),
            'SEED_VC_DIR': str(self.root / 'engine'), 'OPENFABRIC_MODULE_ROOT': str(self.root / 'modules'),
        }))
        for name, value in [('DATA_DIR', self.old), ('DB_PATH', self.old / 'aicollector.db'),
                            ('FILES_DIR', self.old / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(video_projects, 'DATA_DIR', self.old))
        self.enterContext(patch.object(video_scenes, 'DATA_DIR', self.old))
        self.song = self.old / 'files' / 'song.wav'
        self.song.parent.mkdir(parents=True)
        self.song.write_bytes(wav_bytes())
        track = db.insert_track(model='upload', title='Migration fixture', lyrics='', seed=None,
            duration_ms=2000, wall_ms=None, params={}, audio_path=self.song, abc_path=None)
        with patch('app.video_jobs._probe_duration', new=AsyncMock(return_value=2)):
            self.project = await video_projects.create(CreateVideoProjectRequest(track_id=track))
        document = video_projects.load(self.project.id)
        document.undo_history = [video_projects.VideoHistorySnapshot(project=self.project.model_copy(deep=True), source=document.source)]
        video_projects.save(document)
        for name in ('video_characters', 'video_character_training', 'providers'):
            (self.old / name).mkdir()
            (self.old / name / 'evidence.json').write_text('{"fixture":"retained"}')
        (self.old / 'models').mkdir()
        (self.old / 'models' / 'trainer.py').write_text('owned trainer fixture')
        (self.old / 'video_character_trainer.json').write_text(json.dumps({'command': str(self.old / 'models' / 'trainer.py')}))
        (self.old / 'providers' / 'openrouter-settings.json').write_text('{"enabled":true,"estimate_limit_usd":3}')
        (self.old / 'providers' / 'openrouter-requests').mkdir()
        (self.old / 'providers' / 'openrouter-requests' / ('b' * 32 + '.json')).write_text('{"fixture":"receipt"}')
        self.native = self.old / 'files' / 'native.avi'
        self.native.write_bytes(self.native_bytes)
        self.external = self.root / 'external.avi'
        self.external.write_bytes(self.native_bytes)
        self._scene('c' * 32, self.native, 'native')
        self._scene('d' * 32, self.external, 'native')
        self._scene('e' * 32, self.native, 'upload')
        self.external_before = self.scenes._source('d' * 32).model_dump_json()
        if db._db is not None:
            db._db.close()
            db._db = None

    async def asyncTearDown(self) -> None:
        if self.db._db is not None:
            self.db._db.close()
            self.db._db = None

    def _scene(self, identifier: str, path: Path, origin: str) -> None:
        from app.video_scene_contracts import VideoSceneSource
        folder = self.old / 'videos' / 'scenes' / identifier
        folder.mkdir(parents=True)
        if origin == 'upload':
            (folder / 'source.video').write_bytes(path.read_bytes())
            source = folder / 'source.video'
            source_origin = 'upload'
        else:
            source, source_origin = path, 'native'
        document = self.scenes.StoredSource(source=VideoSceneSource(
            id=identifier, name='fixture.avi', origin=source_origin, duration_sec=2,
            width=32, height=24, fps=24, has_audio=False, bytes=source.stat().st_size, created_at=self.scenes.now()),
            path=str(source) if source_origin == 'native' else 'source.video', input_format='avi',
            signature=self.scenes._signature(source))
        (folder / 'source.json').write_text(document.model_dump_json())

    def _activate_destination(self) -> None:
        self.projects.DATA_DIR = self.new
        self.scenes.DATA_DIR = self.new
        self.db.DATA_DIR, self.db.DB_PATH, self.db.FILES_DIR = self.new, self.new / 'aicollector.db', self.new / 'files'

    def _assert_available(self) -> None:
        self.assertTrue((self.new / 'video_projects' / self.project.id / 'project.json').is_file())
        self._activate_destination()
        self.assertFalse(self.projects.get(self.project.id).source_changed)
        document = self.projects.load(self.project.id)
        self.assertIsNotNone(document.source)
        self.assertEqual(document.undo_history[0].source, document.source)
        for identifier in ('c' * 32, 'd' * 32, 'e' * 32):
            self.assertEqual(self.scenes.get_source(identifier).id, identifier)
        self.assertEqual(self.scenes._source('d' * 32).model_dump_json(), self.external_before)
        for name in ('video_projects', 'video_characters', 'video_character_training', 'providers', 'video_character_trainer.json'):
            self.assertTrue((self.new / name).exists(), name)
            self.assertFalse((self.old / name).exists(), name)
        command = json.loads((self.new / 'video_character_trainer.json').read_text())['command']
        self.assertEqual(command, str(self.new / 'models' / 'trainer.py'))
        self.assertEqual((self.new / 'providers' / 'openrouter-requests' / ('b' * 32 + '.json')).read_text(), '{"fixture":"receipt"}')

    async def test_move_preserves_video_stores_and_owned_sources(self) -> None:
        data_root.migrate_library(self.old, self.new)
        self._assert_available()

    async def test_cross_volume_move_rebinds_verified_source_identities(self) -> None:
        rename = Path.rename
        def cross_volume(path: Path, target: Path) -> Path:
            if path.parent == self.old and target.parent == self.new:
                raise OSError(errno.EXDEV, 'injected different volume')
            return rename(path, target)
        with patch.object(Path, 'rename', autospec=True, side_effect=cross_volume):
            data_root.migrate_library(self.old, self.new)
        self._assert_available()

    async def test_failed_video_rewrite_restores_all_original_metadata(self) -> None:
        original = {path.relative_to(self.old): path.read_bytes() for path in self.old.rglob('*.json')}
        rewrite = data_root.rewrite_text_prefixes
        def fail_video(path: Path, old: Path, new: Path) -> None:
            rewrite(path, old, new)
            if path.name == 'videos':
                raise OSError('injected video metadata publication failure')
        with patch.object(data_root, 'rewrite_text_prefixes', side_effect=fail_video):
            with self.assertRaises(OSError):
                data_root.migrate_library(self.old, self.new)
        for relative, payload in original.items():
            self.assertEqual((self.old / relative).read_bytes(), payload, str(relative))
        self.assertFalse(data_root.has_library(self.new))
        self.assertFalse(self.projects.get(self.project.id).source_changed)
        self.assertEqual(self.scenes.get_source('c' * 32).id, 'c' * 32)

    async def test_commit_recovery_retains_rebased_video_sources(self) -> None:
        with patch.object(data_root, '_finish_migration', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                data_root.migrate_library(self.old, self.new)
        self.root.joinpath('config.json').write_text(json.dumps({'data_dir': str(self.new), 'active_data_dir': str(self.old)}))
        self.assertEqual(data_root.resolve_data_dir(self.old), self.new)
        self._assert_available()

    async def test_already_changed_source_is_not_reauthorized_by_relocation(self) -> None:
        self.native.write_bytes(b'changed scene fixture')
        self.song.write_bytes(b'changed song fixture')
        data_root.migrate_library(self.old, self.new)
        self._activate_destination()
        self.assertTrue(self.projects.get(self.project.id).source_changed)
        with self.assertRaises(self.scenes.VideoSceneError) as rejected:
            self.scenes.get_source('c' * 32)
        self.assertEqual(rejected.exception.code, 'source_changed')

    async def test_source_changed_during_identity_capture_is_not_reauthorized(self) -> None:
        digest = data_root._source_digest
        changed = False
        def mutate_before_hash(path: Path) -> tuple[str, os.stat_result]:
            nonlocal changed
            if path == self.native and not changed:
                changed = True
                path.write_bytes(b'X' * path.stat().st_size)
            return digest(path)
        with patch.object(data_root, '_source_digest', side_effect=mutate_before_hash):
            data_root.migrate_library(self.old, self.new)
        self._activate_destination()
        with self.assertRaises(self.scenes.VideoSceneError) as rejected:
            self.scenes.get_source('c' * 32)
        self.assertEqual(rejected.exception.code, 'source_changed')

    async def test_corrupt_cross_volume_copy_rolls_back_with_original_evidence(self) -> None:
        rename, copy = Path.rename, data_root._copy_library_file
        original_metadata = (self.old / 'videos' / 'scenes' / ('c' * 32) / 'source.json').read_bytes()
        def cross_volume(path: Path, target: Path) -> Path:
            if path.parent == self.old and target.parent == self.new:
                raise OSError(errno.EXDEV, 'injected different volume')
            return rename(path, target)
        def corrupt_copy(src: str | Path, dest: str | Path, *, follow_symlinks: bool = True) -> None:
            copy(src, dest, follow_symlinks=follow_symlinks)
            if Path(src) == self.native:
                Path(dest).write_bytes(b'corrupted copied source')
        with patch.object(Path, 'rename', autospec=True, side_effect=cross_volume), \
             patch.object(data_root, '_copy_library_file', side_effect=corrupt_copy):
            with self.assertRaises(data_root.DataDirError) as rejected:
                data_root.migrate_library(self.old, self.new)
        self.assertEqual(rejected.exception.code, 'invalid_metadata')
        self.assertEqual(self.native.read_bytes(), self.native_bytes)
        self.assertEqual((self.old / 'videos' / 'scenes' / ('c' * 32) / 'source.json').read_bytes(), original_metadata)
        self.assertFalse(data_root.has_library(self.new))

    async def test_interrupted_identity_rewrite_rolls_back_before_retry(self) -> None:
        rebase = data_root._rebase_video_sources
        def interrupt_after_rebase(dest: Path, sources: list[data_root._SourceRebase]) -> None:
            rebase(dest, sources)
            raise KeyboardInterrupt()
        with patch.object(data_root, '_rebase_video_sources', side_effect=interrupt_after_rebase):
            with self.assertRaises(KeyboardInterrupt):
                data_root.migrate_library(self.old, self.new)
        data_root.migrate_library(self.old, self.new)
        self._assert_available()

    async def test_relocated_native_and_uploaded_scenes_can_still_render_previews(self) -> None:
        from app.video_scene_contracts import VideoScenePreviewRequest
        from app.video_media import probe_media
        data_root.migrate_library(self.old, self.new)
        self._activate_destination()
        with patch.object(self.scenes, '_closing', False):
            try:
                for identifier in ('c' * 32, 'e' * 32):
                    preview = await self.scenes.preview(identifier, VideoScenePreviewRequest(start_sec=0))
                    media = self.scenes.preview_media(identifier, preview.id)
                    info = await probe_media(media)
                    self.assertAlmostEqual(info.video_duration, 2, delta=0.1)
                    self.assertEqual((info.width, info.height), (32, 24))
            finally:
                await self.scenes.shutdown()

    async def test_history_snapshots_do_not_repeat_full_source_hashing(self) -> None:
        document = self.projects.load(self.project.id)
        document.undo_history = [document.undo_history[0].model_copy(deep=True) for _ in range(50)]
        self.projects.save(document)
        with patch.object(data_root, '_source_digest', wraps=data_root._source_digest) as digest:
            data_root.migrate_library(self.old, self.new)
        song_reads = [call for call in digest.call_args_list if call.args[0].name == 'song.wav']
        self.assertEqual(len(song_reads), 2, 'One source verification and one destination verification suffice for identical snapshots')
        self._assert_available()


class LibraryInventoryTests(unittest.TestCase):
    def test_each_active_store_is_detected_as_an_existing_library(self) -> None:
        for name in ('videos', 'video_projects', 'video_characters', 'video_character_training', 'providers'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / name).mkdir()
                self.assertTrue(data_root.has_library(root), name)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'video_character_trainer.json').write_text('{}')
            self.assertTrue(data_root.has_library(root))
