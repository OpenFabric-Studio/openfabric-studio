from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app import music_dsp
from app.music_analysis_contracts import VersionMusicSource
from app.music_dsp_contracts import DspMixInput, DspMixRequest, DspTransformRequest, DuckSettings, WarpAnchor
from scripts.music_analysis_worker import run_command


class MusicDspTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.staging = self.root / 'staging'
        self.staging.mkdir()
        self.rate = 48000
        phase = np.arange(self.rate, dtype=np.float64) / self.rate
        tone = np.asarray(.2 * np.sin(2 * np.pi * 440 * phase), dtype=np.float32)
        self.samples = np.column_stack((tone, -tone))
        self.source = self.audio(self.samples, 1)

    def audio(self, samples: np.ndarray, track_id: int) -> music_dsp.ResolvedDspSource:
        path = self.root / f'{track_id}.wav'
        sf.write(path, samples, self.rate, subtype='FLOAT')
        return music_dsp.ResolvedDspSource(path=path, source=VersionMusicSource(track_id=track_id,
            version_id=f'{track_id:032x}'), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            sample_rate_hz=self.rate, channels=samples.shape[1], duration_frames=len(samples))

    def request(self, **changes: object) -> DspTransformRequest:
        return DspTransformRequest.model_validate({'source': self.source.source.model_dump(),
            'source_sha256': self.source.sha256, 'revision': 0, 'anchors': [
                {'source_sec': 0.0, 'target_sec': 0.0}, {'source_sec': 1.0, 'target_sec': 1.0}],
            'output_duration_sec': 1.0, **changes})

    async def test_identity_bypasses_stretch_and_preserves_exact_stereo_pcm(self) -> None:
        native = AsyncMock(side_effect=AssertionError('identity entered stretch engine'))
        original = self.source.path.read_bytes()
        rendered = await music_dsp.render_transform(self.request(), self.source, self.staging, run_command, native=native)
        output, rate = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        self.assertEqual((rate, rendered.duration_frames, rendered.channels), (self.rate, self.rate, 2))
        np.testing.assert_array_equal(output, self.samples)
        self.assertEqual(self.source.path.read_bytes(), original)
        native.assert_not_awaited()

    async def test_missing_native_and_collapsed_anchors_refuse_before_commands(self) -> None:
        run = AsyncMock(side_effect=AssertionError('unsupported render launched a command'))
        with self.assertRaises(music_dsp.MusicDspError) as unavailable:
            await music_dsp.render_transform(self.request(pitch_shift_semitones=1.0), self.source, self.staging, run)
        self.assertEqual(unavailable.exception.code, 'dsp_stretch_unavailable')
        request = self.request(anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0),
            WarpAnchor(source_sec=1e-10, target_sec=1e-10)], output_duration_sec=1e-10)
        with self.assertRaises(music_dsp.MusicDspError) as collapsed:
            await music_dsp.render_transform(request, self.source, self.staging, run)
        self.assertEqual(collapsed.exception.code, 'dsp_invalid_anchors')
        run.assert_not_awaited()

    async def test_gain_eq_fades_keep_exact_frames_and_channel_relation(self) -> None:
        request = self.request(gain_db=-3.0, eq_bands=[{'frequency_hz': 440.0, 'gain_db': -6.0}],
            fades={'fade_in_sec': .05, 'fade_out_sec': .05, 'curve': 'equal_power'})
        rendered = await music_dsp.render_transform(request, self.source, self.staging, run_command)
        output, _ = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        self.assertEqual(len(output), self.rate)
        np.testing.assert_allclose(output[:, 0], -output[:, 1], atol=1e-7)
        ratio = np.sqrt(np.mean(output[12000:36000, 0] ** 2) / np.mean(self.samples[12000:36000, 0] ** 2))
        self.assertAlmostEqual(20 * np.log10(ratio), -9.0, delta=.15)
        self.assertAlmostEqual(float(output[0, 0]), 0.0, places=7)
        self.assertAlmostEqual(float(output[-1, 0]), 0.0, places=7)

    async def test_highpass_is_real_two_pole_filter_and_preserves_stereo_frames(self) -> None:
        phase = np.arange(self.rate, dtype=np.float64) / self.rate
        tone = np.asarray(.1 * np.sin(2 * np.pi * 30 * phase) + .1 * np.sin(2 * np.pi * 1000 * phase), dtype=np.float32)
        source = self.audio(np.column_stack((tone, -tone)), 9)
        request = DspTransformRequest.model_validate({**self.request(highpass_hz=100).model_dump(),
            'source': source.source.model_dump(), 'source_sha256': source.sha256})
        rendered = await music_dsp.render_transform(request, source, self.staging, run_command)
        output, _ = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        self.assertEqual(output.shape, (48000, 2))
        np.testing.assert_allclose(output[:, 0], -output[:, 1], atol=1e-7)
        steady = output[12000:36000, 0]
        times = phase[12000:36000]
        low = abs(np.sum(steady * np.exp(-2j * np.pi * 30 * times)))
        high = abs(np.sum(steady * np.exp(-2j * np.pi * 1000 * times)))
        self.assertLess(low / high, .1)

    async def test_invalid_private_metadata_and_preserved_rate_eq_refuse_before_commands(self) -> None:
        run = AsyncMock(side_effect=AssertionError('invalid plan launched a command'))
        for source, request in ((replace(self.source, sample_rate_hz=0), self.request()),
                (replace(self.source, sample_rate_hz=8000, duration_frames=8000),
                    self.request(eq_bands=[{'frequency_hz': 4000.0, 'gain_db': -1.0}]))):
            with self.subTest(rate=source.sample_rate_hz), self.assertRaises(music_dsp.MusicDspError) as error:
                await music_dsp.render_transform(request, source, self.staging, run)
            self.assertEqual(error.exception.code, 'dsp_invalid_source')
        run.assert_not_awaited()

    async def test_mix_places_source_in_exact_frames_and_preserves_requested_silence(self) -> None:
        samples = np.zeros((4800, 1), dtype=np.float32)
        samples[10] = .2
        source = self.audio(samples, 2)
        item = DspMixInput(id='a' * 32, source=source.source, source_sha256=source.sha256,
            start_sec=.01, duration_sec=.1, pan=-1.0)
        rendered = await music_dsp.render_mix(DspMixRequest(revision=0, duration_sec=.2, inputs=[item]),
            {item.id: source}, self.staging, run_command)
        output, _ = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        self.assertEqual(output.shape, (9600, 2))
        self.assertEqual(int(np.argmax(np.abs(output[:, 0]))), 490)
        self.assertEqual(float(np.max(np.abs(output[:, 1]))), 0.0)
        self.assertEqual(float(np.max(np.abs(output[6000:]))), 0.0)

    async def test_ducking_is_linked_and_cannot_exceed_declared_reduction(self) -> None:
        sidechain = self.audio(self.samples * 3, 2)
        main = DspMixInput(id='a' * 32, source=self.source.source, source_sha256=self.source.sha256,
            duration_sec=1.0, duck=DuckSettings(sidechain_input_id='b' * 32, threshold_dbfs=-50.0,
                ratio=8.0, attack_ms=1.0, max_reduction_db=6.0))
        detector = DspMixInput(id='b' * 32, source=sidechain.source, source_sha256=sidechain.sha256,
            duration_sec=1.0, gain_db=-12.0412)
        # Pan sidechain hard right; the left channel measures only the ducked main.
        detector = DspMixInput.model_validate({**detector.model_dump(), 'pan': 1.0})
        rendered = await music_dsp.render_mix(DspMixRequest(revision=0, duration_sec=1.0, inputs=[main, detector]),
            {main.id: self.source, detector.id: sidechain}, self.staging, run_command)
        output, _ = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        active = np.abs(self.samples[12000:36000, 0]) > .1
        ratio = output[12000:36000, 0][active] / self.samples[12000:36000, 0][active]
        self.assertGreaterEqual(float(np.min(ratio)), 10 ** (-6 / 20) - 1e-6)
        self.assertLess(float(np.median(ratio)), .7)

    async def test_muted_detector_still_drives_duck_without_audible_contribution(self) -> None:
        main = DspMixInput(id='a' * 32, source=self.source.source, source_sha256=self.source.sha256,
            duration_sec=1.0, duck=DuckSettings(sidechain_input_id='b' * 32, threshold_dbfs=-50,
                ratio=8, attack_ms=1, max_reduction_db=6))
        sidechain = self.audio(self.samples * 3, 2)
        detector = DspMixInput.model_validate({'id': 'b' * 32, 'source': sidechain.source.model_dump(),
            'source_sha256': sidechain.sha256, 'duration_sec': 1.0, 'muted': True})
        rendered = await music_dsp.render_mix(DspMixRequest(revision=0, duration_sec=1,
            inputs=[main, detector]), {main.id: self.source, detector.id: sidechain}, self.staging, run_command)
        output, _ = sf.read(self.staging / rendered.artifact_name, dtype='float32', always_2d=True)
        active = np.abs(self.samples[12000:36000, 0]) > .1
        ratio = output[12000:36000, 0][active] / self.samples[12000:36000, 0][active]
        self.assertGreaterEqual(float(np.min(ratio)), 10 ** (-6 / 20) - 1e-6)
        self.assertLess(float(np.median(ratio)), .7)
        np.testing.assert_allclose(output[:, 0], -output[:, 1], atol=1e-7)

    async def test_source_digest_mismatch_never_reaches_command(self) -> None:
        request = self.request(source_sha256='f' * 64)
        run = AsyncMock()
        with self.assertRaises(music_dsp.MusicDspError) as error:
            await music_dsp.render_transform(request, self.source, self.staging, run)
        self.assertEqual(error.exception.code, 'dsp_source_changed')
        run.assert_not_awaited()

    async def test_long_uniform_native_span_reaches_processing_but_large_maps_are_refused(self) -> None:
        source = music_dsp.ResolvedDspSource(path=self.source.path, source=self.source.source,
            sha256=self.source.sha256, sample_rate_hz=self.rate, channels=2, duration_frames=601 * self.rate)
        native = AsyncMock()
        run = AsyncMock()
        uniform = self.request(anchors=[WarpAnchor(source_sec=0, target_sec=0),
            WarpAnchor(source_sec=31, target_sec=38.75)], output_duration_sec=38.75)
        with patch.object(music_dsp, '_metrics', side_effect=RuntimeError('span admitted')):
            with self.assertRaisesRegex(RuntimeError, 'span admitted'):
                await music_dsp.render_transform(uniform, source, self.staging, run, native=native)
        for anchors in ([WarpAnchor(source_sec=0, target_sec=0), WarpAnchor(source_sec=601, target_sec=601.1)],
                        [WarpAnchor(source_sec=0, target_sec=0), WarpAnchor(source_sec=15, target_sec=18),
                            WarpAnchor(source_sec=31, target_sec=34)]):
            request = self.request(anchors=anchors, output_duration_sec=anchors[-1].target_sec)
            with self.assertRaises(music_dsp.MusicDspError) as error:
                await music_dsp.render_transform(request, source, self.staging, run, native=native)
            self.assertEqual(error.exception.code, 'dsp_budget_exceeded')
        run.assert_not_awaited()

    async def test_valid_wav_header_with_wrong_frame_count_is_rejected(self) -> None:
        path = self.staging / 'short.wav'
        sf.write(path, self.samples[:100], self.rate, subtype='FLOAT')
        with self.assertRaises(music_dsp.MusicDspError) as error:
            music_dsp._validate_pcm(path, self.rate, 2, self.rate)
        self.assertEqual(error.exception.code, 'dsp_output_invalid')

    async def test_native_callback_gets_zero_based_frames_and_silence_cannot_hide_failed_processing(self) -> None:
        from app.music_dsp_contracts import DspEngineIdentity
        request = self.request(anchors=[{'source_sec': .2, 'target_sec': 1.0},
            {'source_sec': .8, 'target_sec': 1.75}], output_duration_sec=.75, pitch_shift_semitones=1.0)
        silent = False

        async def native(source_wav, output_wav, *, sample_rate_hz, channels, input_frames, output_frames,
                         anchors, pitch_shift_semitones, run):
            self.assertEqual((input_frames, output_frames, anchors), (28800, 36000, ((0, 0), (28800, 36000))))
            samples, _ = sf.read(source_wav, dtype='float32', always_2d=True)
            indices = np.minimum(input_frames - 1, np.floor(np.arange(output_frames) * input_frames / output_frames).astype(int))
            rendered = np.zeros((output_frames, channels), dtype=np.float32) if silent else samples[indices]
            sf.write(output_wav, rendered, sample_rate_hz, subtype='FLOAT')
            return DspEngineIdentity(name='mock-native', revision='test-v1', configuration_sha256='a' * 64)

        rendered = await music_dsp.render_transform(request, self.source, self.staging, run_command, native=native)
        self.assertEqual((rendered.target_start_frame, rendered.duration_frames), (48000, 36000))
        silent = True
        with self.assertRaises(music_dsp.MusicDspError) as error:
            await music_dsp.render_transform(request, self.source, self.staging, run_command, native=native)
        self.assertEqual(error.exception.code, 'dsp_output_invalid')

    async def test_timeout_is_typed_and_unverified_callback_keeps_owned_partials(self) -> None:
        before = self.source.path.read_bytes()
        with self.assertRaises(music_dsp.MusicDspError) as timeout:
            await music_dsp.render_transform(self.request(), self.source, self.staging,
                AsyncMock(side_effect=TimeoutError('private diagnostic')))
        self.assertEqual(timeout.exception.code, 'dsp_timeout')
        partial = self.staging / 'owned-partial'

        async def run(argv: list[str], timeout: float) -> bytes:
            partial.write_bytes(b'worker may still own this')
            raise music_dsp.MusicDspError('dsp_worker_unverified')

        with self.assertRaises(music_dsp.MusicDspError):
            await music_dsp.render_transform(self.request(), self.source, self.staging, run)
        self.assertTrue(partial.exists())
        self.assertEqual(self.source.path.read_bytes(), before)

    async def test_final_master_normalization_is_measured_and_source_is_unchanged(self) -> None:
        source = self.audio(np.tile(self.samples, (3, 1)) * .25, 3)
        original = source.path.read_bytes()
        request = DspTransformRequest(source=source.source, source_sha256=source.sha256, revision=0,
            anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0), WarpAnchor(source_sec=3.0, target_sec=3.0)],
            output_duration_sec=3.0, loudness={'profile': 'music'})
        rendered = await music_dsp.render_transform(request, source, self.staging, run_command)
        self.assertEqual(rendered.target_result, 'met')
        self.assertAlmostEqual(rendered.output_metrics.integrated_lufs, -14, delta=.5)
        self.assertLessEqual(rendered.output_metrics.true_peak_dbtp, -.9)
        self.assertEqual(rendered.duration_frames, 144000)
        self.assertEqual(source.path.read_bytes(), original)

    async def test_zero_duck_is_a_real_bypass_and_linked_compression_retains_stereo(self) -> None:
        silent = self.audio(np.zeros_like(self.samples), 2)
        first = DspMixInput(id='a' * 32, source=self.source.source, source_sha256=self.source.sha256,
            duration_sec=1.0, duck=DuckSettings(sidechain_input_id='b' * 32, max_reduction_db=0.0))
        second = DspMixInput(id='b' * 32, source=silent.source, source_sha256=silent.sha256, duration_sec=1.0)
        mixed = await music_dsp.render_mix(DspMixRequest(revision=0, duration_sec=1.0, inputs=[first, second]),
            {first.id: self.source, second.id: silent}, self.staging, run_command)
        samples, _ = sf.read(self.staging / mixed.artifact_name, dtype='float32', always_2d=True)
        np.testing.assert_array_equal(samples, self.samples)
        compressed = await music_dsp.render_transform(self.request(compression={'threshold_dbfs': -40.0,
            'ratio': 4.0, 'attack_ms': 1.0, 'release_ms': 20.0, 'knee_db': 0.0}), self.source, self.staging, run_command)
        samples, _ = sf.read(self.staging / compressed.artifact_name, dtype='float32', always_2d=True)
        np.testing.assert_allclose(samples[:, 0], -samples[:, 1], atol=1e-7)
        self.assertLess(float(np.sqrt(np.mean(samples[24000:] ** 2))), .05)

    async def test_reference_gain_is_bounded_and_does_not_invent_silent_or_short_loudness(self) -> None:
        from app.audio_quality_contracts import AudioMetrics
        reference = AudioMetrics(duration_sec=10.0, integrated_lufs=-18.0)
        prepared = AudioMetrics(duration_sec=10.0, integrated_lufs=-40.0, true_peak_dbtp=-10.0)
        matched = music_dsp.level_match_gain(reference, prepared)
        self.assertAlmostEqual(matched.gain_db, 12.0412)
        self.assertIn('gain_capped', matched.warnings)
        self.assertGreater(matched.residual_lu, 9.0)
        unavailable = music_dsp.level_match_gain(AudioMetrics(duration_sec=10.0), prepared)
        self.assertEqual(unavailable.gain_db, 0.0)
        self.assertIsNone(unavailable.residual_lu)
        self.assertIn('level_match_unavailable', unavailable.warnings)
        short = music_dsp.level_match_gain(AudioMetrics(duration_sec=1.0, integrated_lufs=-18.0), prepared)
        self.assertIn('level_match_unavailable', short.warnings)

    async def test_inpaint_splice_preserves_original_float_pcm_outside_exact_region(self) -> None:
        original = self.staging / 'original.wav'
        generated = self.staging / 'generated.wav'
        output = self.staging / 'spliced.wav'
        samples = self.samples.copy()
        samples[100, 0] = 1.5
        sf.write(original, samples, 48000, subtype='FLOAT')
        sf.write(generated, np.full((44100, 2), .2, dtype=np.float32), 44100, subtype='PCM_16')
        before = original.read_bytes()
        await music_dsp.splice_inpaint(original, generated, output, sample_rate_hz=48000, channels=2,
            source_frames=48000, output_frames=48000, start_frame=12000, end_frame=36000,
            crossfade_frames=240, run=run_command)
        result, rate = sf.read(output, dtype='float32', always_2d=True)
        self.assertEqual((rate, result.shape), (48000, samples.shape))
        np.testing.assert_array_equal(result[:12000], samples[:12000])
        np.testing.assert_array_equal(result[36000:], samples[36000:])
        np.testing.assert_allclose(result[12240:35760], .2, atol=4e-5)
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(np.all(np.isfinite(result)))

    async def test_inpaint_splice_rejects_lossy_precision_invalid_frames_and_overwrite(self) -> None:
        original = self.staging / 'original.wav'
        generated = self.staging / 'generated.wav'
        output = self.staging / 'spliced.wav'
        sf.write(generated, np.zeros((44100, 2), dtype=np.float32), 44100, subtype='PCM_16')
        for subtype in ('DOUBLE', 'PCM_32'):
            sf.write(original, self.samples, 48000, subtype=subtype)
            with self.subTest(subtype=subtype), self.assertRaises(music_dsp.MusicDspError):
                await music_dsp.splice_inpaint(original, generated, output, sample_rate_hz=48000, channels=2,
                    source_frames=48000, output_frames=48000, start_frame=12000, end_frame=36000,
                    crossfade_frames=240, run=run_command)
            self.assertFalse(output.exists())
        sf.write(original, self.samples, 48000, subtype='FLOAT')
        output.write_bytes(b'existing completed output')
        with self.assertRaises(music_dsp.MusicDspError):
            await music_dsp.splice_inpaint(original, generated, output, sample_rate_hz=48000, channels=2,
                source_frames=48000, output_frames=48000, start_frame=12000, end_frame=36000,
                crossfade_frames=240, run=run_command)
        self.assertEqual(output.read_bytes(), b'existing completed output')
