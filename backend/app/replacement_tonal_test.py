from __future__ import annotations

import hashlib
import asyncio
from pathlib import Path
import tempfile
import unittest

import numpy as np
from numpy.typing import NDArray
import soundfile as sf

from app.music_dsp import ResolvedDspSource, render_transform
from app.music_dsp_contracts import DspTransformRequest, WarpAnchor
from app.music_analysis_contracts import ProductionMusicSource
from app.replacement_pitch_contracts import DiagnosticArtifactIdentity
from app.replacement_tonal import analyze_owned_tonal, analyze_tonal, TonalError
from app.replacement_tonal_contracts import ReplacementTonalWorkerRequest
from scripts.music_analysis_worker import run_command


class TonalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.rate = 22050
        self.count = self.rate * 4
        self.rng = np.random.default_rng(17)

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    def audio(self, identifier: str, samples: NDArray[np.float32], *, rate: int | None = None) -> DiagnosticArtifactIdentity:
        sample_rate = self.rate if rate is None else rate
        path = self.artifacts / (identifier * 32 + '.wav')
        sf.write(path, samples, sample_rate, subtype='FLOAT')
        return DiagnosticArtifactIdentity(artifact_id=identifier * 32,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sample_rate_hz=sample_rate,
            channels=1 if samples.ndim == 1 or samples.shape[1] == 1 else 2, duration_frames=len(samples))

    def noise(self) -> NDArray[np.float32]:
        return np.asarray(self.rng.normal(0, .08, self.count), dtype=np.float32)

    def request(self, reference: DiagnosticArtifactIdentity, backing: DiagnosticArtifactIdentity,
                new: DiagnosticArtifactIdentity | None = None, *, aligned: bool = True,
                matched: DiagnosticArtifactIdentity | None = None) -> ReplacementTonalWorkerRequest:
        return ReplacementTonalWorkerRequest(plan_id='a' * 32, revision=3, artifact_root=str(self.artifacts),
            source_vocal=reference, backing=backing, new_vocal=new, new_vocal_aligned=aligned,
            matched_vocal=matched)

    async def test_gain_only_difference_is_not_treated_as_tonal_correction(self) -> None:
        samples = self.noise()
        reference = self.audio('1', samples)
        new = self.audio('2', samples * 3)
        backing = self.audio('3', self.noise())
        checksums = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.artifacts.iterdir()}
        report = await analyze_tonal(self.request(reference, backing, new), self.work, run_command)
        self.assertEqual(report.matching_eq.status, 'available')
        self.assertEqual(report.matching_eq.suggested_eq_bands, [])
        self.assertLess(report.matching_eq.before_difference_db, .001)
        self.assertIsNone(report.matching_eq.after_difference_db)
        self.assertEqual(report.bleed[1].correlation_strength, 'weak')
        self.assertIn('low_correlation_not_absence_of_bleed', report.warnings)
        for path, checksum in checksums.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)

    async def test_real_suggested_eq_is_bounded_and_post_metric_measures_actual_processed_bytes(self) -> None:
        samples = self.noise()
        spectrum = np.fft.rfft(samples)
        frequencies = np.fft.rfftfreq(len(samples), 1 / self.rate)
        shape = np.clip((np.log2(np.maximum(frequencies, 125) / 500)) * 3, -6, 9)
        tilted = np.asarray(np.fft.irfft(spectrum * 10 ** (shape / 20), n=len(samples)), dtype=np.float32)
        reference, new = self.audio('1', samples), self.audio('2', tilted)
        backing = self.audio('3', self.noise())
        report = await analyze_tonal(self.request(reference, backing, new), self.work, run_command)
        eq = report.matching_eq.suggested_eq_bands
        self.assertTrue(eq)
        self.assertLessEqual(sum(max(0.0, band.gain_db) for band in eq), 6)
        self.assertTrue(all(abs(band.gain_db) <= 6 for band in eq))
        path = self.artifacts / ('2' * 32 + '.wav')
        source_ref = ProductionMusicSource(asset_id='2' * 32, sha256=new.sha256)
        source = ResolvedDspSource(path, source_ref, new.sha256, self.rate, 1, self.count)
        transformed = await render_transform(DspTransformRequest(source=source_ref, source_sha256=new.sha256,
            revision=3, anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0),
                WarpAnchor(source_sec=4.0, target_sec=4.0)], output_duration_sec=4.0,
            sample_rate_hz=48000, eq_bands=eq), source, self.work, run_command)
        actual, rate = sf.read(self.work / transformed.artifact_name, dtype='float32', always_2d=True)
        matched = self.audio('4', actual, rate=rate)
        post = await analyze_tonal(self.request(reference, backing, new, matched=matched), self.work, run_command)
        self.assertLess(post.matching_eq.after_difference_db, post.matching_eq.before_difference_db)
        self.assertEqual(post.artifacts[-1].identity.sha256, matched.sha256)

    async def test_delayed_inverted_stereo_signal_keeps_lag_and_polarity_without_phase_cancellation(self) -> None:
        accompaniment = self.noise()
        delay = 441
        reference = np.concatenate((np.zeros(delay, dtype=np.float32), -accompaniment[:-delay]))
        vocal = self.audio('1', np.column_stack((reference, -reference)))
        backing = self.audio('3', np.column_stack((accompaniment, -accompaniment)))
        report = await analyze_tonal(self.request(vocal, backing), self.work, run_command)
        measured = report.bleed[0]
        self.assertEqual(measured.correlation_strength, 'high')
        self.assertGreater(measured.median_absolute_correlation, .99)
        observation = measured.windows[0]
        self.assertAlmostEqual(observation.lag_ms, 20.0, delta=.1)
        self.assertEqual(observation.polarity, 'inverted')
        self.assertFalse(observation.lag_ambiguous)

    async def test_silence_and_unmapped_prepared_performance_remain_inconclusive(self) -> None:
        silent = self.audio('1', np.zeros(self.count, dtype=np.float32))
        backing = self.audio('3', self.noise())
        new = self.audio('2', self.noise())
        report = await analyze_tonal(self.request(silent, backing, new, aligned=False), self.work, run_command)
        self.assertEqual(report.matching_eq.status, 'inconclusive')
        self.assertEqual(report.bleed[0].status, 'inconclusive')
        self.assertEqual(report.bleed[1].reason, 'timeline_unmapped')
        self.assertEqual(report.matching_eq.suggested_eq_bands, [])

    async def test_unmapped_spectra_can_be_compared_but_missing_bandwidth_is_disabled(self) -> None:
        samples = np.asarray(self.rng.normal(0, .08, 8000 * 4), dtype=np.float32)
        reference, new = self.audio('1', samples, rate=8000), self.audio('2', samples * 2, rate=8000)
        backing = self.audio('3', samples, rate=8000)
        report = await analyze_tonal(self.request(reference, backing, new, aligned=False), self.work, run_command)
        self.assertEqual(report.matching_eq.status, 'available')
        self.assertEqual(report.matching_eq.bands[-1].reason, 'bandwidth_unavailable')
        self.assertEqual(report.bleed[1].status, 'inconclusive')

    async def test_changed_hash_header_and_symlink_are_rejected(self) -> None:
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        request = self.request(reference, backing)
        path = self.artifacts / ('1' * 32 + '.wav')
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(TonalError) as changed:
            await analyze_tonal(request, self.work, run_command)
        self.assertEqual(changed.exception.code, 'replacement_tonal_source_changed')
        path.unlink()
        path.symlink_to(self.artifacts / ('3' * 32 + '.wav'))
        with self.assertRaises(TonalError):
            await analyze_tonal(request, self.work, run_command)

    async def test_owned_worker_returns_typed_report_and_integrity_error(self) -> None:
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        request = self.request(reference, backing)
        result = await analyze_owned_tonal(request, self.work, run_command)
        self.assertEqual(result.plan_id, request.plan_id)
        self.assertEqual(result.revision, request.revision)
        self.assertEqual(result.artifacts[0].identity, reference)
        self.assertNotIn(str(self.root), result.model_dump_json())
        path = self.artifacts / ('1' * 32 + '.wav')
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(TonalError) as changed:
            await analyze_owned_tonal(request, self.work, run_command)
        self.assertEqual(changed.exception.code, 'replacement_tonal_source_changed')

    async def test_worker_response_must_match_current_revision_and_all_input_hashes(self) -> None:
        from app.replacement_tonal_contracts import ReplacementTonalWorkerOutcome
        from unittest.mock import AsyncMock
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        request = self.request(reference, backing)
        report = await analyze_tonal(request, self.work, run_command)
        foreign = type(report).model_validate({**report.model_dump(), 'revision': request.revision + 1})
        async def forged(argv: list[str], timeout: float) -> bytes:
            Path(argv[-1]).write_text(ReplacementTonalWorkerOutcome(result=foreign,
                input_sha256=hashlib.sha256(Path(argv[-2]).read_bytes()).hexdigest()).model_dump_json())
            return b''
        with self.assertRaises(TonalError) as changed:
            await analyze_owned_tonal(request, self.work, forged)
        self.assertEqual(changed.exception.code, 'replacement_tonal_source_changed')
        broken = AsyncMock(side_effect=OSError('private internal detail'))
        with self.assertRaises(TonalError) as unavailable:
            await analyze_owned_tonal(request, self.work, broken)
        self.assertEqual(str(unavailable.exception), 'replacement_tonal_unavailable')

    async def test_worker_result_is_bound_to_the_exact_private_input_manifest(self) -> None:
        from app.replacement_tonal_contracts import ReplacementTonalWorkerOutcome
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        request = self.request(reference, backing)
        report = await analyze_tonal(request, self.work, run_command)
        async def wrong_input(argv: list[str], timeout: float) -> bytes:
            outcome = ReplacementTonalWorkerOutcome(result=report, input_sha256='f' * 64)
            Path(argv[-1]).write_text(outcome.model_dump_json())
            return b''
        with self.assertRaises(TonalError) as changed:
            await analyze_owned_tonal(request, self.work, wrong_input)
        self.assertEqual(changed.exception.code, 'replacement_tonal_source_changed')

    async def test_long_source_uses_at_most_sixty_seconds_in_disjoint_recorded_windows(self) -> None:
        samples = np.asarray(self.rng.normal(0, .04, self.rate * 123), dtype=np.float32)
        reference, backing = self.audio('1', samples), self.audio('3', samples)
        report = await analyze_tonal(self.request(reference, backing), self.work, run_command)
        for basis in report.artifacts:
            self.assertEqual(len(basis.windows), 12)
            self.assertLessEqual(sum(window.end_frame - window.start_frame for window in basis.windows), self.rate * 60)
            self.assertEqual(basis.windows[0].start_frame, 0)
            self.assertEqual(basis.windows[-1].end_frame, len(samples))
            self.assertTrue(all(left.end_frame <= right.start_frame for left, right in zip(basis.windows, basis.windows[1:])))

    async def test_both_lag_boundaries_retain_signed_polarity_and_periodic_lag_is_ambiguous(self) -> None:
        from app.replacement_tonal import _lagged, MAX_LAG
        samples = np.asarray(self.rng.normal(0, .04, self.count), dtype=np.float64)
        mask = np.ones(self.count, dtype=np.bool_)
        for shift in (-MAX_LAG, MAX_LAG):
            with self.subTest(shift=shift):
                shifted = np.roll(samples, shift) * -1
                result = _lagged(shifted, samples, mask)
                self.assertIsNotNone(result)
                assert result is not None
                correlation, lag, _, ambiguous = result
                self.assertLess(correlation, -.999)
                self.assertEqual(lag, shift)
                self.assertFalse(ambiguous)
        tone = np.sin(np.arange(self.count) / self.rate * 2 * np.pi * 220)
        periodic = _lagged(tone, tone, mask)
        self.assertIsNotNone(periodic)
        assert periodic is not None
        self.assertTrue(periodic[-1])

    async def test_cancelled_owned_callback_propagates_without_discarding_workspace(self) -> None:
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        async def cancelled(argv: list[str], timeout: float) -> bytes:
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await analyze_owned_tonal(self.request(reference, backing), self.work, cancelled)
        self.assertEqual(len(list(self.work.glob('tonal-*/input.json'))), 1)
        self.assertEqual(list(self.work.glob('tonal-*/output.json')), [])

    async def test_nonfinite_sampled_audio_and_malformed_worker_output_fail_closed(self) -> None:
        samples = self.noise()
        samples[self.rate] = np.nan
        reference, backing = self.audio('1', samples), self.audio('3', self.noise())
        with self.assertRaises(TonalError) as invalid:
            await analyze_tonal(self.request(reference, backing), self.work, run_command)
        self.assertEqual(invalid.exception.code, 'replacement_tonal_invalid_audio')
        async def malformed(argv: list[str], timeout: float) -> bytes:
            Path(argv[-1]).write_text('{"result":"not trusted"}')
            return b''
        with self.assertRaises(TonalError) as output:
            await analyze_owned_tonal(self.request(reference, backing), self.work, malformed)
        self.assertEqual(output.exception.code, 'replacement_tonal_unavailable')

    async def test_retained_audio_changed_during_measurement_is_not_published_as_current(self) -> None:
        reference, backing = self.audio('1', self.noise()), self.audio('3', self.noise())
        path = self.artifacts / ('1' * 32 + '.wav')
        changed = False
        async def modify_after_decode(argv: list[str], timeout: float) -> bytes:
            nonlocal changed
            output = await run_command(argv, timeout)
            if not changed and '-i' in argv and str(path) in argv:
                sf.write(path, self.noise(), self.rate, subtype='FLOAT')
                changed = True
            return output
        with self.assertRaises(TonalError) as error:
            await analyze_tonal(self.request(reference, backing), self.work, modify_after_decode)
        self.assertTrue(changed)
        self.assertEqual(error.exception.code, 'replacement_tonal_source_changed')


if __name__ == '__main__':
    unittest.main()
