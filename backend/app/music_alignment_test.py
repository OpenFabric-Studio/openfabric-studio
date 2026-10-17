from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
import hashlib
from pathlib import Path
import tempfile
import wave

import numpy as np

from app import music_alignment
from app.music_analysis_contracts import VersionMusicSource
from app.music_dsp import MusicDspError
from app.music_dsp_contracts import AlignmentRequest


class MusicAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rate = 8000
        self.request = AlignmentRequest(reference=VersionMusicSource(track_id=1, version_id='a' * 32),
            reference_sha256='b' * 64, prepared=VersionMusicSource(track_id=2, version_id='c' * 32),
            prepared_sha256='d' * 64, revision=0)

    def fixture(self, frequencies: list[int], *, leading: float = .2, scale: float = 1.0) -> np.ndarray:
        pieces = [np.zeros((round(leading * self.rate), 1), dtype=np.float32)]
        for frequency in frequencies:
            time = np.arange(round(.5 * scale * self.rate)) / self.rate
            tone = np.asarray(.2 * np.sin(2 * np.pi * frequency * time), dtype=np.float32)
            pieces.extend([tone[:, None], np.zeros((round(.3 * scale * self.rate), 1), dtype=np.float32)])
        return np.concatenate(pieces)

    def align(self, reference: np.ndarray, prepared: np.ndarray):
        return music_alignment.align_features(self.request,
            music_alignment.extract_features(reference, self.rate), music_alignment.extract_features(prepared, self.rate))

    def test_identity_is_monotone_deterministic_and_requires_review(self) -> None:
        samples = self.fixture([440, 660, 880])
        first = self.align(samples, samples)
        second = self.align(samples, samples)
        self.assertEqual(first.status, 'needs_review')
        self.assertGreaterEqual(len(first.anchors), 2)
        self.assertEqual(first.anchors, second.anchors)
        self.assertEqual([phrase.phrase_id for phrase in first.phrases], [phrase.phrase_id for phrase in second.phrases])
        self.assertTrue(all(abs(anchor.source_sec - anchor.target_sec) < .021 for anchor in first.anchors))
        self.assertTrue(all(phrase.confidence is None and phrase.onset_error_sec is None for phrase in first.phrases))

    def test_slow_prepared_take_with_leading_silence_keeps_its_actual_offset(self) -> None:
        result = self.align(self.fixture([440, 660, 880]), self.fixture([440, 660, 880], leading=1.2, scale=1.1))
        self.assertGreaterEqual(len(result.anchors), 2, result.model_dump_json())
        self.assertAlmostEqual(result.anchors[0].source_sec, 1.2, delta=.021)
        self.assertAlmostEqual(result.anchors[0].target_sec, .2, delta=.021)
        self.assertLess(result.anchors[-1].target_sec, result.anchors[-1].source_sec)
        self.assertEqual(result.status, 'needs_review')

    def test_missing_and_swapped_phrases_remain_explicit_and_are_never_reordered(self) -> None:
        for prepared in ([440, 880], [660, 440, 880]):
            with self.subTest(prepared=prepared):
                result = self.align(self.fixture([440, 660, 880]), self.fixture(prepared))
                self.assertIn('unmatched_phrases', result.warnings)
                self.assertTrue(any(phrase.reason.startswith('unmatched_') for phrase in result.phrases))
                self.assertNotEqual(result.status, 'accepted')

    def test_silence_and_short_activity_do_not_get_an_identity_warp(self) -> None:
        for samples in (np.zeros((8000, 2), dtype=np.float32), np.ones((800, 1), dtype=np.float32) * .1):
            result = self.align(samples, samples)
            self.assertEqual(result.status, 'unavailable')
            self.assertEqual(result.anchors, [])

    def test_repeated_or_held_phrases_are_ambiguous(self) -> None:
        for samples in (self.fixture([440, 440, 440]), self.fixture([440], scale=40.0)):
            result = self.align(samples, samples)
            self.assertIn('phrase_ambiguous', result.warnings)
            self.assertEqual(result.status, 'needs_review')

    def test_dtw_refuses_matrix_budget_before_allocation(self) -> None:
        values = np.zeros((2001, 27), dtype=np.float32)
        with patch.object(music_alignment.np, 'empty', side_effect=AssertionError('oversized allocation')):
            with self.assertRaises(MusicDspError) as error:
                music_alignment.bounded_dtw(values, values)
        self.assertEqual(error.exception.code, 'alignment_budget_exceeded')

    def test_combined_phrase_budget_refuses_before_matching_matrix_allocation(self) -> None:
        features = music_alignment.extract_features(self.fixture([440]), self.rate)
        phrases = [music_alignment._Phrase(10, 20)] * 513
        with patch.object(music_alignment.np, 'full', side_effect=AssertionError('oversized phrase matrix')):
            with self.assertRaises(MusicDspError) as error:
                music_alignment._match(features, features, phrases, phrases)
        self.assertEqual(error.exception.code, 'alignment_budget_exceeded')

    def test_anchor_budget_keeps_inspectable_phrases_instead_of_invalid_result(self) -> None:
        samples = self.fixture([440, 660, 880])
        with patch.object(music_alignment, 'MAX_ANCHORS', 2, create=True):
            result = self.align(samples, samples)
        self.assertLessEqual(len(result.anchors), 2)
        self.assertIn('alignment_budget_exceeded', result.warnings)
        self.assertTrue(any(phrase.reason == 'aligned' for phrase in result.phrases))
        self.assertTrue(any(phrase.reason == 'budget_exceeded' for phrase in result.phrases))

    def test_antiphase_stereo_preserves_activity_and_invalid_pcm_is_rejected(self) -> None:
        mono = self.fixture([440, 660])
        stereo = np.column_stack((mono[:, 0], -mono[:, 0]))
        result = self.align(stereo, stereo)
        self.assertGreaterEqual(len(result.anchors), 2)
        with self.assertRaises(MusicDspError):
            music_alignment.extract_features(np.full((1000, 1), np.nan, dtype=np.float32), self.rate)

    def test_manual_anchors_are_used_instead_of_automatic_phrase_guessing(self) -> None:
        from app.music_dsp_contracts import WarpAnchor
        request = AlignmentRequest.model_validate({**self.request.model_dump(), 'manual_anchors': [
            {'source_sec': 1.2, 'target_sec': .2}, {'source_sec': 1.7, 'target_sec': .7}]})
        result = music_alignment.align_features(request,
            music_alignment.extract_features(self.fixture([440]), self.rate),
            music_alignment.extract_features(self.fixture([660], leading=1.2), self.rate))
        self.assertEqual(result.anchors, [WarpAnchor(source_sec=1.2, target_sec=.2), WarpAnchor(source_sec=1.7, target_sec=.7)])
        self.assertEqual(result.phrases[0].reason, 'manual')
        self.assertEqual(result.status, 'needs_review')
        collapsed = AlignmentRequest.model_validate({**self.request.model_dump(), 'manual_anchors': [
            {'source_sec': 0.0, 'target_sec': 0.0}, {'source_sec': 1e-10, 'target_sec': 1e-10}]})
        with self.assertRaises(MusicDspError):
            music_alignment.align_features(collapsed,
                music_alignment.extract_features(self.fixture([440]), self.rate),
                music_alignment.extract_features(self.fixture([440]), self.rate))

    def test_only_an_oversized_phrase_is_refused_while_other_phrases_remain_inspectable(self) -> None:
        samples = np.concatenate((self.fixture([440]), self.fixture([660], leading=.3, scale=100.0)))
        result = self.align(samples, samples)
        self.assertEqual(result.status, 'needs_review')
        self.assertIn('alignment_budget_exceeded', result.warnings)
        self.assertTrue(any(phrase.reason == 'budget_exceeded' for phrase in result.phrases))
        self.assertTrue(any(phrase.reason == 'aligned' for phrase in result.phrases))
        self.assertGreaterEqual(len(result.anchors), 2)


class MusicAlignmentStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_deadline_is_typed_and_preserves_source(self) -> None:
        from app.music_dsp import ResolvedDspSource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'source.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(bytes(16000))
            original = path.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            source = VersionMusicSource(track_id=1, version_id='a' * 32)
            resolved = ResolvedDspSource(path, source, digest, 8000, 1, 8000)
            request = AlignmentRequest(reference=source, reference_sha256=digest,
                prepared=source, prepared_sha256=digest, revision=0)
            with self.assertRaises(MusicDspError) as error:
                await music_alignment.align_prepared_vocal(request, resolved, resolved, root,
                    AsyncMock(side_effect=TimeoutError))
            self.assertEqual(error.exception.code, 'dsp_timeout')
            self.assertEqual(path.read_bytes(), original)

    async def test_whole_song_decode_is_windowed_and_crossing_phrase_stays_uncertain(self) -> None:
        from app.music_dsp import ResolvedDspSource
        from scripts import music_analysis_worker
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source.wav'
            rate, duration = 8000, 132
            with wave.open(str(source), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                for second in range(duration):
                    time = np.arange(rate) / rate
                    active = 1 <= second < 3 or 105 <= second < 125
                    frequency = 440 if second < 100 else 660
                    block = np.asarray(.2 * np.sin(2 * np.pi * frequency * time) * 32767, dtype='<i2') if active else np.zeros(rate, dtype='<i2')
                    output.writeframes(block.tobytes())
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            first = VersionMusicSource(track_id=1, version_id='a' * 32)
            second = VersionMusicSource(track_id=2, version_id='b' * 32)
            reference = ResolvedDspSource(source, first, digest, rate, 1, rate * duration)
            prepared = ResolvedDspSource(source, second, digest, rate, 1, rate * duration)
            request = AlignmentRequest(reference=first, reference_sha256=digest,
                prepared=second, prepared_sha256=digest, revision=0)
            decode = music_analysis_worker.decode_window
            spans: list[tuple[float, float, int]] = []

            async def recording(path, staging, start, seconds, channels, run):
                samples = await decode(path, staging, start, seconds, channels, run)
                spans.append((start, seconds, len(samples)))
                return samples

            with patch.object(music_alignment, '_metrics', new=AsyncMock()), \
                    patch.object(music_analysis_worker, 'decode_window', side_effect=recording):
                result = await music_alignment.align_prepared_vocal(request, reference, prepared, root,
                    music_analysis_worker.run_command)
            self.assertEqual([(start, seconds) for start, seconds, _ in spans],
                [(0.0, 120.0), (110.0, 22.0), (0.0, 120.0), (110.0, 22.0)])
            self.assertLessEqual(max(frames for _, _, frames in spans), 2646000)
            self.assertEqual(result.status, 'needs_review')
            self.assertIn('anchor_boundary_uncertain', result.warnings)
            self.assertTrue(any(phrase.reason == 'ambiguous' for phrase in result.phrases))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            features = list(root.rglob('*.features.f32'))
            self.assertEqual(len(features), 2)
            self.assertTrue(all(path.stat().st_size == 6600 * 27 * 4 for path in features))
