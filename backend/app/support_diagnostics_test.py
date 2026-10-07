"""Diagnostic reports are an allowlist, never a redacted log dump."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.module_contracts import ModuleInfo, ModuleInventory, ModuleEvidence
from app.support_contracts import EngineRuntime, SupportAction, SupportReport
from app.support_diagnostics import build_report


class SupportDiagnosticsTests(unittest.TestCase):
    def inventory(self) -> ModuleInventory:
        return ModuleInventory(platform='darwin', architecture='arm64', acceleration='apple_silicon',
            managed_root='/Users/private-person/runtime', free_bytes=1000, checked_at='2026-10-07', modules=[
                ModuleInfo(id='speech', name='Secret profile name', description='Transcript: secret text',
                    state='partial', supported=True, managed=False, automation='manual', dependencies=[],
                    capabilities=['private capability string'], evidence=[ModuleEvidence(code='private', detail='sk-secret /Users/private/audio.wav', verified=False)], actions=[]),
            ])

    def test_report_excludes_private_paths_free_text_names_and_secrets(self) -> None:
        report = build_report(self.inventory(), [EngineRuntime(id='ace_step', state='error', owned=False,
            instance_id=None, idle_state='unknown', can_stop=False)],
            [SupportAction(id='stop_engine', outcome='failed', error_code='engine_busy')])
        value = report.model_dump_json()
        for private in ('/Users', 'Secret', 'Transcript', 'sk-secret', 'private', '1000', 'instance_id', 'audio.wav'):
            self.assertNotIn(private, value)
        self.assertEqual(report.modules[0].id, 'speech')
        self.assertEqual(report.modules[0].state, 'partial')
        self.assertEqual(report.engines[0].state, 'error')
        self.assertEqual(report.recent_actions[0].error_code, 'engine_busy')

    def test_unexpected_host_identifiers_are_not_serialized(self) -> None:
        inventory = self.inventory().model_copy(update={'platform': '/private/custom-os', 'architecture': 'Secret custom chip'})
        report = build_report(inventory, [], [])
        self.assertEqual(report.platform, 'unknown')
        self.assertEqual(report.architecture, 'unknown')
        self.assertNotIn('Secret', report.model_dump_json())

    def test_reports_and_actions_reject_unreviewed_fields_or_free_error_text(self) -> None:
        report = build_report(self.inventory(), [], []).model_dump()
        for extra in ('logs', 'transcript', 'api_key', 'data_dir', 'model_name'):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                SupportReport.model_validate({**report, extra: 'private'})
        with self.assertRaises(ValidationError):
            SupportAction(id='stop_engine', outcome='failed', error_code='private secret failure')

    def test_recent_action_history_is_bounded_and_does_not_include_run_tokens(self) -> None:
        report = build_report(self.inventory(), [], [SupportAction(id='refresh_engines', outcome='completed')] * 100)
        self.assertEqual(len(report.recent_actions), 20)
