"""Recording ingestion measures bounded CPU fixtures and keeps held-out files separate."""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from pydantic import ValidationError

from app import db, recording_sessions as sessions, voice_build
from app.artist_production_test_fixtures import tone_wav_bytes
from app.recording_contracts import RecordingAudioInfo, RecordingLabels


class RecordingSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'), ('_db', None)):
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.root / 'voices'))
        self.enterContext(patch.object(voice_build, 'SEED_VC_DIR', self.root / 'seed-vc'))
        self.voice_id = 'a' * 32
        self.voice = self.root / 'voices' / self.voice_id
        self.training = self.voice / 'recordings'
        self.held_out = self.voice / 'trials' / 'sources'
        self.training.mkdir(parents=True)
        self.held_out.mkdir(parents=True)
        voice_build.write_meta(self.voice, voice_build.new_voice_meta(self.voice_id, 'Synthetic fixture'))
        self.labels = RecordingLabels(language='en', register='comfortable', articulation='legato', expression='neutral')
        sessions.migrate(db.get_db())
        self.addCleanup(self.close_database)

    def close_database(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def audio(self, directory: Path, name: str = 'dry.wav', *, frequency_hz: float = 220) -> Path:
        path = directory / name
        path.write_bytes(tone_wav_bytes(seconds=.4, frequency_hz=frequency_hz))
        return path

    def test_qc_preserves_original_bytes_actual_format_and_labels(self) -> None:
        path = self.audio(self.training)
        original = path.read_bytes()
        record = sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(record.original_filename, 'dry.wav')
        self.assertEqual(record.relative_path, 'recordings/dry.wav')
        self.assertEqual((record.format, record.sample_rate, record.channels, record.subtype), ('WAV', 44100, 1, 'PCM_16'))
        self.assertAlmostEqual(record.duration_sec, .4)
        self.assertEqual(record.labels, self.labels)
        self.assertAlmostEqual(record.qc.peak, .2, places=4)
        self.assertAlmostEqual(record.qc.rms_dbfs, 20 * math.log10(.2 / math.sqrt(2)), places=3)
        self.assertEqual(record.qc.clipped_fraction, 0)
        self.assertEqual(record.qc.near_silence_fraction, 0)
        self.assertTrue(record.qc.pitch_listening_required)
        self.assertIn('noise_estimate_is_amplitude_proxy', record.qc.warnings)
        self.assertEqual(sessions.list_sessions(self.voice_id), [record])

    def test_capture_guidance_does_not_rewrite_a_48khz_24bit_stereo_source(self) -> None:
        path = self.training / 'captured.wav'
        data = np.tile(np.array([.1, -.1], dtype=np.float32).reshape(-1, 1), (2400, 2))
        sf.write(path, data, 48000, subtype='PCM_24')
        original = path.read_bytes()
        record = sessions.inspect_file(path, self.voice_id, 'training', self.labels, original_filename='Singer take.wav')
        self.assertEqual((record.sample_rate, record.channels, record.subtype), (48000, 2, 'PCM_24'))
        self.assertEqual(record.original_filename, 'Singer take.wav')
        self.assertEqual(path.read_bytes(), original)

    def test_measurement_reads_bounded_blocks(self) -> None:
        path = self.audio(self.training)
        original_read = sf.SoundFile.read
        reads: list[int] = []
        def bounded_read(source: sf.SoundFile, frames: int = -1, *, dtype: Literal['float32'],
                         always_2d: bool = False) -> NDArray[np.float32]:
            self.assertGreater(frames, 0)
            self.assertLessEqual(frames, 4410)
            reads.append(frames)
            return original_read(source, frames, dtype=dtype, always_2d=always_2d)
        with patch.object(sf.SoundFile, 'read', new=bounded_read):
            sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertGreater(len(reads), 1)

    def test_silence_full_scale_and_noise_proxy_are_honest_amplitude_diagnostics(self) -> None:
        path = self.training / 'levels.wav'
        samples = np.concatenate((np.zeros(4410, dtype=np.float32), np.ones(4410, dtype=np.float32)))
        sf.write(path, samples, 44100, subtype='FLOAT')
        record = sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertEqual(record.qc.peak, 1)
        self.assertEqual(record.qc.clipped_fraction, .5)
        self.assertEqual(record.qc.near_silence_fraction, .5)
        self.assertLess(record.qc.noise_floor_proxy_dbfs, -100)
        self.assertIn('full_scale_samples', record.qc.warnings)

    def test_invalid_nonfinite_and_oversized_sources_leave_no_record(self) -> None:
        path = self.training / 'bad.wav'
        path.write_bytes(b'not audio')
        with self.assertRaises(sessions.RecordingError) as invalid:
            sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertEqual(invalid.exception.code, 'recording_invalid_audio')
        sf.write(path, np.array([float('nan')], dtype=np.float32), 44100, subtype='FLOAT')
        with self.assertRaises(sessions.RecordingError):
            sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        path = self.audio(self.training)
        with patch.object(sessions, 'MAX_AUDIO_BYTES', 40), self.assertRaises(sessions.RecordingError) as oversized:
            sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertEqual(oversized.exception.code, 'recording_too_large')
        self.assertEqual(sessions.list_sessions(self.voice_id), [])

    def test_duplicate_recording_keeps_first_metadata_and_rejects_renamed_copy(self) -> None:
        path = self.audio(self.training)
        original = sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        different_labels = RecordingLabels(language='es', expression='bright')
        self.assertEqual(sessions.inspect_file(path, self.voice_id, 'training', different_labels), original)
        renamed = self.training / 'renamed.wav'
        shutil.copyfile(path, renamed)
        with self.assertRaises(sessions.RecordingError) as duplicate:
            sessions.inspect_file(renamed, self.voice_id, 'training', different_labels)
        self.assertEqual(duplicate.exception.code, 'recording_duplicate')
        self.assertEqual(sessions.list_sessions(self.voice_id), [original])

    def test_held_out_history_cannot_be_relabelled_as_training_after_file_moves(self) -> None:
        held_out = self.audio(self.held_out, 'evaluation.wav')
        saved = sessions.inspect_file(held_out, self.voice_id, 'held_out', self.labels)
        training = self.training / 'renamed.wav'
        held_out.rename(training)
        with self.assertRaises(sessions.RecordingError) as ingestion:
            sessions.inspect_file(training, self.voice_id, 'training', self.labels)
        self.assertEqual(ingestion.exception.code, 'recording_held_out_overlap')
        with self.assertRaises(sessions.RecordingError) as build:
            sessions.require_training_sources(self.voice_id, [training.name])
        self.assertEqual(build.exception.code, 'recording_held_out_overlap')
        self.assertEqual(sessions.list_sessions(self.voice_id), [saved])

    def test_existing_trial_sources_are_protected_without_session_metadata(self) -> None:
        training = self.audio(self.training)
        held_out = self.held_out / 'different-name.wav'
        shutil.copyfile(training, held_out)
        with self.assertRaises(sessions.RecordingError) as build:
            sessions.require_training_sources(self.voice_id, [training.name])
        self.assertEqual(build.exception.code, 'recording_held_out_overlap')
        with self.assertRaises(sessions.RecordingError) as trial:
            sessions.require_held_out_file(self.voice_id, held_out)
        self.assertEqual(trial.exception.code, 'recording_training_overlap')

    def test_distinct_held_out_files_pass_and_do_not_enter_training_records(self) -> None:
        training = self.audio(self.training)
        held_out = self.audio(self.held_out, 'evaluation.wav', frequency_hz=330)
        sessions.require_training_sources(self.voice_id, [training.name])
        sessions.require_held_out_file(self.voice_id, held_out)
        record = sessions.inspect_file(held_out, self.voice_id, 'held_out', self.labels)
        self.assertEqual(record.purpose, 'held_out')
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [])

    def test_symlink_escape_wrong_root_and_traversal_are_rejected(self) -> None:
        outside = self.audio(self.root, 'private.wav')
        link = self.training / 'escape.wav'
        link.symlink_to(outside)
        for path, purpose in ((link, 'training'), (outside, 'training'), (self.audio(self.training), 'held_out')):
            with self.subTest(path=path, purpose=purpose), self.assertRaises(sessions.RecordingError) as caught:
                if purpose == 'training':
                    sessions.inspect_file(path, self.voice_id, 'training', self.labels)
                else:
                    sessions.inspect_file(path, self.voice_id, 'held_out', self.labels)
            self.assertEqual(caught.exception.code, 'recording_invalid_path')
        with self.assertRaises(sessions.RecordingError):
            sessions.require_training_sources(self.voice_id, ['../private.wav'])

    def test_changed_source_is_not_published(self) -> None:
        path = self.audio(self.training)
        measure = sessions._measure
        def changed(source: Path) -> RecordingAudioInfo:
            result = measure(source)
            source.write_bytes(tone_wav_bytes(seconds=.4, frequency_hz=440))
            return result
        with patch.object(sessions, '_measure', side_effect=changed), self.assertRaises(sessions.RecordingError) as caught:
            sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        self.assertEqual(caught.exception.code, 'recording_source_changed')
        self.assertEqual(sessions.list_sessions(self.voice_id), [])

    def test_training_rejects_changed_known_bytes_even_with_original_mtime_restored(self) -> None:
        path = self.audio(self.training)
        original = sessions.inspect_file(path, self.voice_id, 'training', self.labels)
        stamp = path.stat()
        path.write_bytes(tone_wav_bytes(seconds=.4, frequency_hz=440))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original.file_bytes)
        self.assertEqual(path.stat().st_mtime_ns, stamp.st_mtime_ns)
        with self.assertRaises(sessions.RecordingError) as caught:
            sessions.require_training_sources(self.voice_id, [path.name])
        self.assertEqual(caught.exception.code, 'recording_source_changed')

    def test_training_loads_known_session_identities_once_for_a_source_batch(self) -> None:
        first = self.audio(self.training, 'one.wav')
        second = self.audio(self.training, 'two.wav', frequency_hz=330)
        sessions.inspect_file(first, self.voice_id, 'training', self.labels)
        sessions.inspect_file(second, self.voice_id, 'training', self.labels)
        with patch.object(sessions, 'list_sessions', wraps=sessions.list_sessions) as known:
            sessions.require_training_sources(self.voice_id, [first.name, second.name])
        self.assertEqual(known.call_count, 1)

    def test_script_is_the_requested_45_training_minute_heuristic(self) -> None:
        script = sessions.recording_script()
        self.assertEqual(script.target_minutes, 45)
        self.assertEqual([category.id for category in script.categories], ['comfortable_phrases', 'pitch_vowels',
            'dynamics_articulation', 'spanish_lyrics', 'english_lyrics', 'expressiveness'])
        self.assertEqual([category.minutes for category in script.categories], [10, 10, 8, 7, 7, 3])
        self.assertEqual(sum(category.minutes for category in script.categories), 45)
        self.assertTrue(all(category.purpose == 'training' for category in script.categories))
        self.assertIn('separate', script.held_out_guidance)
        self.assertEqual(script.selection_max_minutes, 60)
        self.assertTrue(script.heuristic_only)

    def test_labels_reject_control_characters_and_unknown_fields(self) -> None:
        for values in ({'language': 'en\0'}, {'register': 'x' * 121}, {'verified_singer': True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                RecordingLabels.model_validate(values)


if __name__ == '__main__':
    unittest.main()
