"""One revoked retained recording must not prevent backend startup."""
from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch

from app.audiobook_workflows_test import _WorkflowFixture
from app import video_projects as store, video_render as render, voice_profiles
from app.reading_media_contracts import RetainedAudioIdentity, RetainedAudioSource
from app.video_contracts import CreateVideoProjectRequest, VideoProjectJob, VideoSpeechClip
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest


class RetainedVideoRecoveryTests(_WorkflowFixture):
    def test_revoked_consent_fails_one_pending_export_and_continues_recovery(self) -> None:
        with patch.object(store, 'DATA_DIR', self.root / 'library'), patch.object(render, '_tasks', {}):
            for index in range(2):
                project = store._picture_project(CreateVideoProjectRequest(name=f'Recovery {index}', duration_sec=2))
                clip = store.artifact(project.id, 'speech/clip.wav')
                clip.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / 'reference.wav', clip)
                digest = store.file_hash(clip)
                project.speech_clip = VideoSpeechClip(id='c' * 32, name='clip', bytes=clip.stat().st_size,
                    duration_sec=.1, sha256=digest, kind='voice', voice_profile_id=self.profile.id)
                project.job = VideoProjectJob(id='e' * 32, operation='export', status='running', shot_ids=[], shot_count=0)
                output = store.artifact(project.id, 'exports/clip.mp4')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b'verified codec boundary fixture')
                pending = store.PendingExport(path='exports/clip.mp4', duration_sec=2, width=704, height=448,
                    fingerprint='test', output_sha256=store.file_hash(output), project_revision=project.revision)
                identity = RetainedAudioIdentity(source=RetainedAudioSource(kind='speech_trial', source_id='a' * 32),
                    source_sha256=digest, clip_sha256=digest, clip_start_ms=0, clip_end_ms=100, profile_ids=[self.profile.id])
                store.save(store.StoredVideoProject(project=project, speech_path='speech/clip.wav',
                    retained_audio=identity, pending_export=pending))
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            with patch('app.video_dialogue.recover'), patch.object(render, 'validate_media'):
                asyncio.run(render.recover())
            rows = store.list_projects()
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertIsNotNone(row.job)
                if row.job is None:
                    self.fail('Missing recoverable job')
                self.assertEqual(row.job.status, 'failed')
                self.assertEqual(row.job.error_code, 'consent_required')
                self.assertEqual(row.file_url, '')
                self.assertTrue(store.artifact(row.id, 'speech/clip.wav').is_file())
