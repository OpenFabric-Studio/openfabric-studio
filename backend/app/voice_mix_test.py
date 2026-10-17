from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import audio_versions, db, voice_mix
from app.voice_artifacts import ModelArtifact
from app.artist_production_contracts import ModelFileIdentity, VoiceModelSnapshot
from app.music_analysis_contracts import VersionMusicSource
from app.voice_mix_contracts import VoiceMixSettings
from scripts.music_analysis_worker import run_command


class VoiceMixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                            ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        db.FILES_DIR.mkdir()
        self.rate = 48000
        time = np.arange(3 * self.rate) / self.rate
        vocal = np.asarray(.1 * np.sin(2 * np.pi * 440 * time), dtype=np.float32)
        self.vocal = db.FILES_DIR / 'vocal.wav'
        self.converted = db.FILES_DIR / 'converted.wav'
        self.backing = db.FILES_DIR / 'backing.wav'
        sf.write(self.vocal, np.column_stack((vocal, -vocal)), self.rate, subtype='FLOAT')
        sf.write(self.converted, np.column_stack((vocal / 2, -vocal / 2)), self.rate, subtype='FLOAT')
        sf.write(self.backing, np.column_stack((vocal / 4, vocal / 4)), self.rate, subtype='FLOAT')
        self.track = db.insert_track(model='upload', title='Fixture', lyrics='', seed=None, duration_ms=3000,
            wall_ms=None, params={}, audio_path=self.vocal, abc_path=None)
        original = audio_versions.retain_original(self.track, self.vocal)
        self.source = VersionMusicSource(track_id=self.track, version_id=original.id)
        self.source_path = audio_versions.resolve_source(self.track, original.id)
        self.version = audio_versions.prepare_application(self.track, 'a' * 32)
        _, self.destination = audio_versions.application_paths(self.track, self.version.id)
        self.model = VoiceModelSnapshot(voice_id='a' * 32, model_revision_id='fixture', diffusion_steps=30,
            files=[ModelFileIdentity(role=role, name=role, sha256='b' * 64)
                for role in ('checkpoint', 'configuration', 'voice_reference')])

    async def asyncTearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    async def test_measured_convert_retains_dry_artifacts_and_reloadable_report(self) -> None:
        original = self.vocal.read_bytes(), self.converted.read_bytes(), self.backing.read_bytes()
        report = await voice_mix.render_converted_mix(VoiceMixSettings(), self.source, self.source_path,
            self.vocal, self.converted, [self.backing], self.version.id, self.destination, self.model, run_command)
        self.assertAlmostEqual(report.applied_gain_db, 6.0206, delta=.1)
        self.assertEqual(report.mix.duration_frames, self.rate * 3)
        self.assertEqual(report.settings.loudness.profile, 'off')
        self.assertEqual(voice_mix.read_report(self.destination, self.version.id), report)
        roles = {artifact.role for artifact in report.artifacts}
        self.assertEqual(roles, {'original_vocal', 'converted_dry', 'backing', 'matched_vocal', 'master'})
        for artifact in report.artifacts:
            path = await voice_mix.artifact_path(self.destination, self.version.id, artifact.id)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact.sha256)
        self.assertEqual((self.vocal.read_bytes(), self.converted.read_bytes(), self.backing.read_bytes()), original)
        from app.music_dsp import MusicDspError
        dry = report.artifacts[0]
        retained = await voice_mix.artifact_path(self.destination, self.version.id, dry.id)
        retained.write_bytes(b'changed retained bytes')
        with self.assertRaises(MusicDspError):
            await voice_mix.artifact_path(self.destination, self.version.id, dry.id)
        self.assertEqual(self.vocal.read_bytes(), original[0])

    async def test_valid_report_cannot_be_rebound_to_another_catalog_source(self) -> None:
        from app.atomic_files import write_object
        from app.contracts import JsonObject
        from app.music_dsp import MusicDspError
        from pydantic import TypeAdapter
        report = await voice_mix.render_converted_mix(VoiceMixSettings(), self.source, self.source_path,
            self.vocal, self.converted, [self.backing], self.version.id, self.destination, self.model, run_command)
        forged = report.model_dump(mode='json')
        forged['source'] = VersionMusicSource(track_id=self.track + 100, version_id='f' * 32).model_dump()
        root = voice_mix._root(self.destination, self.version.id)
        write_object(root / 'current.json', TypeAdapter(JsonObject).validate_python(forged))
        with self.assertRaises(MusicDspError):
            voice_mix.read_report(self.destination, self.version.id)

    async def test_low_disk_refuses_before_copy_or_command_and_preserves_inputs(self) -> None:
        from app.music_dsp import MusicDspError
        run = AsyncMock(side_effect=AssertionError('low disk launched work'))
        with patch.object(voice_mix, '_disk', side_effect=MusicDspError('dsp_insufficient_disk')):
            with self.assertRaises(MusicDspError):
                await voice_mix.render_converted_mix(VoiceMixSettings(), self.source, self.source_path,
                    self.vocal, self.converted, [self.backing], self.version.id, self.destination, self.model, run)
        self.assertIsNone(voice_mix.read_report(self.destination, self.version.id))
        run.assert_not_awaited()

    def test_settings_are_persisted_per_version_and_conflicting_retry_cannot_replace_them(self) -> None:
        from app.music_dsp import MusicDspError
        settings = VoiceMixSettings(vocal_level='manual', manual_gain_db=-3.0)
        voice_mix.configure(self.destination, self.version.id, settings)
        self.assertEqual(voice_mix.read_settings(self.destination, self.version.id), settings)
        with self.assertRaises(MusicDspError):
            voice_mix.configure(self.destination, self.version.id, VoiceMixSettings())
        self.assertEqual(voice_mix.read_settings(self.destination, self.version.id), settings)

    async def test_admission_pins_model_files_and_mutation_is_rejected_before_observation(self) -> None:
        from app.music_dsp import MusicDspError
        checkpoint, config, reference, engine = [self.root / name for name in ('model.pth', 'model.yml', 'ref.wav', 'inference.py')]
        for path in (checkpoint, config, reference, engine):
            path.write_bytes(path.name.encode())
        pinned = voice_mix.pin_conversion(ModelArtifact(checkpoint, config, 0, 'trained', reference),
            'a' * 32, 'trained', engine, 30)
        snapshot = await voice_mix.observe_conversion(pinned)
        self.assertEqual(len(snapshot.files), 4)
        checkpoint.write_bytes(b'modified checkpoint')
        with self.assertRaises(MusicDspError):
            await voice_mix.observe_conversion(pinned)

    async def test_malformed_converted_audio_fails_typed_before_commands(self) -> None:
        from app.music_dsp import MusicDspError
        self.converted.write_bytes(b'not audio')
        run = AsyncMock(side_effect=AssertionError('malformed audio reached command'))
        with self.assertRaises(MusicDspError) as error:
            await voice_mix.render_converted_mix(VoiceMixSettings(), self.source, self.source_path,
                self.vocal, self.converted, [self.backing], self.version.id, self.destination, self.model, run)
        self.assertEqual(error.exception.code, 'dsp_invalid_source')
        run.assert_not_awaited()

    def test_conversion_identity_is_frozen_for_version_retry(self) -> None:
        from app.music_dsp import MusicDspError
        voice_mix.configure_conversion(self.destination, self.version.id, self.model)
        self.assertEqual(voice_mix.read_conversion(self.destination, self.version.id), self.model)
        changed = self.model.model_copy(update={'model_revision_id': 'different-model'})
        with self.assertRaises(MusicDspError):
            voice_mix.configure_conversion(self.destination, self.version.id, changed)
        self.assertEqual(voice_mix.read_conversion(self.destination, self.version.id), self.model)

    async def test_failed_artifact_sync_does_not_publish_report_or_replace_original(self) -> None:
        original = self.vocal.read_bytes()
        with patch.object(voice_mix, '_sync_artifacts', side_effect=OSError('disk sync failed'), create=True):
            with self.assertRaises(OSError):
                await voice_mix.render_converted_mix(VoiceMixSettings(), self.source, self.source_path,
                    self.vocal, self.converted, [self.backing], self.version.id, self.destination, self.model, run_command)
        self.assertIsNone(voice_mix.read_report(self.destination, self.version.id))
        self.assertEqual(self.vocal.read_bytes(), original)
