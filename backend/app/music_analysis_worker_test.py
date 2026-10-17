from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, patch

import numpy as np

from app import music_analysis
from app.audio_quality import Measurement, LoudnormReport
from app.audio_quality_contracts import AudioMetrics
from app.music_analysis_contracts import VersionMusicSource
from app.music_beats import BeatAnalysisRecipe, BeatRuntimeIdentity, BeatWindowResult
from scripts import music_analysis_worker as worker


class AnalysisWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / 'source.wav'
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(8000 * 2))
        self.sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.version_sha256 = hashlib.sha256(b'ffmpeg version test\n').hexdigest()
        self.request = music_analysis.AnalysisWorkerRequest(job_id='a' * 32, result_id='b' * 32,
            source=VersionMusicSource(track_id=1, version_id='c' * 32), snapshot_name=self.source.name,
            audio_sha256=self.sha256, configuration_sha256=music_analysis.configuration_digest('ffmpeg version test', self.version_sha256),
            ffmpeg_version='ffmpeg version test', ffmpeg_sha256=self.version_sha256, created_at='2026-10-17T00:00:00Z')

    async def test_optional_beats_are_explicit_source_bound_and_meter_stays_unknown(self) -> None:
        identity = BeatRuntimeIdentity(source_sha256='d' * 64, weights_sha256='e' * 64,
            python_sha256='f' * 64, python_version='3.12.13', packages={str(n): '1' for n in range(8)})
        request = music_analysis.AnalysisWorkerRequest.model_validate({**self.request.model_dump(),
            'beats': BeatAnalysisRecipe(runtime_root=str(self.root), identity=identity).model_dump(),
            'configuration_sha256': music_analysis.configuration_digest('ffmpeg version test', self.version_sha256, beats=identity)})
        metric = AudioMetrics(duration_sec=10.0, source_sha256=self.sha256, samples_analyzed=80000,
            ffmpeg_version='ffmpeg version test')
        measurement = Measurement(metric, LoudnormReport(input_i='-inf', input_tp='-inf', input_lra='0', input_thresh='-70', target_offset='0'))
        audio = np.full((220500, 1), .25, dtype=np.float32)
        infer = AsyncMock(return_value=BeatWindowResult(duration_sec=10.0,
            beats_sec=[float(n) / 2 for n in range(20)], downbeats_sec=[float(n) * 2 for n in range(5)]))
        with patch.object(worker, 'probe', new=AsyncMock(return_value=worker.AudioHeader(8000, 1, 10.0))), \
                patch.object(worker, 'measure', new=AsyncMock(return_value=measurement)), \
                patch.object(worker, 'decode_window', new=AsyncMock(return_value=audio)), \
                patch.object(worker, 'infer_window', new=infer):
            result = await worker.analyze_snapshot(request, self.root, AsyncMock(return_value=b'ffmpeg version test\n'))
        infer.assert_awaited_once()
        self.assertEqual(result.weights_sha256, identity.weights_sha256)
        self.assertEqual(result.tempo.bpm, 120.0)
        self.assertIsNone(result.tempo.confidence)
        self.assertEqual(result.meter.status, 'unknown')
        beat_engine = next(engine for engine in result.engines if engine.task == 'beats')
        self.assertEqual(beat_engine.configuration_sha256, identity.configuration_sha256)
        self.assertEqual(beat_engine.weights_sha256, identity.weights_sha256)
        self.assertIn('beat-this-small0-weights-mit', result.license_evidence_ids)

    async def test_probe_budget_rejects_before_full_decode(self) -> None:
        run = AsyncMock(return_value=b'{"streams":[{"sample_rate":"192000","channels":2,"duration":"7200"}]}')
        with self.assertRaises(music_analysis.MusicAnalysisError) as error:
            await worker.probe(self.source, self.root, run)
        self.assertEqual(error.exception.code, 'decode_budget_exceeded')
        self.assertEqual(run.await_count, 1)

    async def test_malformed_probe_and_multichannel_are_rejected(self) -> None:
        for sample in (b'{"streams":[]}', b'{"streams":[{"sample_rate":"48000","channels":3,"duration":"1"}]}',
                       b'{"streams":[{"sample_rate":"48000","channels":1,"duration":"nan"}]}'):
            with self.subTest(sample=sample), self.assertRaises(music_analysis.MusicAnalysisError):
                await worker.probe(self.source, self.root, AsyncMock(return_value=sample))

    async def test_windows_are_bounded_and_long_structure_is_unknown(self) -> None:
        duration = 241.0
        metric = AudioMetrics(duration_sec=duration, source_sha256=self.sha256, samples_analyzed=8000 * 241,
            ffmpeg_version='ffmpeg version test')
        measurement = Measurement(metric, LoudnormReport(input_i='-inf', input_tp='-inf', input_lra='0', input_thresh='-70', target_offset='0'))
        spans: list[tuple[float, float]] = []

        async def decode(path: Path, root: Path, start: float, seconds: float, channels: int, run: worker.RunCommand) -> np.ndarray:
            spans.append((start, seconds))
            self.assertLessEqual(seconds, 120)
            return np.zeros((round(seconds * 22050), channels), dtype=np.float32)

        with patch.object(worker, 'probe', new=AsyncMock(return_value=worker.AudioHeader(8000, 1, duration))), \
                patch.object(worker, 'measure', new=AsyncMock(return_value=measurement)), \
                patch.object(worker, 'decode_window', side_effect=decode):
            result = await worker.analyze_snapshot(self.request, self.root, AsyncMock(return_value=b'ffmpeg version test\n'))
        self.assertEqual(spans, [(0.0, 120.0), (110.0, 120.0), (220.0, 21.0)])
        self.assertEqual(result.duration_frames, 1928000)
        self.assertIn('analysis_partial', result.warnings)
        self.assertEqual((result.key.status, result.meter.status, result.tempo.status), ('unknown', 'unknown', 'unknown'))
        self.assertEqual((result.sections, result.beats_sec), ([], []))
        self.assertLessEqual(len(result.energy), 4000)

    async def test_truncated_pcm_cannot_be_published_as_complete(self) -> None:
        async def run(argv: list[str], timeout: float) -> bytes:
            Path(argv[-1]).write_bytes(bytes(4 * 22050 // 2))
            return b''
        with self.assertRaises(music_analysis.MusicAnalysisError) as error:
            await worker.decode_window(self.source, self.root, 0.0, 1.0, 1, run)
        self.assertEqual(error.exception.code, 'invalid_audio')

    async def test_real_short_cpu_snapshot_and_loudness_are_source_bound(self) -> None:
        from app.video_media import tool
        output = await worker.run_command([tool('ffmpeg'), '-version'], 10.0)
        actual_version = output.decode().splitlines()[0][:200]
        request = music_analysis.AnalysisWorkerRequest.model_validate({**self.request.model_dump(),
            'ffmpeg_version': actual_version, 'ffmpeg_sha256': hashlib.sha256(output).hexdigest(),
            'configuration_sha256': music_analysis.configuration_digest(actual_version, hashlib.sha256(output).hexdigest())})
        result = await worker.analyze_snapshot(request, self.root, worker.run_command)
        self.assertEqual(result.audio_sha256, self.sha256)
        self.assertEqual(result.duration_frames, 8000)
        self.assertIsNone(result.loudness.integrated_lufs)
        self.assertIn('silence', result.warnings)
        self.assertEqual(result.key.status, 'unknown')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.sha256)
        self.assertFalse((self.root / 'window.f32').exists())

    async def test_underreported_flac_header_cannot_exceed_reserved_decode_disk(self) -> None:
        from app.video_media import tool
        source = self.root / 'source.flac'
        await worker.run_command([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=8000:duration=10', '-c:a', 'flac', str(source)], 15.0)
        encoded = bytearray(source.read_bytes())
        packed = int.from_bytes(encoded[18:26], 'big')
        encoded[18:26] = ((packed & ~((1 << 36) - 1)) | 8000).to_bytes(8, 'big')
        source.write_bytes(encoded)
        source_hash = hashlib.sha256(encoded).hexdigest()
        version = await worker.run_command([tool('ffmpeg'), '-version'], 10.0)
        identity = version.decode().splitlines()[0][:200]
        version_hash = hashlib.sha256(version).hexdigest()
        request = music_analysis.AnalysisWorkerRequest.model_validate({**self.request.model_dump(),
            'snapshot_name': source.name, 'audio_sha256': source_hash, 'ffmpeg_version': identity,
            'ffmpeg_sha256': version_hash, 'configuration_sha256': music_analysis.configuration_digest(identity, version_hash)})
        peak_sizes: list[int] = []

        async def run(argv: list[str], timeout: float) -> bytes:
            output = await worker.run_command(argv, timeout)
            if argv[-1].endswith('.analysis.f32'):
                peak_sizes.append(Path(argv[-1]).stat().st_size)
            return output

        with patch.object(worker, 'decode_window', side_effect=AssertionError('truncated measurement reached features')):
            with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                await worker.analyze_snapshot(request, self.root, run)
        self.assertEqual(error.exception.code, 'decode_budget_exceeded')
        self.assertEqual(len(peak_sizes), 1)
        self.assertLessEqual(peak_sizes[0], music_analysis.decoded_byte_budget(worker.AudioHeader(8000, 1, 1.0)))
        self.assertLess(peak_sizes[0], 320000)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
