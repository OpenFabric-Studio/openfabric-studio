from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.replacement_contracts import ReplacementPlanResponse
from app.replacement_pitch_contracts import DiagnosticArtifactIdentity
from app.replacement_pitch import PitchDiagnosticError
from app.replacement_tonal import TonalError
from app import replacement_diagnostics as diagnostics


class ReplacementDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(diagnostics, 'pitch_python', AsyncMock(return_value=self.root / 'python')))
        self.plan = ReplacementPlanResponse.model_validate({'id': 'a' * 32,
            'request': {'mode': 'prepared', 'title': 'Fixture',
                'source': {'source': {'kind': 'version', 'track_id': 1, 'version_id': 'b' * 32}, 'audio_sha256': 'c' * 64},
                'prepared': {'source': {'kind': 'version', 'track_id': 2, 'version_id': 'd' * 32}, 'audio_sha256': 'e' * 64}},
            'updated_at': '2026-10-17T10:00:00Z'})
        self.reference = DiagnosticArtifactIdentity(artifact_id='b' * 32, sha256='c' * 64,
            sample_rate_hz=48000, channels=1, duration_frames=720000)
        self.backing = self.reference.model_copy(update={'artifact_id': 'd' * 32, 'sha256': 'e' * 64})

    async def measure(self):
        return await diagnostics.measure_diagnostics(self.plan, 'f' * 32, self.root, self.root,
            self.reference, self.backing, AsyncMock())

    async def test_unavailable_advisory_workers_are_explicit_without_changing_settings(self) -> None:
        original = self.plan.model_dump()
        with patch.object(diagnostics, 'analyze_owned_pitch', AsyncMock(side_effect=PitchDiagnosticError('pitch_budget_exceeded'))), \
                patch.object(diagnostics, 'analyze_owned_tonal', AsyncMock(side_effect=TonalError('replacement_tonal_unavailable'))):
            report = await self.measure()
        self.assertEqual(report.unavailable, ['pitch', 'tonal'])
        self.assertEqual(self.plan.model_dump(), original)

    async def test_integrity_errors_are_fatal_and_never_become_unavailable_success(self) -> None:
        with patch.object(diagnostics, 'analyze_owned_pitch', AsyncMock(side_effect=PitchDiagnosticError('pitch_artifact_changed'))), \
                patch.object(diagnostics, 'analyze_owned_tonal', AsyncMock()) as tonal:
            with self.assertRaises(diagnostics.DiagnosticsError) as error:
                await self.measure()
            self.assertEqual(error.exception.code, 'replacement_artifact_changed')
            tonal.assert_not_awaited()

    async def test_cancellation_stops_the_composed_measurement_without_a_report(self) -> None:
        with patch.object(diagnostics, 'analyze_owned_pitch', AsyncMock(side_effect=asyncio.CancelledError)), \
                patch.object(diagnostics, 'analyze_owned_tonal', AsyncMock()) as tonal:
            with self.assertRaises(asyncio.CancelledError):
                await self.measure()
            tonal.assert_not_awaited()

    async def test_no_compatible_pitch_runtime_skips_the_worker_without_installing_anything(self) -> None:
        with patch.object(diagnostics, 'pitch_python', AsyncMock(return_value=None)), \
                patch.object(diagnostics, 'analyze_owned_pitch', AsyncMock(side_effect=AssertionError('Unverified runtime used'))) as pitch, \
                patch.object(diagnostics, 'analyze_owned_tonal', AsyncMock(side_effect=TonalError('replacement_tonal_unavailable'))):
            report = await self.measure()
        self.assertEqual(report.unavailable, ['pitch', 'tonal'])
        pitch.assert_not_awaited()
