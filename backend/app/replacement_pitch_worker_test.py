from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

from app import replacement_pitch as pitch
from app.replacement_pitch_contracts import (DiagnosticArtifactIdentity, PitchWorkerArtifact,
    PitchWorkerInput, ReplacementPitchWorkerOutcome)
from scripts import replacement_pitch_worker as worker


class PitchWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.artifacts = self.root / 'artifacts'; self.artifacts.mkdir()
        path = self.artifacts / ('a' * 32 + '.wav')
        time = np.arange(48000) / 16000
        sf.write(path, .2 * np.sin(2 * np.pi * 220 * time), 16000, subtype='FLOAT')
        self.identity = DiagnosticArtifactIdentity(artifact_id='a' * 32,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sample_rate_hz=16000,
            channels=1, duration_frames=48000)
        self.request = PitchWorkerInput(artifact_root=str(self.artifacts),
            artifacts=[PitchWorkerArtifact(identity=self.identity, role='reference')])

    async def test_real_isolated_cpu_worker_returns_typed_bound_report(self) -> None:
        request, result = self.root / 'request.json', self.root / 'result.json'
        request.write_text(self.request.model_dump_json())
        completed = await asyncio.to_thread(subprocess.run, [sys.executable, '-I', str(Path(worker.__file__).resolve()),
            '--request', str(request), '--result', str(result)], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        outcome = ReplacementPitchWorkerOutcome.model_validate_json(result.read_bytes())
        self.assertEqual(outcome.input_sha256, hashlib.sha256(request.read_bytes()).hexdigest())
        self.assertIsNotNone(outcome.result)
        assert outcome.result is not None
        self.assertEqual(outcome.result.artifacts[0].identity, self.identity)
        self.assertEqual(outcome.result.artifacts[0].status, 'observed')
        self.assertIsNone(outcome.result.confidence)

    async def test_owned_wrapper_uses_callback_and_preserves_stable_integrity_error(self) -> None:
        seen: list[list[str]] = []
        async def run(argv: list[str], timeout: float) -> bytes:
            seen.append(argv)
            self.assertLessEqual(timeout, 600)
            request = Path(argv[argv.index('--request') + 1])
            result = Path(argv[argv.index('--result') + 1])
            self.assertEqual(worker.main(request, result), 0)
            return b''
        report = await pitch.analyze_owned_pitch(self.request, self.root, run)
        self.assertEqual(len(seen), 1)
        self.assertEqual(report.artifacts[0].identity, self.identity)
        changed = self.request.model_copy(update={'artifacts': [self.request.artifacts[0].model_copy(update={
            'identity': self.identity.model_copy(update={'sha256': 'b' * 64})})]})
        with self.assertRaises(pitch.PitchDiagnosticError) as error:
            await pitch.analyze_owned_pitch(changed, self.root, run)
        self.assertEqual(error.exception.code, 'pitch_artifact_changed')

    async def test_owned_wrapper_rejects_wrong_digest_or_foreign_report(self) -> None:
        async def wrong(argv: list[str], timeout: float) -> bytes:
            request, result = Path(argv[-3]), Path(argv[-1])
            self.assertEqual(worker.main(request, result), 0)
            outcome = ReplacementPitchWorkerOutcome.model_validate_json(result.read_bytes())
            result.write_text(outcome.model_copy(update={'input_sha256': 'c' * 64}).model_dump_json())
            return b''
        with self.assertRaises(pitch.PitchDiagnosticError) as error:
            await pitch.analyze_owned_pitch(self.request, self.root, wrong)
        self.assertEqual(error.exception.code, 'pitch_worker_output_invalid')

    async def test_cancellation_propagates_and_keeps_owned_child_workspace(self) -> None:
        async def cancelled(argv: list[str], timeout: float) -> bytes:
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await pitch.analyze_owned_pitch(self.request, self.root, cancelled)
        self.assertEqual(len(list(self.root.glob('pitch-*/input.json'))), 1)

    async def test_owned_wrapper_rejects_unbound_configuration(self) -> None:
        async def wrong(argv: list[str], timeout: float) -> bytes:
            request, result = Path(argv[-3]), Path(argv[-1])
            self.assertEqual(worker.main(request, result), 0)
            outcome = ReplacementPitchWorkerOutcome.model_validate_json(result.read_bytes())
            assert outcome.result is not None
            report = outcome.result.model_copy(update={'configuration_sha256': 'c' * 64})
            result.write_text(outcome.model_copy(update={'result': report}).model_dump_json())
            return b''
        with self.assertRaises(pitch.PitchDiagnosticError) as error:
            await pitch.analyze_owned_pitch(self.request, self.root, wrong)
        self.assertEqual(error.exception.code, 'pitch_worker_output_invalid')

    async def test_malformed_request_or_existing_result_is_not_overwritten(self) -> None:
        request, result = self.root / 'request.json', self.root / 'result.json'
        request.write_text('{}')
        self.assertEqual(worker.main(request, result), 2)
        self.assertFalse(result.exists())
        request.write_text(self.request.model_dump_json()); result.write_bytes(b'previous')
        self.assertEqual(worker.main(request, result), 2)
        self.assertEqual(result.read_bytes(), b'previous')
