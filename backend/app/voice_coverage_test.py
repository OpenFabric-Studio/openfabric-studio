"""CPU coverage measurements remain finite, independent, and explicitly limited."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError

import numpy as np
import soundfile as sf

from app.voice_contracts import VoiceCoverageMeasurement, VoicePitchBin

SCRIPT = Path(__file__).with_name('voice_coverage.py')


def tone(frequency: float, seconds: float = 1, sample_rate: int = 16000) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    times = np.arange(round(seconds * sample_rate), dtype=np.float64) / sample_rate
    return .2 * np.sin(2 * np.pi * frequency * times)


def measurement(note: int, seconds: float, rolloff: float | None = None, high_band: float | None = None) -> VoiceCoverageMeasurement:
    frequency = 440 * 2 ** ((note - 69) / 12)
    return VoiceCoverageMeasurement(duration_sec=seconds, analyzed_sec=seconds, voiced_sec=seconds,
        reliable_voiced_sec=seconds, pitch_p05_hz=frequency, pitch_median_hz=frequency,
        pitch_p95_hz=frequency, median_voicing_probability=.95, pitch_bins=[VoicePitchBin(midi_note=note, seconds=seconds)],
        source_sample_rate=44100, spectral_rolloff95_hz=rolloff, high_band_energy_fraction=high_band,
        warnings=['identity_unverified'])


class CoverageTests(unittest.TestCase):
    def test_backend_import_does_not_load_dsp_dependencies(self) -> None:
        completed = subprocess.run([sys.executable, '-c',
            "import sys; import app.voice_coverage; print(','.join(name for name in ('numpy','scipy','soundfile','librosa') if name in sys.modules))"],
            cwd=SCRIPT.parents[1], capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), '')

    def test_harmonic_pitch_is_measured_without_claiming_full_singer_range(self) -> None:
        from app.voice_coverage import analyze_coverage
        audio = tone(220) + .04 * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)
        original = audio.copy()
        report = analyze_coverage(audio, 16000)
        self.assertAlmostEqual(report.pitch_median_hz, 220, delta=4)
        self.assertGreater(report.reliable_voiced_sec, .8)
        self.assertLessEqual(report.reliable_voiced_sec, report.voiced_sec)
        self.assertLessEqual(report.voiced_sec, report.analyzed_sec)
        self.assertLessEqual(report.analyzed_sec, report.duration_sec)
        self.assertAlmostEqual(sum(item.seconds for item in report.pitch_bins), report.reliable_voiced_sec)
        self.assertIn('observed_pitch_is_not_full_vocal_range', report.warnings)
        self.assertIn('identity_unverified', report.warnings)
        report.model_dump_json()
        np.testing.assert_array_equal(audio, original)

    def test_silence_and_random_noise_do_not_get_invented_pitch_ranges(self) -> None:
        from app.voice_coverage import analyze_coverage
        for audio in [np.zeros(16000), np.random.default_rng(42).normal(0, .05, 16000)]:
            with self.subTest(silent=not audio.any()):
                report = analyze_coverage(audio, 16000)
                self.assertIsNone(report.pitch_median_hz)
                self.assertIsNone(report.pitch_p05_hz)
                self.assertIsNone(report.pitch_p95_hz)
                self.assertEqual(report.reliable_voiced_sec, 0)
                self.assertEqual(report.pitch_bins, [])
                self.assertIn('no_reliable_pitch', report.warnings)
                report.model_dump_json()

    def test_nonfinite_input_has_stable_error_instead_of_nonfinite_output(self) -> None:
        from app.voice_coverage import CoverageError, analyze_coverage
        audio = tone(220)
        audio[20] = np.nan
        with self.assertRaises(CoverageError) as failure:
            analyze_coverage(audio, 16000)
        self.assertEqual(failure.exception.code, 'invalid_audio')

    def test_antiphase_audio_is_preserved_during_pitch_analysis(self) -> None:
        from app.voice_coverage import analyze_coverage
        wave = tone(440)
        report = analyze_coverage(np.column_stack((wave, -wave)), 16000)
        self.assertAlmostEqual(report.pitch_median_hz, 440, delta=6)
        self.assertGreater(report.reliable_voiced_sec, .8)

    def test_short_silent_input_avoids_padding_into_fake_analysis(self) -> None:
        from app.voice_coverage import analyze_coverage
        report = analyze_coverage(np.zeros(100), 16000)
        self.assertEqual(report.analyzed_sec, 0)
        self.assertEqual(report.reliable_voiced_sec, 0)
        self.assertIsNone(report.pitch_median_hz)
        report.model_dump_json()

    def test_bandwidth_proxy_reflects_spectrum_and_documents_its_limit(self) -> None:
        from app.voice_coverage import analyze_coverage
        low = tone(220, sample_rate=44100)
        detailed = low + tone(6000, sample_rate=44100)
        narrow = analyze_coverage(low, 44100)
        broad = analyze_coverage(detailed, 44100)
        self.assertLess(narrow.high_band_energy_fraction, .001)
        self.assertGreater(broad.high_band_energy_fraction, .4)
        self.assertLess(narrow.spectral_rolloff95_hz, 500)
        self.assertGreater(broad.spectral_rolloff95_hz, 5500)
        self.assertIn('spectral_measurement_is_not_recording_quality', broad.warnings)
        limited = analyze_coverage(tone(220, sample_rate=8000), 8000)
        self.assertIsNone(limited.high_band_energy_fraction)
        self.assertIn('limited_analysis_bandwidth', limited.warnings)

    def test_summary_weights_pitch_seconds_and_spectral_proxies(self) -> None:
        from app.voice_coverage import summarize_coverage
        summary = summarize_coverage([measurement(57, 1, 2000, .1), measurement(81, 3, 6000, .5)], unavailable_segments=2)
        self.assertEqual(summary.duration_sec, 4)
        self.assertEqual(summary.reliable_voiced_sec, 4)
        self.assertEqual([(item.midi_note, item.seconds) for item in summary.pitch_bins], [(57, 1), (81, 3)])
        self.assertAlmostEqual(summary.pitch_p05_hz, 220)
        self.assertAlmostEqual(summary.pitch_median_hz, 880)
        self.assertAlmostEqual(summary.pitch_p95_hz, 880)
        self.assertAlmostEqual(summary.spectral_rolloff95_hz, 5000)
        self.assertAlmostEqual(summary.high_band_energy_fraction, .4)
        self.assertEqual(summary.measurements, 2)
        self.assertEqual(summary.unavailable_segments, 2)
        self.assertIn('coverage_unavailable', summary.warnings)
        summary.model_dump_json()

    def test_empty_summary_has_no_invented_measurements(self) -> None:
        from app.voice_coverage import summarize_coverage
        summary = summarize_coverage([], unavailable_segments=1)
        self.assertIsNone(summary.pitch_median_hz)
        self.assertIsNone(summary.spectral_rolloff95_hz)
        self.assertEqual(summary.measurements, 0)
        self.assertEqual(summary.duration_sec, 0)
        self.assertIn('coverage_unavailable', summary.warnings)

    def test_batch_cli_measures_each_file_and_reports_partial_failure(self) -> None:
        from app.voice_coverage import CoverageBatchReport
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / 'low.wav', root / 'high.wav']
            for file, frequency in zip(files, [220, 880]):
                sf.write(file, tone(frequency), 16000, subtype='FLOAT')
            before = [file.read_bytes() for file in files]
            manifest = root / 'batch.json'
            manifest.write_text(json.dumps({'inputs': [{'id': 'low', 'path': str(files[0])},
                {'id': 'high', 'path': str(files[1])}, {'id': 'missing', 'path': str(root / 'private-name.wav')}]}))
            environment = {**os.environ, 'NUMBA_CACHE_DIR': str(root / 'cache'), 'LIBROSA_CACHE_DIR': str(root / 'cache')}
            completed = subprocess.run([sys.executable, '-m', 'app.voice_coverage', '--batch', str(manifest)],
                cwd=SCRIPT.parents[1], capture_output=True, text=True, env=environment)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = CoverageBatchReport.model_validate_json(completed.stdout)
            self.assertEqual(report.failed_ids, ['missing'])
            self.assertEqual(set(report.measurements), {'low', 'high'})
            self.assertAlmostEqual(report.measurements['low'].pitch_p95_hz, 220, delta=4)
            self.assertAlmostEqual(report.measurements['high'].pitch_p05_hz, 880, delta=12)
            self.assertNotIn('private-name', completed.stderr)
            self.assertEqual([file.read_bytes() for file in files], before)

    def test_batch_cli_rejects_duplicate_ids_with_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'batch.json'
            manifest.write_text(json.dumps({'inputs': [{'id': 'same', 'path': '/unused'}, {'id': 'same', 'path': '/unused'}]}))
            completed = subprocess.run([sys.executable, '-m', 'app.voice_coverage', '--batch', str(manifest)],
                cwd=SCRIPT.parents[1], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, '')
            self.assertEqual(completed.stderr.strip(), '{"error_code": "invalid_coverage_manifest"}')

    def test_batch_manifest_is_bounded_to_selected_segment_limit(self) -> None:
        from app.voice_coverage import CoverageBatchInput, CoverageInput
        item = CoverageInput(id='one', path='/unused')
        values = [item.model_copy(update={'id': str(index)}) for index in range(1001)]
        with self.assertRaises(ValidationError):
            CoverageBatchInput(inputs=values)

    def test_oversized_passage_is_rejected_before_pitch_estimation(self) -> None:
        from app.voice_coverage import CoverageError, analyze_file
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'long.wav'
            sf.write(source, np.zeros(31 * 8000), 8000)
            with self.assertRaises(CoverageError) as failure:
                analyze_file(source)
            self.assertEqual(failure.exception.code, 'invalid_audio')

    def test_single_file_cli_prints_one_valid_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / 'silent.wav'
            sf.write(audio, np.zeros(100), 16000)
            completed = subprocess.run([sys.executable, '-m', 'app.voice_coverage', str(audio)],
                cwd=SCRIPT.parents[1], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = VoiceCoverageMeasurement.model_validate_json(completed.stdout)
            self.assertEqual(result.duration_sec, 100 / 16000)
            self.assertIsNone(result.pitch_median_hz)


if __name__ == '__main__':
    unittest.main()
