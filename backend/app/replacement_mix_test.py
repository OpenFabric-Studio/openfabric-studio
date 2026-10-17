from __future__ import annotations

import unittest

from app.music_dsp_contracts import WarpAnchor
from app.replacement_mix import afir_unnormalized_option, phrase_spans


class ReplacementPhraseTests(unittest.TestCase):
    def test_long_map_is_explicitly_partitioned_into_bounded_overlapping_transforms(self) -> None:
        anchors = [WarpAnchor(source_sec=0.0, target_sec=2.0),
            WarpAnchor(source_sec=60.0, target_sec=62.0),
            WarpAnchor(source_sec=120.0, target_sec=134.0)]
        spans = phrase_spans(anchors, 48000)
        self.assertGreater(len(spans), 4)
        self.assertEqual(spans[0][0].target_sec, 2.0)
        self.assertEqual(spans[-1][-1].target_sec, 134.0)
        for span in spans:
            self.assertLessEqual(span[-1].source_sec - span[0].source_sec, 30.0)
        for left, right in zip(spans, spans[1:]):
            self.assertGreater(left[-1].target_sec, right[0].target_sec)
            self.assertAlmostEqual(left[-1].target_sec - right[0].target_sec, .01, places=5)

    def test_collapsed_or_unsafe_maps_are_rejected_before_render(self) -> None:
        for anchors in ([WarpAnchor(source_sec=0.0, target_sec=0.0),
                WarpAnchor(source_sec=.000001, target_sec=.000001)],
                [WarpAnchor(source_sec=0.0, target_sec=0.0), WarpAnchor(source_sec=1.0, target_sec=3.0)]):
            with self.subTest(anchors=anchors), self.assertRaises(ValueError):
                phrase_spans(anchors, 48000)


class ImpulseNormalizationTests(unittest.TestCase):
    def test_newer_afir_help_disables_irnorm_and_legacy_help_uses_gtype_none(self) -> None:
        self.assertEqual(afir_unnormalized_option(b'irnorm\ngtype\nnone\n'), 'irnorm=-1')
        self.assertEqual(afir_unnormalized_option(b'gtype\n none\n irfmt\n'), 'gtype=none')
        self.assertIsNone(afir_unnormalized_option(b'irfmt\nprecision\n'))


class PerformanceCleanupTests(unittest.TestCase):
    def test_bypass_and_bounded_cleanup_preserve_timeline_stereo_and_dry_source(self) -> None:
        import hashlib
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        import numpy as np
        import soundfile as sf
        from app import replacement_mix
        from app.clean_vocal import clean_vocal
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rate = 48000
            signal = np.zeros(rate * 4 + 317, dtype=np.float32)
            signal[rate:2 * rate] = .7 * np.sin(np.arange(rate) / rate * 2 * np.pi * 220)
            signal[2 * rate:3 * rate] = .002 * np.sin(np.arange(rate) / rate * 2 * np.pi * 440)
            signal[rate // 2:rate // 2 + 120] = .05  # transient, not a diagnosed breath
            source, bypass, cleaned = root / 'dry.wav', root / 'bypass.wav', root / 'clean.wav'
            sf.write(source, np.column_stack((signal, -signal)), rate, subtype='FLOAT')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            replacement_mix.prepare_performance_file(source, bypass, cleanup='off', keep_breaths=True)
            np.testing.assert_array_equal(sf.read(bypass, dtype='float32', always_2d=True)[0][:, 0], signal)
            sizes = []
            def bounded(audio, sample_rate):
                sizes.append(len(audio))
                return clean_vocal(audio, sample_rate)
            with patch('app.clean_vocal.clean_vocal', side_effect=bounded):
                replacement_mix.prepare_performance_file(source, cleaned, cleanup='light', keep_breaths=False)
            actual, actual_rate = sf.read(cleaned, dtype='float32', always_2d=True)
            self.assertEqual(actual.shape, (len(signal), 2))
            self.assertEqual(actual_rate, rate)
            self.assertLessEqual(max(sizes), 65536 + 16384)
            self.assertLess(np.linalg.norm(actual[2 * rate:3 * rate, 0]), np.linalg.norm(signal[2 * rate:3 * rate]))
            np.testing.assert_allclose(actual[:, 1], -actual[:, 0], atol=1e-7)
            self.assertLessEqual(np.max(np.abs(actual)), .701)
            self.assertTrue(np.all(actual[:rate // 4] == 0))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)


class OwnedImpulseResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_more_than_32_phrase_outputs_are_stitched_in_bounded_groups_with_exact_frames(self) -> None:
        import hashlib
        from pathlib import Path
        import tempfile
        import numpy as np
        import soundfile as sf
        from app.music_dsp import ResolvedDspSource, render_transform
        from app.music_dsp_contracts import DspTransformRequest, DspTransformResult
        from app.music_analysis_contracts import ProductionMusicSource
        from app import replacement_mix
        from scripts.music_analysis_worker import run_command
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path, rate, frames = root / 'input.wav', 48000, 4800
            signal = np.asarray(.2 * np.sin(np.arange(frames) / rate * 2 * np.pi * 440), dtype=np.float32)
            sf.write(path, np.column_stack((signal, -signal)), rate, subtype='FLOAT')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source = ResolvedDspSource(path, ProductionMusicSource(asset_id='a' * 32, sha256=digest), digest, rate, 2, frames)
            request = DspTransformRequest(source=source.source, source_sha256=digest, revision=1,
                anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0), WarpAnchor(source_sec=.1, target_sec=.1)], output_duration_sec=.1)
            result = await render_transform(request, source, root, run_command)
            phrases = [DspTransformResult.model_validate({**result.model_dump(), 'target_start_frame': index * frames,
                'request': {**request.model_dump(), 'anchors': [{'source_sec': 0.0, 'target_sec': index / 10},
                    {'source_sec': .1, 'target_sec': (index + 1) / 10}]}}) for index in range(33)]
            mixed = await replacement_mix.stitch_phrases(phrases, 3.3, 1, 2, root, run_command)
            actual, _ = sf.read(root / mixed.artifact_name, dtype='float32', always_2d=True)
            self.assertEqual(actual.shape, (frames * 33, 2))
            np.testing.assert_allclose(actual[:, 0], np.tile(signal, 33), atol=2e-6)
            np.testing.assert_array_equal(actual[:, 1], -actual[:, 0])

    async def test_delta_ir_has_exact_stereo_frames_without_hidden_gain_or_clipping(self) -> None:
        import hashlib
        from pathlib import Path
        import tempfile
        import numpy as np
        import soundfile as sf
        from app.music_dsp import ResolvedDspSource
        from app.music_analysis_contracts import ProductionMusicSource
        from app.replacement_mix import convolve_owned_ir
        from scripts.music_analysis_worker import run_command
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            rate = 48000
            time = np.arange(rate) / rate
            signal = np.asarray(1.5 * np.sin(time * 2 * np.pi * 440), dtype=np.float32)
            source, impulse = root / 'source.wav', root / 'ir.wav'
            sf.write(source, np.column_stack((signal, -signal)), rate, subtype='FLOAT')
            kernel = np.zeros(4800, dtype=np.float32)
            kernel[0] = 1
            sf.write(impulse, kernel, rate, subtype='FLOAT')
            original = source.read_bytes(), impulse.read_bytes()
            def resolved(path, identifier, channels, frames):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                return ResolvedDspSource(path, ProductionMusicSource(asset_id=identifier, sha256=digest), digest, rate, channels, frames)
            result, audio = await convolve_owned_ir(resolved(source, 'a' * 32, 2, rate),
                resolved(impulse, 'b' * 32, 1, len(kernel)), .25, root, run_command)
            actual, _ = sf.read(audio.path, dtype='float32', always_2d=True)
            self.assertEqual(actual.shape, (rate, 2))
            np.testing.assert_allclose(actual[:, 0], signal, atol=2e-6)
            np.testing.assert_array_equal(actual[:, 1], -actual[:, 0])
            self.assertEqual(result.ir_gain, 1.0)
            self.assertEqual((source.read_bytes(), impulse.read_bytes()), original)
            kernel[0] = np.float32(.25)
            sf.write(impulse, kernel, rate, subtype='FLOAT')
            result, audio = await convolve_owned_ir(resolved(source, 'a' * 32, 2, rate),
                resolved(impulse, 'b' * 32, 1, len(kernel)), .25, root, run_command)
            actual, _ = sf.read(audio.path, dtype='float32', always_2d=True)
            np.testing.assert_allclose(actual[:, 0], signal * np.float32(.8125), atol=2e-6)
            np.testing.assert_array_equal(actual[:, 1], -actual[:, 0])
            self.assertEqual(result.ir_gain, 1.0)
