from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app.music_beats import BeatRuntimeIdentity, BeatWorkerRequest
from scripts import music_beats_worker as worker


class BeatWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.samples = np.zeros((22050 * 2, 2), dtype=np.float32)
        self.samples[:, 0] = .25; self.samples[:, 1] = -.25
        data = self.samples.tobytes()
        (self.root / 'beats-input.f32').write_bytes(data)
        self.request = BeatWorkerRequest(runtime_root=str(self.root), input_sha256=hashlib.sha256(data).hexdigest(),
            frames=len(self.samples), channels=2, identity=BeatRuntimeIdentity(source_sha256='a' * 64,
                weights_sha256='b' * 64, python_sha256='c' * 64, python_version='3.12.13',
                packages={str(n): '1' for n in range(8)}))

    def test_antiphase_stereo_uses_a_real_channel_not_cancelling_average(self) -> None:
        def tracker(samples: NDArray[np.float32], rate: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
            self.assertEqual(samples.shape, (44100,))
            self.assertEqual(rate, 22050)
            self.assertTrue(np.all(samples == .25))
            return np.array([.5, 1.0]), np.array([.5])
        result = worker.analyze_window(self.request, self.root, tracker)
        self.assertEqual(result.beats_sec, [.5, 1.0])
        self.assertEqual(result.downbeats_sec, [.5])

    def test_input_hash_shape_nonfinite_and_overshoot_fail_before_tracker(self) -> None:
        called = False
        def tracker(samples: NDArray[np.float32], rate: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
            nonlocal called
            called = True
            return np.empty(0), np.empty(0)
        for values in (np.full_like(self.samples, np.nan), np.full_like(self.samples, 17), self.samples[:10]):
            data = values.tobytes(); (self.root / 'beats-input.f32').write_bytes(data)
            request = self.request.model_copy(update={'input_sha256': hashlib.sha256(data).hexdigest()})
            with self.assertRaises(ValueError):
                worker.analyze_window(request, self.root, tracker)
        self.assertFalse(called)
        (self.root / 'beats-input.f32').write_bytes(self.samples.tobytes()[:-4])
        with self.assertRaises(ValueError):
            worker.analyze_window(self.request, self.root, tracker)

    def test_malformed_model_output_is_not_coerced_into_valid_estimates(self) -> None:
        for beats, downbeats in ((np.array([np.nan]), np.empty(0)), (np.array([1., .5]), np.empty(0)),
                                (np.array([.5]), np.array([1.])), (np.array([[.5]]), np.empty(0)),
                                (np.array([.5, 2.021]), np.empty(0)),
                                (np.array([.5]), np.array([2.021])),
                                (np.array([.5, 2.01, 2.0]), np.empty(0)),
                                (np.array([.5, 2.0, 2.0]), np.empty(0)),
                                (np.array([.5]), np.array([np.inf])),
                                (np.array([-.001, .5]), np.empty(0))):
            with self.subTest(beats=beats), self.assertRaises(ValueError):
                worker.analyze_window(self.request, self.root, lambda samples, rate: (beats, downbeats))

    def test_model_endpoint_and_one_frame_padding_events_are_omitted_not_moved(self) -> None:
        for endpoint in (2.0, 2.0 + 1e-9, 2.01, 2.02):
            with self.subTest(endpoint=endpoint):
                result = worker.analyze_window(self.request, self.root,
                    lambda samples, rate: (np.array([.5, 1.5, endpoint]), np.array([.5, endpoint])))
                self.assertEqual(result.duration_sec, 2.0)
                self.assertEqual(result.beats_sec, [.5, 1.5])
                self.assertEqual(result.downbeats_sec, [.5])

    def test_invalid_pcm_is_rejected_before_constructing_or_importing_model(self) -> None:
        data = np.full_like(self.samples, np.nan).tobytes()
        (self.root / 'beats-input.f32').write_bytes(data)
        request = self.request.model_copy(update={'input_sha256': hashlib.sha256(data).hexdigest()})
        (self.root / 'request.json').write_text(request.model_dump_json())
        with patch.object(worker, 'identity', return_value=request.identity), \
                patch.object(worker, 'native_tracker', side_effect=AssertionError('model must not load')) as factory:
            with self.assertRaises(ValueError):
                worker.main(self.root / 'request.json', self.root / 'result.json')
        factory.assert_not_called()

    def test_missing_retained_checkpoint_fails_before_optional_dependency_import(self) -> None:
        with patch.dict(sys.modules, {'torch': None, 'beat_this': None}):
            with self.assertRaises((ValueError, OSError)):
                worker.native_tracker(self.request, self.root)
