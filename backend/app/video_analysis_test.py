"""Measured song markers from isolated synthetic audio, without model loading."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import soundfile as sf


class AnalysisTests(unittest.TestCase):
    def test_planner_energy_uses_sample_timestamps_for_dense_and_decimated_analysis(self) -> None:
        from app.video_analysis import planner_energy
        from app.video_contracts import VideoSongAnalysis, VideoEnergyPoint
        for step in (.05, 2.5):
            analysis = VideoSongAnalysis(duration_sec=10, sample_rate=8000, energy=[
                VideoEnergyPoint(time_sec=index * step, value=.1 if index * step < 5 else .9)
                for index in range(round(10 / step))])
            levels = planner_energy(analysis)
            self.assertEqual(len(levels), 10)
            self.assertAlmostEqual(levels[1], .1)
            self.assertAlmostEqual(levels[8], .9)

    def test_missing_numpy_reports_dependency_error_instead_of_invalid_source(self) -> None:
        from app.video_analysis import AnalysisError, analyze_song
        with patch.dict(sys.modules, {'numpy': None}):
            with self.assertRaises(AnalysisError) as failure:
                analyze_song(Path('unused.wav'))
        self.assertEqual(failure.exception.code, 'analysis_unavailable')

    def test_cpu_analyzer_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('app.video_analysis'), 'Bounded measured song analysis is required')

    def test_click_track_yields_finite_120bpm_markers_without_lyric_alignment_claims(self) -> None:
        from app.video_analysis import analyze_samples
        sr = 8000
        samples = np.zeros(sr * 8)
        for start in range(0, samples.size, sr // 2):
            samples[start:start + 80] = np.hanning(80) * .8
        original = samples.copy()
        result = analyze_samples(samples, sr)
        self.assertAlmostEqual(result.tempo_bpm, 120, delta=2)
        self.assertGreater(len([marker for marker in result.markers if marker.kind == 'beat']), 10)
        self.assertIn('lyrics_are_not_time_aligned', result.warnings)
        self.assertIn('sections_are_energy_heuristics', result.warnings)
        self.assertTrue(all(0 <= marker.time_sec <= result.duration_sec for marker in result.markers))
        result.model_dump_json()
        np.testing.assert_array_equal(samples, original)

    def test_silence_has_no_invented_tempo_or_sections(self) -> None:
        from app.video_analysis import analyze_samples
        result = analyze_samples(np.zeros(8000 * 4), 8000)
        self.assertIsNone(result.tempo_bpm)
        self.assertEqual(result.markers, [])
        self.assertTrue(all(peak == 0 for peak in result.waveform_peaks))

    def test_bad_audio_is_rejected_with_stable_code(self) -> None:
        from app.video_analysis import AnalysisError, analyze_samples
        for samples in (np.array([np.nan]), np.array([np.inf]), np.array([])):
            with self.assertRaises(AnalysisError) as failure:
                analyze_samples(samples, 8000)
            self.assertEqual(failure.exception.code, 'invalid_audio')

    def test_antiphase_stereo_does_not_disappear_from_energy_and_onsets(self) -> None:
        from app.video_analysis import analyze_samples
        mono = np.zeros(8000 * 4)
        for start in range(8000, mono.size, 4000):
            mono[start:start + 80] = np.hanning(80) * .8
        result = analyze_samples(np.column_stack((mono, -mono)), 8000)
        self.assertGreater(max(result.waveform_peaks), .9)
        self.assertGreater(len([marker for marker in result.markers if marker.kind == 'onset']), 3)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for duration-bound regression')
    def test_streaming_duration_limit_rejects_without_source_changes(self) -> None:
        from app.video_analysis import AnalysisError, analyze_song
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'bounded.wav'
            sf.write(path, np.zeros(8000 * 2), 8000)
            original = path.read_bytes()
            with patch('app.video_analysis.MAX_SECONDS', 1), self.assertRaises(AnalysisError) as failure:
                analyze_song(path)
            self.assertEqual(failure.exception.code, 'audio_too_long')
            self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for actual CPU CLI fixture')
    def test_cli_decodes_temporary_waveform_and_emits_validated_contract(self) -> None:
        from app.video_contracts import VideoSongAnalysis
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'isolated.wav'
            samples = np.zeros(8000 * 4)
            samples[8000:8040] = .5
            sf.write(path, samples, 8000)
            result = subprocess.run([sys.executable, '-m', 'app.video_analysis', '--input', str(path)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30, check=True)
            report = VideoSongAnalysis.model_validate(json.loads(result.stdout))
            self.assertAlmostEqual(report.duration_sec, 4, delta=.01)
            self.assertGreater(max(report.waveform_peaks), 0)
