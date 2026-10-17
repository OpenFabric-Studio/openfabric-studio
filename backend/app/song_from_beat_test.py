"""ACE preflight never starts a model or substitutes a source/model."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib

import numpy as np
import soundfile as sf

from app import config, db, audio_versions, song_sources, song_capabilities, song_from_beat
from app.music_analysis_contracts import VersionMusicSource
from app.music_queue_contracts import MusicQueueError
from app.song_from_beat_contracts import AceLegoConditioning
from app.module_catalog import ModuleEnvironment, engine_python


class AceLaunchTests(unittest.TestCase):
    def test_selected_base_launch_is_direct_offline_and_local_to_the_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = ModuleEnvironment.for_root(Path(directory), platform='darwin', architecture='arm64')
            original = config.MODELS['ace_step']
            definition = replace(original, processes=[replace(original.processes[0], cwd=env.paths['ace_step'])])
            python = engine_python(env.paths['ace_step'], env.platform)
            python.parent.mkdir(parents=True)
            python.write_bytes(b'fixture')
            with patch.dict(config.MODELS, {'ace_step': definition}):
                specs = song_from_beat.launch_specs(env)
                self.assertEqual(specs[0].cmd[:2], [str(python), str(song_capabilities.bootstrap_path())])
                self.assertEqual(specs[0].env['ACESTEP_CONFIG_PATH'], 'acestep-v15-base')
                self.assertEqual(specs[0].env['ACESTEP_NO_INIT'], 'true')
                self.assertEqual(specs[0].env['HF_HUB_OFFLINE'], '1')
                self.assertEqual(specs[0].env['ACESTEP_CHECKPOINTS_DIR'], str(env.paths['ace_step'].resolve() / 'checkpoints'))
                self.assertEqual(config.MODELS['ace_step'], definition)


class AcePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_source_and_unknown_or_insufficient_disk_fail_before_native_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', root), \
                 patch.object(db, 'DB_PATH', root / 'catalog.db'), patch.object(db, 'FILES_DIR', root / 'files'):
                connection = db.get_db()
                await song_sources.recover()
                try:
                    state = song_capabilities.capabilities().model_copy(deep=True)
                    state = state.model_copy(update={'modes': [row.model_copy(update={'installed': True,
                        'weights_ready': True, 'ready': True, 'reason': None}) for row in state.modes]})
                    db.FILES_DIR.mkdir(exist_ok=True)
                    for frames in (4800, 480000):
                        path = db.FILES_DIR / (str(frames) + '.wav')
                        sf.write(path, np.full((frames, 2), .01, dtype=np.float32), 48000, subtype='FLOAT')
                        identifier = db.insert_track(model='upload', title='Input', lyrics='', seed=None,
                            duration_ms=frames // 48, wall_ms=None, params={}, audio_path=path, abc_path=None)
                        version = audio_versions.retain_original(identifier, path)
                        original = audio_versions.resolve_source(identifier, version.id)
                        options = AceLegoConditioning(mode='ace_lego', source=VersionMusicSource(track_id=identifier,
                            version_id=version.id), source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(), track_name='bass')
                        with patch.object(song_capabilities, 'capabilities', return_value=state):
                            if frames == 4800:
                                with self.assertRaises(MusicQueueError) as error:
                                    await song_from_beat.preview(options)
                                self.assertEqual(error.exception.code, 'conditioning_source_invalid')
                            else:
                                for free in (None, 0, 100 * 1024**3):
                                    with self.subTest(free=free), patch.object(song_from_beat, 'available_bytes', return_value=free):
                                        checked = await song_from_beat.preview(options)
                                        self.assertEqual(checked.can_start, free is not None and free > 0)
                                        self.assertEqual(checked.error_code, None if checked.can_start else 'insufficient_disk')
                finally:
                    await song_sources.shutdown()
                    connection.close()
