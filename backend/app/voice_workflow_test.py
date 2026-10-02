"""Duration bounds and selected-variant coverage are backend policy."""
from __future__ import annotations

import tempfile
import unittest
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.voice_contracts import AnalyzeVoiceCoverageRequest, PrepareVoiceRequest, SelectVoiceSamplesRequest, VoiceCoverageMeasurement, VoicePreparationResponse, VoiceReferenceCandidate, VoiceSegment
from app import voice_preparation as prep


class DurationTests(unittest.TestCase):
    def test_preparation_budget_is_bounded_and_defaults_to_fifteen_minutes(self) -> None:
        self.assertEqual(PrepareVoiceRequest().max_selected_seconds, 900)
        self.assertEqual(PrepareVoiceRequest(max_selected_seconds=3600).max_selected_seconds, 3600)
        for value in (0, 59, 3601, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                PrepareVoiceRequest(max_selected_seconds=value)

    def test_selection_uses_prepared_budget_instead_of_fixed_fifteen_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            segments = [VoiceSegment(id=str(index), source_filename='voice.wav', start_sec=index * 10,
                end_sec=(index + 1) * 10, duration_sec=10, score=.8, periodicity=.9,
                level_db=-16, peak=.8, clipped_fraction=0, accepted=True, reasons=[]) for index in range(100)]
            reference = VoiceReferenceCandidate(id='ref', segment_id='0', source_filename='voice.wav',
                start_sec=0, end_sec=10, duration_sec=10, score=.8)
            request = SelectVoiceSamplesRequest(revision='current', segment_ids=[item.id for item in segments], reference_id='ref')
            for budget, allowed in ((900, False), (1800, True)):
                prep.save(path, prep.PreparationDocument(response=VoicePreparationResponse(revision='current', status='done',
                    options=PrepareVoiceRequest(max_selected_seconds=budget), segments=segments, references=[reference])))
                if allowed:
                    self.assertEqual(prep.select(path, request).accepted_seconds, 1000)
                else:
                    with self.assertRaises(HTTPException) as raised:
                        prep.select(path, request)
                    self.assertEqual(raised.exception.detail, 'too_much_audio')

    def test_saved_budget_can_increase_without_repeating_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            segments = [VoiceSegment(id=str(index), source_filename='voice.wav', start_sec=index * 10,
                end_sec=(index + 1) * 10, duration_sec=10, score=.8, periodicity=.9,
                level_db=-16, peak=.8, clipped_fraction=0, accepted=True, reasons=[]) for index in range(100)]
            reference = VoiceReferenceCandidate(id='ref', segment_id='0', source_filename='voice.wav', start_sec=0, end_sec=10, duration_sec=10, score=.8)
            prep.save(path, prep.PreparationDocument(response=VoicePreparationResponse(revision='saved', status='done',
                options=PrepareVoiceRequest(max_selected_seconds=900), segments=segments, references=[reference]), generation='preserved'))
            response = prep.select(path, SelectVoiceSamplesRequest(revision='saved', segment_ids=[item.id for item in segments], reference_id='ref', max_selected_seconds=1800))
            self.assertEqual(response.accepted_seconds, 1000)
            self.assertEqual(response.options.max_selected_seconds, 1800)
            self.assertEqual(prep.load(path).generation, 'preserved')
            self.assertFalse(prep.busy())


class CoverageJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path / 'recordings').mkdir()
        (self.path / 'recordings' / 'voice.wav').write_bytes(b'source')
        from app.atomic_files import write_object
        write_object(self.path / 'voice.json', {'recordings': [{'filename': 'voice.wav'}]})
        for name in ('original.wav', 'cleaned.wav'):
            (self.path / name).write_bytes(b'audio')
        self.measurement = VoiceCoverageMeasurement(duration_sec=10, analyzed_sec=9.8, voiced_sec=8,
            reliable_voiced_sec=7, pitch_median_hz=220, source_sample_rate=44100)
        self.segment = VoiceSegment(id='one', source_filename='voice.wav', start_sec=0, end_sec=10,
            duration_sec=10, score=.8, periodicity=.9, level_db=-16, peak=.8, clipped_fraction=0,
            accepted=True, reasons=[], has_cleaned=True, original_coverage=self.measurement)
        prep.save(self.path, prep.PreparationDocument(response=VoicePreparationResponse(revision='saved', status='done',
            segments=[self.segment], selected_segment_ids=['one'], accepted_seconds=10, reference_id='ref',
            references=[VoiceReferenceCandidate(id='ref', segment_id='one', source_filename='voice.wav', start_sec=0, end_sec=10, duration_sec=10, score=.8)]),
            fingerprints=prep.fingerprints(self.path), samples={'one': prep.SampleFiles(original='original.wav', cleaned='cleaned.wav')}))

    async def asyncTearDown(self) -> None:
        await prep.shutdown()
        self.temp.cleanup()

    async def test_selection_aggregates_selected_variant_and_reports_missing_measurements(self) -> None:
        selected = prep.select(self.path, SelectVoiceSamplesRequest(revision='saved', segment_ids=['one'], reference_id='ref'))
        self.assertIsNotNone(selected.coverage)
        self.assertEqual(selected.coverage.measurements, 1)
        self.assertEqual(selected.coverage.reliable_voiced_sec, 7)
        selected = prep.select(self.path, SelectVoiceSamplesRequest(revision=selected.revision, segment_ids=['one'], reference_id='ref', cleaned_segment_ids=['one']))
        self.assertEqual(selected.coverage.measurements, 0)
        self.assertEqual(selected.coverage.unavailable_segments, 1)

    async def test_coverage_rejects_stale_revision_and_source_changes(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='old'))
        self.assertEqual(raised.exception.detail, 'stale_preparation')
        (self.path / 'recordings' / 'voice.wav').write_bytes(b'changed')
        with self.assertRaises(HTTPException) as raised:
            prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='saved'))
        self.assertEqual(raised.exception.detail, 'source_changed')

    async def test_cached_analysis_does_not_spawn_or_repeat_separation(self) -> None:
        from app import voice_build, voice_separation
        with patch.object(voice_build, '_spawn') as spawn, patch.object(voice_separation, 'separate_vocal') as separation:
            queued = prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='saved'))
            self.assertEqual(queued.operation, 'coverage')
            task = prep._jobs[self.path.resolve()].task
            self.assertIsNotNone(task)
            await task
        self.assertEqual(prep.get(self.path).status, 'done')
        self.assertEqual(prep.get(self.path).coverage.measurements, 1)
        spawn.assert_not_called()
        separation.assert_not_called()

    async def test_batch_analyzes_only_missing_selected_variant(self) -> None:
        from app import voice_build
        from app.voice_coverage import CoverageBatchInput, CoverageBatchReport
        document = prep.load(self.path)
        document.response.cleaned_segment_ids = ['one']
        prep.save(self.path, document)
        seen: list[str] = []
        async def spawn(argv: list[str], **kwargs: object) -> int:
            manifest = CoverageBatchInput.model_validate_json(Path(argv[-1]).read_text())
            seen.extend(item.path for item in manifest.inputs)
            return 0
        report = CoverageBatchReport(measurements={'one_cleaned': self.measurement}, failed_ids=[]).model_dump_json()
        with patch.object(voice_build, '_engine_python', return_value=Path(sys.executable)), patch.object(voice_build, '_spawn', side_effect=spawn), patch('app.orchestrator.process.tail_log', return_value=report):
            prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='saved'))
            task = prep._jobs[self.path.resolve()].task
            await task
        self.assertEqual(seen, [str(self.path / 'cleaned.wav')])
        self.assertEqual(prep.get(self.path).coverage.measurements, 1)
        self.assertIsNotNone(prep.load(self.path).response.segments[0].cleaned_coverage)

    async def test_coverage_cancellation_owns_process_and_releases_busy_slot(self) -> None:
        document = prep.load(self.path)
        document.response.options.singer_confirmed = True
        prep.save(self.path, document)
        prep.validated(self.path, 'saved')
        entered = asyncio.Event()
        async def blocked(path: Path, document: prep.PreparationDocument, job: prep.PreparationJob) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(prep, '_run_coverage', side_effect=blocked):
            prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='saved'))
            await entered.wait()
            self.assertTrue(prep.busy())
            cancelled = await prep.cancel(self.path)
        self.assertEqual(cancelled.status, 'done')
        self.assertIn('coverage_cancelled', cancelled.warnings)
        prep.validated(self.path, 'saved')
        self.assertFalse(prep.busy())

    async def test_cancel_during_finished_coverage_cleanup_preserves_success(self) -> None:
        from app import voice_build
        entered = asyncio.Event()
        release = asyncio.Event()

        async def cleanup(proc: object) -> None:
            entered.set()
            await release.wait()

        with patch.object(voice_build, 'kill_proc', side_effect=cleanup):
            prep.start_coverage(self.path, AnalyzeVoiceCoverageRequest(revision='saved'))
            await entered.wait()
            completed = prep.load(self.path)
            self.assertEqual(completed.response.status, 'done')
            self.assertIsNotNone(completed.response.progress)
            cancellation = asyncio.create_task(prep.cancel(self.path))
            await asyncio.sleep(0)
            self.assertFalse(cancellation.done())
            release.set()
            result = await cancellation

        self.assertEqual(result.model_dump(), completed.response.model_dump())
        self.assertNotIn('coverage_cancelled', result.warnings)
        self.assertEqual(prep.load(self.path).model_dump(), completed.model_dump())
        self.assertEqual((self.path / 'original.wav').read_bytes(), b'audio')
        self.assertFalse(prep.busy())

    async def test_interrupted_optional_coverage_preserves_review_and_checks_sources(self) -> None:
        document = prep.load(self.path)
        document.response.options.singer_confirmed = True
        document.response.status, document.response.operation = 'running', 'coverage'
        prep.save(self.path, document)
        response = prep.get(self.path)
        self.assertEqual(response.status, 'done')
        self.assertIn('coverage_interrupted', response.warnings)
        prep.validated(self.path, 'saved')
        (self.path / 'recordings' / 'voice.wav').write_bytes(b'changed')
        self.assertEqual(prep.get(self.path).error_code, 'source_changed')

    async def test_selection_rejects_old_accepted_samples_outside_engine_duration(self) -> None:
        for duration in (.7, 31):
            document = prep.load(self.path)
            document.response.revision = 'saved'
            document.response.segments[0].duration_sec = duration
            document.response.segments[0].end_sec = duration
            prep.save(self.path, document)
            with self.subTest(duration=duration):
                with self.assertRaises(HTTPException) as raised:
                    prep.select(self.path, SelectVoiceSamplesRequest(revision='saved', segment_ids=['one'], reference_id='ref'))
                self.assertEqual(raised.exception.detail, 'invalid_selection')

    def test_coverage_contract_rejects_impossible_durations_and_pitch_order(self) -> None:
        valid = self.measurement.model_dump()
        for update in ({'analyzed_sec': 11}, {'reliable_voiced_sec': 9},
            {'pitch_p05_hz': 300, 'pitch_median_hz': 200, 'pitch_p95_hz': 500},
            {'pitch_bins': [{'midi_note': 57, 'seconds': 10}]}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                VoiceCoverageMeasurement.model_validate({**valid, **update})
