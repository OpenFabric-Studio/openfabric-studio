from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import numpy as np
import soundfile as sf

from app import regional_acoustic_helper as acoustic
from app.regional_acoustic_contracts import RegionalAcousticExcerpt
from app.regional_acoustic_helper_test import phrase
from scripts import regional_acoustic_worker as worker


class AcousticWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def request(self) -> acoustic.AcousticWorkerRequest:
        sources = []
        for identifier, signal in (('a', phrase()), ('b', -.31 * phrase()), ('c', phrase(8))):
            path = self.root / (identifier + '.wav'); sf.write(path, signal, 8000, subtype='FLOAT')
            binding = RegionalAcousticExcerpt(excerpt_id=identifier * 32, recording_id=identifier * 32,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), decoded_pcm_sha256=identifier * 64,
                sample_rate_hz=8000, channels=1, start_frame=0, end_frame=len(signal), coverage='screened', landmark_count=0)
            sources.append(acoustic.AcousticWorkerSource(source_path=str(path), source_frames=len(signal), excerpt=binding))
        return acoustic.AcousticWorkerRequest(selection_sha256='d' * 64,
            configuration_sha256=acoustic.configuration_sha256(), sources=sources)

    async def test_actual_bounded_decode_flags_shared_audio_without_claiming_identity(self) -> None:
        request = self.request()
        report = await worker.scan(request, worker.run_command)
        self.assertEqual(len(report.candidates), 1)
        self.assertEqual({report.candidates[0].left_excerpt_id, report.candidates[0].right_excerpt_id}, {'a' * 32, 'b' * 32})
        self.assertTrue(all(item.coverage == 'screened' for item in report.excerpts))
        acoustic.validate_report(report)

    async def test_retained_paired_auditions_are_exact_private_float_wavs_and_capacity_is_explicit(self) -> None:
        request = self.request()
        report = await worker.scan(request, worker.run_command)
        retained = await worker.retain_auditions(request, report, self.root, worker.run_command)
        candidate = retained.candidates[0]
        self.assertIsNotNone(candidate.left_audition); self.assertIsNotNone(candidate.right_audition)
        for side, artifact in (('left', candidate.left_audition), ('right', candidate.right_audition)):
            assert artifact is not None
            path = self.root / 'auditions' / (candidate.id + '-' + side + '.wav')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact.sha256)
            info = sf.info(path)
            self.assertEqual((info.samplerate, info.channels, info.frames, info.subtype), (8000, 1, artifact.frames, 'FLOAT'))
            self.assertLessEqual(artifact.frames, 96000)
        acoustic.validate_report(retained)
        smaller = self.root / 'limited'; smaller.mkdir()
        with patch.object(worker, 'AUDITION_BYTES', 1):
            limited = await worker.retain_auditions(request, report, smaller, worker.run_command)
        self.assertIsNone(limited.candidates[0].left_audition)
        self.assertEqual(limited.candidates[0].audition_unavailable_reason, 'capacity')
        self.assertEqual(list((smaller / 'auditions').iterdir()), [])

    async def test_duplicate_private_path_cannot_claim_a_different_source_hash(self) -> None:
        request = self.request(); original = request.sources[0]
        altered = request.sources[1].model_copy(update={'source_path': original.source_path})
        run = AsyncMock()
        with self.assertRaises(ValueError):
            await worker.scan(request.model_copy(update={'sources': [original, altered]}), run)
        run.assert_not_awaited()

    async def test_source_tampering_and_configuration_changes_fail_before_decoding(self) -> None:
        request = self.request(); run = AsyncMock()
        path = Path(request.sources[0].source_path); path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            await worker.scan(request, run)
        run.assert_not_awaited()
        with self.assertRaises(ValueError):
            await worker.scan(request.model_copy(update={'configuration_sha256': 'f' * 64}), run)
        run.assert_not_awaited()

    async def test_resampled_lossless_copy_is_flagged_with_native_frame_coordinates(self) -> None:
        request = self.request()
        source = request.sources[1]
        path = self.root / 'resampled.flac'
        sf.write(path, np.repeat(-.31 * phrase(), 2, axis=0), 16000, subtype='PCM_16')
        changed = source.model_copy(update={'source_path': str(path), 'source_frames': 192000,
            'excerpt': source.excerpt.model_copy(update={'sample_rate_hz': 16000, 'end_frame': 192000,
                'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})})
        report = await worker.scan(request.model_copy(update={'sources': [request.sources[0], changed, request.sources[2]]}), worker.run_command)
        self.assertEqual(len(report.candidates), 1)
        match = report.candidates[0]
        self.assertAlmostEqual((match.left_end_frame - match.left_start_frame) / 8000,
            (match.right_end_frame - match.right_start_frame) / 16000, places=4)

    async def test_invalid_decoded_pcm_fails_without_a_report(self) -> None:
        request = self.request()
        run = AsyncMock(return_value=np.full((12 * 8000,), np.nan, dtype='<f4').tobytes())
        with self.assertRaises(ValueError):
            await worker.scan(request, run)
