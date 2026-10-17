from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from app.replacement_contracts import (ReplacementFitSettings, ReplacementMixSettings,
    ReplacementMixEditRequest, ReplacementPlanResponse, ReplacementRender, replacement_request_adapter)


class ReplacementContractTests(unittest.TestCase):
    def request(self) -> dict[str, object]:
        return {'mode': 'prepared', 'title': 'Prepared fixture',
            'source': {'source': {'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32}, 'audio_sha256': 'b' * 64},
            'prepared': {'source': {'kind': 'version', 'track_id': 2, 'version_id': 'c' * 32}, 'audio_sha256': 'd' * 64}}

    def test_prepared_mode_needs_no_unused_voice_and_forbids_converter_target(self) -> None:
        body = self.request()
        result = replacement_request_adapter.validate_json(json.dumps(body))
        self.assertEqual(result.mode, 'prepared')
        self.assertNotIn('target', result.model_dump())
        body['target'] = {'engine': 'seed-vc', 'voice_id': 'e' * 32, 'model_revision_id': 'trained'}
        with self.assertRaises(ValidationError):
            replacement_request_adapter.validate_json(json.dumps(body))

    def test_modes_sources_and_converter_identity_are_discriminated(self) -> None:
        body = self.request()
        for changes in ({'mode': 'convert'}, {'mode': 'hybrid'}, {'mode': 'unknown'},
                {'source': {'source': {'kind': 'path', 'path': '../private.wav'}, 'audio_sha256': 'b' * 64}},
                {'prepared': body['source']}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                replacement_request_adapter.validate_json(json.dumps({**body, **changes}))
        converted = {key: value for key, value in body.items() if key != 'prepared'}
        converted.update(mode='convert', target={'engine': 'seed-vc', 'voice_id': 'e' * 32, 'model_revision_id': 'trained'})
        self.assertEqual(replacement_request_adapter.validate_json(json.dumps(converted)).mode, 'convert')

    def test_pitch_components_cannot_exceed_the_actual_combined_native_limit(self) -> None:
        self.assertEqual(ReplacementFitSettings(pitch_shift_semitones=12, pitch_nudge_semitones=-1.0).pitch_semitones, 11.0)
        self.assertEqual(ReplacementFitSettings(pitch_shift_semitones=-12, pitch_nudge_semitones=1.0).pitch_semitones, -11.0)
        for body in ({'pitch_shift_semitones': 12, 'pitch_nudge_semitones': .1},
                {'pitch_shift_semitones': -12, 'pitch_nudge_semitones': -.1},
                {'pitch_shift_semitones': True}, {'pitch_nudge_semitones': float('nan')}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ReplacementFitSettings.model_validate(body)

    def test_mix_ir_and_loudness_choices_cannot_be_silently_ignored(self) -> None:
        for body in ({'space': 'owned_ir'}, {'ir_asset_id': 'a' * 32}, {'wet': .1},
                {'master_level': 'off', 'loudness': {'profile': 'music'}},
                {'compression': {'makeup_db': 4.0}}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ReplacementMixSettings.model_validate(body)

    def test_render_done_requires_artifact_and_live_track_or_explicit_deletion_tombstone(self) -> None:
        body = {'id': 'a' * 32, 'plan_id': 'b' * 32, 'revision': 1, 'request_id': 'c' * 32,
            'status': 'done', 'settings_sha256': 'd' * 64, 'created_at': '2026-10-17T10:00:00Z'}
        with self.assertRaises(ValidationError):
            ReplacementRender.model_validate(body)
        with self.assertRaises(ValidationError):
            ReplacementRender.model_validate({**body, 'master_artifact_id': 'e' * 32})
        result = ReplacementRender.model_validate({**body, 'master_artifact_id': 'e' * 32, 'track_deleted': True})
        self.assertIsNone(result.track_id)

    def test_current_mix_is_reloadable_and_edits_require_a_real_revision(self) -> None:
        plan = ReplacementPlanResponse.model_validate_json(json.dumps({'id': 'a' * 32,
            'request': self.request(), 'updated_at': '2026-10-17T10:00:00Z'}))
        self.assertEqual(plan.mix, ReplacementMixSettings())
        with self.assertRaises(ValidationError):
            ReplacementMixEditRequest.model_validate({'expected_revision': True, 'mix': {}})

    def test_saved_diagnostics_are_bound_to_exact_request_and_revision(self) -> None:
        import hashlib
        from app.replacement_diagnostics_contracts import ReplacementDiagnostics
        plan = ReplacementPlanResponse.model_validate_json(json.dumps({'id': 'a' * 32,
            'request': self.request(), 'updated_at': '2026-10-17T10:00:00Z'}))
        checksum = hashlib.sha256(json.dumps(plan.request.model_dump(mode='json'), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        report = ReplacementDiagnostics(revision=1, request_sha256=checksum, attempt_id='b' * 32,
            created_at=plan.updated_at, unavailable=['pitch', 'tonal'])
        saved = ReplacementPlanResponse.model_validate({**plan.model_dump(), 'diagnostics': report})
        self.assertEqual(saved.diagnostics, report)
        for changes in ({'request_sha256': 'f' * 64}, {'revision': 2}, {'alignment_sha256': 'e' * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ReplacementPlanResponse.model_validate({**plan.model_dump(),
                    'diagnostics': {**report.model_dump(), **changes}})
