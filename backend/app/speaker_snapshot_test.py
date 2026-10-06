"""Accepted snapshots stay read-only and reject stale or revoked sources."""
from __future__ import annotations
from app.audiobook_workflows_test import _WorkflowFixture
from app import audiobook_workflows as workflows,audiobooks,voice_profiles
from app.audiobook_contracts import CreateAudiobookAuditionRequest,AudiobookChapterInput
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest


class SpeakerSnapshotTests(_WorkflowFixture):
    def test_completed_passage_snapshot_checks_revision_and_live_consent(self)->None:
        book=self.create()
        rows=workflows.get_passages(book,0)
        snapshot=workflows.passage_render_snapshot(book,0,rows.passages[0].id,rows.revision)
        self.assertEqual(snapshot.profile_id,self.profile.id)
        with self.assertRaises(audiobooks.AudiobookError):
            workflows.passage_render_snapshot(book,0,rows.passages[0].id,rows.revision+1)
        voice_profiles.patch_profile(self.profile.id,PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        with self.assertRaises(audiobooks.AudiobookError) as caught:
            workflows.passage_render_snapshot(book,0,rows.passages[0].id,rows.revision)
        self.assertEqual(caught.exception.code,"consent_required")

    def test_completed_audition_clip_snapshot_does_not_generate_or_change_it(self)->None:
        preview=workflows.start_audition(CreateAudiobookAuditionRequest(title="Preview",profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text="A representative line.")]))
        snapshot=workflows.audition_clip_snapshot(preview.id,0)
        self.assertEqual(snapshot.profile_id,self.profile.id)
        self.assertEqual(workflows.get_audition(preview.id),preview)
        with self.assertRaises(audiobooks.AudiobookError):workflows.audition_clip_snapshot(preview.id,17)
