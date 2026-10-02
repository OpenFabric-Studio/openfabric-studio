"""Reviewable preparation keeps provenance, rejects stale edits, and owns work."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import voice_build as voice
from app.atomic_files import write_object
from app.voice_contracts import PrepareVoiceRequest, SelectVoiceSamplesRequest, VoiceSegment, VoiceSourceSelection


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.voice_id = 'a' * 32
        self.path = self.root / self.voice_id
        (self.path / 'recordings').mkdir(parents=True)
        (self.path / 'recordings' / 'voice.wav').write_bytes(b'original')
        write_object(self.path / 'voice.json', {'id': self.voice_id, 'name': 'Test', 'created_at': 'now', 'status': 'idle', 'recordings': [{'filename': 'voice.wav', 'bytes': 8}]})
        self.patch = patch.object(voice, 'VOICES_DIR', self.root)
        self.patch.start()

    async def asyncTearDown(self) -> None:
        await voice.shutdown()
        self.patch.stop()
        self.temp.cleanup()

    async def test_preparation_routes_are_separate_from_training(self) -> None:
        from app import voice_preparation as prep
        async def blocked(path: Path, document: object, job: object) -> None:
            await asyncio.Event().wait()
        with patch.object(prep, '_run', new=blocked):
            response = prep.start(self.path, PrepareVoiceRequest(singer_confirmed=True))
            self.assertEqual(response.status, 'queued')
            self.assertFalse(voice._builds)
            await prep.cancel(self.path)
        self.assertEqual(prep.get(self.path).status, 'cancelled')

    async def test_queued_cancel_timing_is_persisted_without_fake_start(self) -> None:
        from app import voice_preparation as prep
        async def blocked(path: Path, document: object, job: object) -> None:
            await asyncio.Event().wait()
        with patch.object(prep, '_run', new=blocked):
            response = prep.start(self.path, PrepareVoiceRequest(singer_confirmed=True))
            self.assertIsNotNone(response.progress)
            if response.progress is not None:
                queued_at = response.progress.queued_at
                self.assertIsNone(response.progress.started_at)
                self.assertEqual(response.progress.files_completed, 0)
                self.assertEqual(response.progress.files_total, 1)
                await prep.cancel(self.path)
                reloaded = prep.load(self.path).response.progress
                self.assertIsNotNone(reloaded)
                if reloaded is not None:
                    self.assertEqual(reloaded.queued_at, queued_at)
                    self.assertIsNone(reloaded.started_at)
                    self.assertIsNotNone(reloaded.finished_at)
                    self.assertEqual(reloaded.status, 'cancelled')

    async def test_cancel_during_final_cleanup_preserves_completed_preparation(self) -> None:
        from app import voice_preparation as prep
        entered = asyncio.Event()
        release = asyncio.Event()
        report = json.dumps({'schema_version': 2, 'input_seconds': 3, 'segments': [
            {'start_sec': 0, 'end_sec': 3, 'score': .7, 'periodicity': .7, 'level_db': -16,
             'peak': .5, 'clipped_fraction': 0, 'accepted': True, 'reasons': []}]})

        async def ffmpeg(arguments: list[str], slot: object, cancelled: object, timeout: int = 1800) -> str:
            Path(arguments[-1]).write_bytes(b'completed sample')
            return ''

        async def cleanup(proc: object) -> None:
            entered.set()
            await release.wait()

        with (patch.object(voice, '_probe_duration', return_value=3), patch.object(voice, '_ffmpeg', side_effect=ffmpeg),
            patch.object(voice, '_spawn', return_value=0), patch.object(voice, '_engine_python', return_value=Path(sys.executable)),
            patch('app.orchestrator.process.tail_log', return_value=report), patch.object(prep, '_measure_selected'),
            patch.object(voice, 'kill_proc', side_effect=cleanup)):
            prep.start(self.path, PrepareVoiceRequest(singer_confirmed=True,
                sources=[VoiceSourceSelection(filename='voice.wav', kind='vocal')]))
            await entered.wait()
            completed = prep.load(self.path)
            self.assertEqual(completed.response.status, 'done')
            self.assertIsNotNone(completed.response.progress)
            sample_bytes = {identifier: (self.path / files.original).read_bytes() for identifier, files in completed.samples.items()}
            cancellation = asyncio.create_task(prep.cancel(self.path))
            await asyncio.sleep(0)
            self.assertFalse(cancellation.done())
            release.set()
            result = await cancellation

        self.assertEqual(result.model_dump(), completed.response.model_dump())
        self.assertEqual(result.error_code, '')
        self.assertEqual(prep.load(self.path).model_dump(), completed.model_dump())
        self.assertTrue(sample_bytes)
        for identifier, expected in sample_bytes.items():
            self.assertEqual((self.path / completed.samples[identifier].original).read_bytes(), expected)
        self.assertFalse(prep.busy())

    async def test_draining_cancellation_does_not_cancel_a_replacement_job(self) -> None:
        from app import voice_preparation as prep
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked(path: Path, document: prep.PreparationDocument, job: prep.PreparationJob) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                if prep._jobs.get(path.resolve()) is job:
                    prep._jobs.pop(path.resolve())

        async def cleanup(proc: object) -> None:
            entered.set()
            await release.wait()

        options = PrepareVoiceRequest(singer_confirmed=True)
        with patch.object(prep, '_run', side_effect=blocked), patch.object(voice, 'kill_proc', side_effect=cleanup):
            prep.start(self.path, options)
            cancellation = asyncio.create_task(prep.cancel(self.path))
            await entered.wait()
            replacement = prep.start(self.path, options)
            replacement_job = prep._jobs[self.path.resolve()]
            release.set()
            result = await cancellation
            try:
                self.assertIs(prep._jobs.get(self.path.resolve()), replacement_job)
                self.assertEqual(result.revision, replacement.revision)
                self.assertEqual(result.status, 'queued')
                self.assertFalse(replacement_job.cancelled)
            finally:
                await prep.cancel(self.path)

    async def test_interrupted_preparation_preserves_start_and_freezes_timing(self) -> None:
        from app import voice_preparation as prep
        from app.voice_progress import VoiceProgressTracker
        tracker = VoiceProgressTracker.create('preparation', 'review', now=10)
        tracker.start(now=11)
        prep.save(self.path, prep.PreparationDocument(response=prep.VoicePreparationResponse(status='running', progress=tracker.progress)))
        response = prep.get(self.path)
        self.assertEqual(response.error_code, 'interrupted')
        self.assertIsNotNone(response.progress)
        if response.progress is not None:
            self.assertEqual(response.progress.started_at, 11)
            finished = response.progress.finished_at
            self.assertIsNotNone(finished)
            self.assertEqual(finished, 11)
            reloaded = prep.get(self.path).progress
            self.assertIsNotNone(reloaded)
            if reloaded is not None:
                self.assertEqual(reloaded.finished_at, finished)

    async def test_current_file_does_not_count_as_completed_source(self) -> None:
        from app import voice_preparation as prep
        entered = asyncio.Event()
        async def separator(*args: object, **kwargs: object) -> Path:
            entered.set()
            await asyncio.Event().wait()
            return self.path / 'unused.wav'
        with patch('app.voice_separation.separate_vocal', side_effect=separator), patch.object(voice, '_probe_duration', return_value=5), patch.object(voice, '_engine_python', return_value=Path(sys.executable)):
            prep.start(self.path, PrepareVoiceRequest(singer_confirmed=True))
            await entered.wait()
            progress = prep.get(self.path).progress
            self.assertIsNotNone(progress)
            if progress is not None:
                self.assertEqual(progress.phase, 'separating')
                self.assertEqual(progress.current_file, 'voice.wav')
                self.assertEqual(progress.files_completed, 0)
                self.assertEqual(progress.phase_current, 0)
            await prep.cancel(self.path)

    def test_interrupted_build_freezes_at_last_persisted_observation(self) -> None:
        from app.voice_progress import VoiceProgressTracker
        tracker = VoiceProgressTracker.create('build', 'review', now=10)
        tracker.start(now=11)
        tracker.phase('training', total=200, unit='steps', now=12)
        tracker.advance(2, now=15)
        metadata = voice.read_meta(self.path)
        metadata.update({'status': 'training', 'job_progress': tracker.progress.model_dump(mode='json')})
        voice.write_meta(self.path, metadata)
        response = voice.public_voice(self.voice_id)
        self.assertEqual(response['error_code'], 'interrupted')
        self.assertEqual(response['job_progress']['finished_at'], 15)
        self.assertEqual(voice.public_voice(self.voice_id)['job_progress']['finished_at'], 15)

    def test_training_log_drives_measured_phase_estimate_and_persists_it(self) -> None:
        from app.voice_progress import VoiceProgressTracker
        timing = VoiceProgressTracker.create('build', 'review', now=10)
        timing.start(now=11)
        timing.phase('training', total=200, unit='steps', now=12)
        job = voice.BuildJob(voice_id=self.voice_id, status='training', stage='training', progress_total=200, log_name='timed', timing=timing)
        log = self.root / 'timed.log'
        with patch.object(voice, 'LOG_DIR', self.root):
            for step, stamp in [(1, 100), (2, 102), (3, 104)]:
                log.write_text(f'step {step}, loss: 0.5\n', encoding='utf-8')
                with patch('app.voice_progress.time.time', return_value=stamp):
                    voice._pull_train_log(job)
                if step < 3:
                    self.assertIsNone(timing.progress.estimated_phase_remaining_sec)
        reloaded = voice.read_meta(self.path)['job_progress']
        self.assertIsInstance(reloaded, dict)
        if isinstance(reloaded, dict):
            self.assertEqual(reloaded['phase_current'], 3)
            self.assertEqual(reloaded['estimated_phase_remaining_sec'], 394)

    async def test_training_wait_exposes_gpu_queue_before_completed_steps(self) -> None:
        self.prepared()
        entered = asyncio.Event()
        release = asyncio.Event()
        class Gate:
            async def __aenter__(self) -> None:
                entered.set()
                await release.wait()
            async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None
        async def merge(paths: object, destination: Path, slot: object, cancelled: object) -> None:
            destination.write_bytes(b'preview')
        with patch.object(voice, 'gpu_lock', new=Gate()), patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=200)
            await entered.wait()
            view = voice.public_voice(self.voice_id)['job_progress']
            self.assertEqual(view['phase'], 'waiting_gpu')
            self.assertEqual(view['phase_current'], 0)
            self.assertEqual(view['phase_total'], 0)
            self.assertIsNone(view['estimated_phase_remaining_sec'])
            await voice.cancel_build(self.voice_id)

    async def test_stale_selection_does_not_change_manifest(self) -> None:
        from app import voice_preparation as prep
        document = prep.PreparationDocument(response=prep.VoicePreparationResponse(revision='new', status='done'))
        prep.save(self.path, document)
        with self.assertRaises(HTTPException) as raised:
            prep.select(self.path, SelectVoiceSamplesRequest(revision='old', segment_ids=['one'], reference_id='ref'))
        self.assertEqual(raised.exception.detail, 'stale_preparation')
        self.assertEqual(prep.get(self.path).revision, 'new')

    async def test_rejected_sample_cannot_be_selected(self) -> None:
        from app import voice_preparation as prep
        segment = VoiceSegment(id='one', source_filename='voice.wav', start_sec=0, end_sec=3, duration_sec=3, score=.1, periodicity=.1, level_db=-16, peak=.5, clipped_fraction=0, accepted=False, reasons=['low_periodicity'])
        prep.save(self.path, prep.PreparationDocument(response=prep.VoicePreparationResponse(revision='current', status='failed', segments=[segment])))
        with self.assertRaises(HTTPException) as raised:
            prep.select(self.path, SelectVoiceSamplesRequest(revision='current', segment_ids=['one'], reference_id='ref'))
        self.assertEqual(raised.exception.detail, 'invalid_selection')

    async def test_artifact_resolver_rejects_symlink_escape(self) -> None:
        from app.voice_artifacts import contained_file
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'private')
        (self.path / 'escape.wav').symlink_to(outside)
        with self.assertRaises(HTTPException):
            contained_file(self.path, 'escape.wav')

    async def test_build_requires_reviewed_preparation(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            voice.start_build(self.voice_id)
        self.assertEqual(raised.exception.detail, 'preparation_required')

    async def test_published_base_voice_is_usable_without_trained_checkpoint(self) -> None:
        from app.voice_artifacts import StoredModel, publish
        (self.path / 'reference.wav').write_bytes(b'reference')
        (self.path / 'voice.wav').write_bytes(b'preview')
        publish(self.path, [StoredModel(id='base', steps=0, kind='base', reference='reference.wav', preview='voice.wav')], 'base')
        self.assertTrue(voice.public_voice(self.voice_id)['usable'])
        self.assertEqual(voice.public_voice(self.voice_id)['active_model_id'], 'base')

    async def test_legacy_model_and_reference_resolve_without_migration(self) -> None:
        for name in ('model.pth', 'config.yml', 'reference.wav'):
            (self.path / name).write_bytes(b'legacy')
        metadata = voice.read_meta(self.path)
        metadata.update({'checkpoint': str(self.path / 'model.pth'), 'config': str(self.path / 'config.yml'), 'reference': str(self.path / 'reference.wav'), 'trained_steps': 200})
        voice.write_meta(self.path, metadata)
        artifact = voice.resolve_model_artifact(self.path, 'trained')
        self.assertEqual(artifact.checkpoint, self.path / 'model.pth')
        self.assertEqual(voice.resolve_reference_artifact(self.path, 'published'), self.path / 'reference.wav')

    def prepared(self, singer_confirmed: bool = True):
        from app import voice_preparation as prep
        from app.voice_contracts import VoiceReferenceCandidate
        segment = VoiceSegment(id='segment', source_filename='voice.wav', start_sec=2, end_sec=5, duration_sec=3, score=.7, periodicity=.7, level_db=-16, peak=.5, clipped_fraction=0, accepted=True, reasons=[])
        reference = VoiceReferenceCandidate(id='ref', segment_id='segment', source_filename='voice.wav', start_sec=2, end_sec=5, duration_sec=3, score=.7)
        (self.path / 'sample.wav').write_bytes(b'sample')
        document = prep.PreparationDocument(response=prep.VoicePreparationResponse(revision='reviewed', status='done', options=PrepareVoiceRequest(singer_confirmed=singer_confirmed),
            segments=[segment], references=[reference], selected_segment_ids=['segment'], reference_id='ref', accepted_seconds=3),
            generation='generation', fingerprints=prep.fingerprints(self.path), samples={'segment': prep.SampleFiles(original='sample.wav')}, references={'ref': prep.SampleFiles(original='sample.wav')})
        prep.save(self.path, document)
        async def mono(source, destination, slot):
            destination.write_bytes(source.read_bytes())
        mono_patch = patch.object(voice, 'phase_safe_mono', side_effect=mono)
        mono_patch.start()
        self.addCleanup(mono_patch.stop)
        engine = self.path / 'engine'
        preset = engine / voice.SINGING_CONFIG
        preset.parent.mkdir(parents=True, exist_ok=True)
        preset.write_text('log_dir: "./runs"\npretrained_model: "baseline.pth"\n', encoding='utf-8')
        async def base(directory, job, filename):
            result = directory / 'base.pth'
            result.write_bytes(b'baseline')
            return result
        for dependency in (patch.object(voice, 'SEED_VC_DIR', engine), patch.object(voice, '_engine_python', return_value=Path(sys.executable)),
            patch.object(voice, '_ensure_pretrained_base', side_effect=base), patch('app.seed_vc_compat.ensure_compatibility'),
            patch.object(voice, 'ensure_whisper_float32_off_cuda')):
            dependency.start()
            self.addCleanup(dependency.stop)
        return document

    async def test_source_change_invalidates_prepared_build(self) -> None:
        from app import voice_preparation as prep
        self.prepared()
        (self.path / 'recordings' / 'voice.wav').write_bytes(b'changed source')
        with self.assertRaises(HTTPException) as raised:
            prep.validated(self.path, 'reviewed')
        self.assertEqual(raised.exception.detail, 'source_changed')

    async def test_uploaded_source_invalidates_saved_review_on_reload(self) -> None:
        import io
        from fastapi import UploadFile
        from app import voice_preparation as prep
        from app.api.routes_voices import upload_recordings
        self.prepared()
        await upload_recordings(self.voice_id, [UploadFile(io.BytesIO(b'new recording'), filename='voice.wav')])
        response = prep.get(self.path)
        self.assertEqual(response.status, 'failed')
        self.assertEqual(response.error_code, 'source_changed')
        self.assertEqual(response.revision, 'reviewed')
        self.assertEqual((self.path / 'recordings' / 'voice.wav').read_bytes(), b'original')
        self.assertEqual((self.path / 'sample.wav').read_bytes(), b'sample')
        with self.assertRaises(HTTPException) as raised:
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
        self.assertEqual(raised.exception.detail, 'source_changed')

    async def test_singer_confirmation_is_required_before_build(self) -> None:
        self.prepared(singer_confirmed=False)
        with self.assertRaises(HTTPException) as raised:
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
        self.assertEqual(raised.exception.detail, 'singer_unconfirmed')

    async def test_reference_only_build_publishes_immutable_samples(self) -> None:
        self.prepared()
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        with patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        view = voice.public_voice(self.voice_id)
        self.assertEqual(view['status'], 'ready')
        self.assertEqual(view['job_progress']['status'], 'done')
        self.assertEqual(view['job_progress']['preparation_revision'], 'reviewed')
        self.assertIsNotNone(view['job_progress']['started_at'])
        self.assertIsNotNone(view['job_progress']['finished_at'])
        self.assertTrue(view['usable'])
        self.assertEqual(view['models'][0]['kind'], 'base')
        reference = voice.resolve_reference_artifact(self.path, 'published')
        self.assertIn('builds', reference.parts)
        self.assertEqual(reference.read_bytes(), b'sample')

    async def test_published_build_preserves_review_provenance_after_new_preparation(self) -> None:
        from app import voice_preparation as prep
        document = self.prepared()
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        with patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        directory = voice.resolve_reference_artifact(self.path, 'published').parent
        self.assertTrue((directory / 'preparation.json').is_file())
        archived = (directory / 'preparation.json').read_bytes()
        replacement = document.model_copy(deep=True)
        replacement.generation = 'new-preparation'
        replacement.response.revision = 'new-revision'
        replacement.response.segments[0].start_sec = 100
        replacement.response.segments[0].end_sec = 103
        prep.save(self.path, replacement)
        snapshot = prep.load(directory)
        self.assertEqual(snapshot.response.revision, 'reviewed')
        self.assertEqual(snapshot.response.selected_segment_ids, ['segment'])
        self.assertEqual(snapshot.response.segments[0].source_filename, 'voice.wav')
        self.assertEqual(snapshot.response.segments[0].start_sec, 2)
        self.assertEqual(snapshot.fingerprints, document.fingerprints)
        self.assertEqual((directory / 'preparation.json').read_bytes(), archived)

    async def test_failed_build_preserves_previous_registry_and_reference(self) -> None:
        from app.voice_artifacts import StoredModel, publish
        self.prepared()
        (self.path / 'published.wav').write_bytes(b'old voice')
        publish(self.path, [StoredModel(id='old', steps=0, kind='base', reference='published.wav', preview='published.wav')], 'old')
        original = (self.path / 'artifacts.json').read_bytes()
        with patch.object(voice, 'merge_vocals', side_effect=OSError('private path')), self.assertLogs('app.voice_build', level='ERROR'):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        self.assertEqual((self.path / 'artifacts.json').read_bytes(), original)
        self.assertEqual(voice.resolve_reference_artifact(self.path, 'published').read_bytes(), b'old voice')
        self.assertEqual(list((self.path / 'builds').iterdir()), [])

    async def test_remix_limiter_preserves_duration_and_caps_master_peak(self) -> None:
        import numpy as np
        import soundfile as sf
        sample_rate = 44100
        time = np.arange(sample_rate * 2) / sample_rate
        waveform = .8 * np.sin(2 * np.pi * 220 * time)
        files = [self.path / f'stem{index}.wav' for index in range(4)]
        for file in files:
            sf.write(file, waveform, sample_rate, subtype='FLOAT')
        output = self.path / 'mix.wav'
        arguments = ['-y']
        for file in files:
            arguments.extend(['-i', str(file)])
        arguments.extend(['-filter_complex', voice.remix_filter(4), '-map', '[out]', '-c:a', 'pcm_f32le', str(output)])
        await voice._ffmpeg(arguments, voice.ProcSlot(), lambda: False)
        audio, actual_rate = sf.read(output)
        self.assertLessEqual(float(np.max(np.abs(audio))), .991)
        self.assertAlmostEqual(len(audio) / actual_rate, 2, places=3)

    def test_vocal_gain_is_bounded(self) -> None:
        self.assertEqual(voice.vocal_gain(-16, -40), 4)
        self.assertEqual(voice.vocal_gain(-40, -16), .25)
        self.assertEqual(voice.vocal_gain(-16, -16), 1)

    async def test_resume_and_comparison_are_explicitly_incompatible(self) -> None:
        self.prepared()
        with self.assertRaises(HTTPException) as raised:
            voice.start_build(self.voice_id, preparation_revision='reviewed', resume=True, compare_checkpoints=True)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail, 'incompatible_build_options')

    async def test_build_does_not_publish_a_changed_selection(self) -> None:
        from app import voice_preparation as prep
        self.prepared()
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
            prep.select(self.path, SelectVoiceSamplesRequest(revision='reviewed', segment_ids=['segment'], reference_id='ref'))
        with patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        self.assertFalse((self.path / 'artifacts.json').exists())
        self.assertEqual(voice.public_voice(self.voice_id)['error_code'], 'stale_preparation')

    async def test_preparation_continues_after_one_invalid_source(self) -> None:
        from app import voice_preparation as prep
        (self.path / 'recordings' / 'broken.wav').write_bytes(b'broken')
        metadata = voice.read_meta(self.path)
        metadata['recordings'] = [{'filename': 'broken.wav', 'bytes': 6}, {'filename': 'voice.wav', 'bytes': 8}]
        voice.write_meta(self.path, metadata)
        options = PrepareVoiceRequest(singer_confirmed=True, sources=[VoiceSourceSelection(filename='broken.wav', kind='vocal'), VoiceSourceSelection(filename='voice.wav', kind='vocal')])
        report = json.dumps({'schema_version': 2, 'input_seconds': 3, 'segments': [{'start_sec': 0, 'end_sec': 3, 'score': .7, 'periodicity': .7, 'level_db': -16, 'peak': .5, 'clipped_fraction': 0, 'accepted': True, 'reasons': []}]})
        async def probe(path):
            return 0 if path.name == 'broken.wav' else 3
        async def ffmpeg(arguments, slot, cancelled, timeout=1800):
            Path(arguments[-1]).write_bytes(b'sample')
            return ''
        async def spawn(command, **kwargs):
            return 0
        with (patch.object(voice, '_probe_duration', side_effect=probe), patch.object(voice, '_ffmpeg', side_effect=ffmpeg),
            patch.object(voice, '_spawn', side_effect=spawn), patch.object(voice, '_engine_python', return_value=Path(sys.executable)),
            patch('app.orchestrator.process.tail_log', return_value=report), patch('app.voice_separation.separate_vocal') as separate):
            prep.start(self.path, options)
            await prep._jobs[self.path.resolve()].task
        response = prep.get(self.path)
        self.assertEqual(response.status, 'done')
        self.assertEqual(response.sources[0].error_code, 'invalid_audio')
        self.assertEqual(response.sources[1].accepted_sec, 3)
        separate.assert_not_called()

    async def test_rebuild_keeps_legacy_model_as_selectable_choice(self) -> None:
        self.prepared()
        for name in ('model.pth', 'config.yml', 'reference.wav', 'voice.wav'):
            (self.path / name).write_bytes(b'legacy')
        metadata = voice.read_meta(self.path)
        metadata.update({'checkpoint': str(self.path / 'model.pth'), 'config': str(self.path / 'config.yml'), 'reference': str(self.path / 'reference.wav'), 'trained_steps': 200})
        voice.write_meta(self.path, metadata)
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'new')
        with patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        choices = voice.public_voice(self.voice_id)['models']
        self.assertIn('trained', [item['id'] for item in choices])
        voice.select_model(self.voice_id, 'trained')
        self.assertEqual(voice.resolve_reference_artifact(self.path, 'published').read_bytes(), b'legacy')

    async def test_phase_safe_mono_keeps_original_and_preserves_antiphase_signal(self) -> None:
        import numpy as np
        import soundfile as sf
        sample_rate = 16000
        time = np.arange(sample_rate) / sample_rate
        tone = .3 * np.sin(2 * np.pi * 220 * time)
        original = self.path / 'antiphase.wav'
        destination = self.path / 'mono.wav'
        sf.write(original, np.column_stack([tone, -tone]), sample_rate, subtype='FLOAT')
        before = original.read_bytes()
        async def execute(command, **kwargs):
            process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await process.communicate()
            return process.returncode
        with patch.object(voice, '_engine_python', return_value=Path(sys.executable)), patch.object(voice, '_spawn', side_effect=execute):
            await voice.phase_safe_mono(original, destination, voice.ProcSlot())
        converted, actual_rate = sf.read(destination)
        self.assertEqual(converted.ndim, 1)
        self.assertEqual(actual_rate, sample_rate)
        self.assertGreater(float(np.sqrt(np.mean(converted ** 2))), .2)
        self.assertEqual(original.read_bytes(), before)

    async def test_reference_only_uses_same_explicit_base_as_training(self) -> None:
        self.prepared()
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        with patch.object(voice, 'merge_vocals', side_effect=merge):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=0)
            await voice._builds[self.voice_id].task
        model_id = voice.public_voice(self.voice_id)['active_model_id']
        artifact = voice.resolve_model_artifact(self.path, model_id)
        self.assertIsNotNone(artifact.checkpoint)
        self.assertIsNotNone(artifact.config)

    async def test_fresh_comparison_build_retains_each_requested_checkpoint(self) -> None:
        self.prepared()
        commands = []
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        async def train(command, **kwargs):
            commands.append(command)
            directory = Path(command[command.index('--config') + 1]).parent / 'runs' / 'model'
            directory.mkdir(parents=True)
            for filename in ('ft_model.pth', 'resume.pth', 'step_200.pth', 'step_500.pth', 'step_1000.pth'):
                (directory / filename).write_bytes(b'complete checkpoint')
            return 0
        with patch.object(voice, 'merge_vocals', side_effect=merge), patch.object(voice, '_spawn', side_effect=train):
            voice.start_build(self.voice_id, preparation_revision='reviewed', compare_checkpoints=True)
            await voice._builds[self.voice_id].task
        view = voice.public_voice(self.voice_id)
        self.assertEqual(view['status'], 'ready')
        self.assertEqual([row['steps'] for row in view['models']], [0, 200, 500, 1000])
        self.assertEqual(commands[0][commands[0].index('--max-steps')+1], '1000')
        self.assertIn('--pretrained-ckpt', commands[0])
        self.assertNotIn('--resume', commands[0])
        for model in view['models']:
            self.assertIsNotNone(voice.resolve_model_artifact(self.path, model['id']).checkpoint)

    async def test_explicit_resume_copies_full_state_to_a_new_run(self) -> None:
        self.prepared()
        commands = []
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        async def train(command, **kwargs):
            commands.append(command)
            run = Path(command[command.index('--config')+1]).parent / 'runs' / 'model'
            run.mkdir(parents=True, exist_ok=True)
            if '--resume' in command:
                self.assertEqual((run / 'resume.pth').read_bytes(), b'full state 200')
                self.assertNotIn('--pretrained-ckpt', command)
            step = command[command.index('--max-steps')+1]
            (run / 'ft_model.pth').write_bytes(f'params {step}'.encode())
            (run / 'resume.pth').write_bytes(f'full state {step}'.encode())
            return 0
        with patch.object(voice, 'merge_vocals', side_effect=merge), patch.object(voice, '_spawn', side_effect=train):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=200)
            await voice._builds[self.voice_id].task
            first_reference = voice.resolve_reference_artifact(self.path, 'published')
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=500, resume=True)
            await voice._builds[self.voice_id].task
        self.assertIn('--resume', commands[1])
        self.assertNotEqual(commands[0][commands[0].index('--config')+1], commands[1][commands[1].index('--config')+1])
        self.assertTrue(first_reference.is_file())
        self.assertEqual(voice.public_voice(self.voice_id)['trained_steps'], 500)

    async def test_resume_is_rejected_before_work_without_full_state(self) -> None:
        self.prepared()
        with self.assertRaises(HTTPException) as raised:
            voice.start_build(self.voice_id, preparation_revision='reviewed', resume=True)
        self.assertEqual(raised.exception.detail, 'resume_unavailable')

    async def test_resume_rejects_finished_target_before_scheduling_work(self) -> None:
        from app import voice_preparation as prep
        from app.voice_artifacts import StoredModel, publish
        document = self.prepared()
        for name in ('checkpoint.pth', 'config.yml', 'resume.pth'):
            (self.path / name).write_bytes(b'published')
        publish(self.path, [StoredModel(id='step500', steps=500, kind='trained',
            checkpoint='checkpoint.pth', config='config.yml', resume_path='resume.pth',
            reference='sample.wav', preview='sample.wav', preparation_revision='reviewed',
            selection_fingerprint=prep.selection_fingerprint(document))], 'step500')
        for target in (200, 500):
            with self.subTest(target=target):
                with self.assertRaises(HTTPException) as raised:
                    voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=target, resume=True)
                self.assertEqual(raised.exception.detail, 'resume_mismatch')
        self.assertNotIn(self.voice_id, voice._builds)
        self.assertFalse((self.path / 'builds').exists())

    async def test_recording_upload_rejects_directory_symlink_escape(self) -> None:
        import io
        import shutil
        from fastapi import UploadFile
        from app.api.routes_voices import upload_recordings
        outside = self.root / 'outside'
        outside.mkdir()
        shutil.rmtree(self.path / 'recordings')
        (self.path / 'recordings').symlink_to(outside, target_is_directory=True)
        original = (self.path / 'voice.json').read_bytes()
        with self.assertRaises(HTTPException) as raised:
            await upload_recordings(self.voice_id, [UploadFile(io.BytesIO(b'upload'), filename='new.wav')])
        self.assertEqual(raised.exception.detail, 'invalid_source')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.path / 'voice.json').read_bytes(), original)

    async def test_parent_shutdown_and_release_cancel_queued_preparation_before_it_starts(self) -> None:
        from app import voice_preparation as prep
        started = []
        async def blocked(path, document, job):
            started.append(path)
            await asyncio.Event().wait()
        with patch.object(prep, '_run', side_effect=blocked):
            prep.start(self.path, PrepareVoiceRequest())
            await voice.shutdown()
            self.assertEqual(started, [])
            self.assertFalse(prep.busy())
            prep.start(self.path, PrepareVoiceRequest())
            await voice.release_voice(self.voice_id)
            self.assertEqual(started, [])
            self.assertFalse(prep.busy())

    async def test_cleanup_failure_prevents_voice_deletion_after_drain(self) -> None:
        from app import voice_preparation as prep
        from app.api.routes_voices import delete_voice
        async def fail(path):
            raise OSError('could not terminate owned process')
        with patch.object(prep, 'cancel', side_effect=fail), self.assertLogs('app.voice_build', level='ERROR'):
            with self.assertRaises(HTTPException) as raised:
                await delete_voice(self.voice_id)
        self.assertEqual(raised.exception.detail, 'voice_cleanup_failed')
        self.assertTrue(self.path.is_dir())

    async def test_failed_preparation_publication_releases_busy_registry(self) -> None:
        from app import voice_preparation as prep
        with patch.object(prep, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                prep.start(self.path, PrepareVoiceRequest())
        self.assertNotIn(self.path.resolve(), prep._jobs)

    async def test_failed_preparation_directory_setup_releases_busy_registry(self) -> None:
        from app import voice_preparation as prep
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if 'preparations' in path.parts:
                raise OSError('disk full')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            prep.start(self.path, PrepareVoiceRequest())
            task = prep._jobs[self.path.resolve()].task
            await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(self.path.resolve(), prep._jobs)
        self.assertEqual(prep.get(self.path).status, 'failed')

    async def test_resume_rejects_changed_weights_under_same_base_filename(self) -> None:
        self.prepared()
        async def merge(paths, destination, slot, cancelled):
            destination.write_bytes(b'preview')
        async def train(command, **kwargs):
            run = Path(command[command.index('--config')+1]).parent / 'runs' / 'model'
            run.mkdir(parents=True, exist_ok=True)
            (run / 'ft_model.pth').write_bytes(b'params')
            (run / 'resume.pth').write_bytes(b'full state')
            return 0
        async def changed_base(directory, job, filename):
            result = directory / 'base.pth'
            result.write_bytes(b'different weights, same filename')
            return result
        with patch.object(voice, 'merge_vocals', side_effect=merge), patch.object(voice, '_spawn', side_effect=train):
            voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=200)
            await voice._builds[self.voice_id].task
            original = (self.path / 'artifacts.json').read_bytes()
            with patch.object(voice, '_ensure_pretrained_base', side_effect=changed_base):
                voice.start_build(self.voice_id, preparation_revision='reviewed', training_steps=500, resume=True)
                await voice._builds[self.voice_id].task
        self.assertEqual(voice.public_voice(self.voice_id)['error_code'], 'resume_mismatch')
        self.assertEqual((self.path / 'artifacts.json').read_bytes(), original)

    def test_corrupted_registry_returns_stable_error(self) -> None:
        (self.path / 'artifacts.json').write_text('{', encoding='utf-8')
        with self.assertRaises(HTTPException) as raised:
            voice.public_voice(self.voice_id)
        self.assertEqual(raised.exception.detail, 'invalid_voice_metadata')

    def test_cpu_downloader_uses_inspected_repo_and_cache_without_models(self) -> None:
        import os
        from types import SimpleNamespace
        from app import voice_download
        cached = self.path / 'cached.pth'
        cached.write_bytes(b'baseline parameters')
        calls = []
        def fetch(*, repo_id: str, filename: str, cache_dir: str) -> str:
            calls.append((repo_id, filename, cache_dir))
            return str(cached)
        destination = self.path / 'downloaded.pth'
        with patch.object(voice_download.importlib, 'import_module', return_value=SimpleNamespace(hf_hub_download=fetch)), patch.dict(os.environ, {'HF_HUB_CACHE': '/isolated/cache'}):
            voice_download.download('baseline.pth', destination)
        self.assertEqual(calls, [('Plachta/Seed-VC', 'baseline.pth', '/isolated/cache')])
        self.assertEqual(destination.read_bytes(), b'baseline parameters')
        self.assertEqual(cached.read_bytes(), b'baseline parameters')

    def test_downloader_rejects_filename_traversal_before_loading_provider(self) -> None:
        from app import voice_download
        with patch.object(voice_download.importlib, 'import_module') as provider:
            with self.assertRaises(ValueError):
                voice_download.download('../private.pth', self.path / 'destination.pth')
        provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()
