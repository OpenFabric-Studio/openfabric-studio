"""Media-aware history survives reload and never rewrites completed artifacts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import video_projects as store
from app.video_contracts import CreateVideoProjectRequest, UpdateVideoProjectRequest, VideoProjectShot, VideoVariant, VideoRevisionRequest, VideoSpeechClip


class VideoHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, "DATA_DIR", self.root))
        self.project = await store.create(CreateVideoProjectRequest(duration_sec=4))

    async def test_undo_reload_restores_removed_approved_variant_and_redo(self) -> None:
        document = store.load(self.project.id)
        shot = VideoProjectShot(id="a" * 32, start_sec=0, seconds=4, prompt="Original", approved_variant_id="b" * 32,
            variants=[VideoVariant(id="b" * 32, seed=0, status="ready", created_at="", file_url="/old-clip")])
        document.project.shots = [shot]
        clip = store.artifact(self.project.id, f"shots/{shot.id}/{'b' * 32}.mp4")
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"immutable old media")
        store.save(document)
        edited = store.update(self.project.id, UpdateVideoProjectRequest(revision=1, shots=[]))
        self.assertTrue(store.get(self.project.id).undo_available)
        restored = store.undo(self.project.id, VideoRevisionRequest(revision=edited.revision))
        self.assertEqual(restored.shots[0].approved_variant_id, "b" * 32)
        self.assertEqual(store.get(self.project.id).shots[0].variants, [shot.variants[0]])
        self.assertTrue(restored.redo_available)
        self.assertEqual(clip.read_bytes(), b"immutable old media")
        redone = store.redo(self.project.id, VideoRevisionRequest(revision=restored.revision))
        self.assertEqual(redone.shots, [])
        self.assertGreater(redone.revision, restored.revision)

    async def test_clearing_speech_retains_history_media_and_undo(self) -> None:
        document = store.load(self.project.id)
        document.speech_path = "speech/original.wav"
        document.project.speech_clip = VideoSpeechClip(id="c" * 32, name="Line", bytes=4, duration_sec=1, sha256="d" * 64)
        speech = store.artifact(self.project.id, document.speech_path)
        speech.parent.mkdir()
        speech.write_bytes(b"PCM!")
        store.save(document)
        cleared = store.clear_speech(self.project.id, VideoRevisionRequest(revision=1))
        self.assertTrue(speech.exists())
        restored = store.undo(self.project.id, VideoRevisionRequest(revision=cleared.revision))
        self.assertIsNotNone(restored.speech_clip)
        self.assertEqual(store.speech_file(self.project.id), speech)

    async def test_new_edit_after_undo_clears_redo_and_stale_revision_cannot_restore(self) -> None:
        saved = store.update(self.project.id, UpdateVideoProjectRequest(revision=1, name="First"))
        restored = store.undo(self.project.id, VideoRevisionRequest(revision=saved.revision))
        changed = store.update(self.project.id, UpdateVideoProjectRequest(revision=restored.revision, name="Branch"))
        self.assertFalse(changed.redo_available)
        with self.assertRaises(store.VideoProjectError) as failure:
            store.undo(self.project.id, VideoRevisionRequest(revision=1))
        self.assertEqual(failure.exception.code, "revision_conflict")
        self.assertEqual(store.get(self.project.id).name, "Branch")

    async def test_duplicate_starts_independent_history_and_cannot_restore_into_original(self) -> None:
        edited = store.update(self.project.id, UpdateVideoProjectRequest(revision=1, name='Edited original'))
        duplicate = store.duplicate(edited.id, VideoRevisionRequest(revision=edited.revision))
        original_bytes = (store.project_dir(edited.id) / 'project.json').read_bytes()
        self.assertFalse(duplicate.undo_available)
        self.assertFalse(duplicate.redo_available)
        with self.assertRaisesRegex(store.VideoProjectError, 'nothing_to_undo'):
            store.undo(duplicate.id, VideoRevisionRequest(revision=duplicate.revision))
        self.assertEqual((store.project_dir(edited.id) / 'project.json').read_bytes(), original_bytes)
        restored = store.undo(edited.id, VideoRevisionRequest(revision=edited.revision))
        another = store.duplicate(restored.id, VideoRevisionRequest(revision=restored.revision))
        self.assertFalse(another.redo_available)
        self.assertFalse(store.load(another.id).redo_history)

    async def test_cross_project_history_identity_is_rejected_before_any_publication(self) -> None:
        other = await store.create(CreateVideoProjectRequest(duration_sec=4, name='Other'))
        edited = store.update(self.project.id, UpdateVideoProjectRequest(revision=1, name='Current'))
        document = store.load(edited.id)
        document.undo_history[-1].project.id = other.id
        store.save(document)
        original_bytes = (store.project_dir(edited.id) / 'project.json').read_bytes()
        other_bytes = (store.project_dir(other.id) / 'project.json').read_bytes()
        with self.assertRaisesRegex(store.VideoProjectError, 'history_invalid'):
            store.undo(edited.id, VideoRevisionRequest(revision=edited.revision))
        self.assertEqual((store.project_dir(edited.id) / 'project.json').read_bytes(), original_bytes)
        self.assertEqual((store.project_dir(other.id) / 'project.json').read_bytes(), other_bytes)

    async def test_duplicate_does_not_claim_historical_media_not_copied_to_its_library(self) -> None:
        document = store.load(self.project.id)
        document.speech_path = 'speech/original.wav'
        document.project.speech_clip = VideoSpeechClip(id='c'*32, name='Line', bytes=4, duration_sec=1, sha256='d'*64)
        speech = store.artifact(self.project.id, document.speech_path)
        speech.parent.mkdir(); speech.write_bytes(b'PCM!'); store.save(document)
        cleared = store.clear_speech(self.project.id, VideoRevisionRequest(revision=1))
        duplicate = store.duplicate(cleared.id, VideoRevisionRequest(revision=cleared.revision))
        self.assertFalse(duplicate.undo_available)
        self.assertEqual(store.load(duplicate.id).undo_history, [])
        self.assertTrue(speech.exists())

    async def test_export_media_version_changes_on_replacement_and_returns_after_undo(self) -> None:
        document = store.load(self.project.id)
        old = store.artifact(self.project.id, 'exports/old.mp4'); old.parent.mkdir(); old.write_bytes(b'old')
        document.published_file = 'exports/old.mp4'; document.project.file_url = '/stable-file'; store.save(document)
        first = store.get(self.project.id)
        changed = store.update(first.id, UpdateVideoProjectRequest(revision=1, name='Changed'))
        replacement = store.load(first.id)
        new = store.artifact(first.id, 'exports/new.mp4'); new.write_bytes(b'new')
        replacement.published_file = 'exports/new.mp4'; store.save(replacement)
        second = store.get(first.id)
        self.assertNotEqual(first.output_version, second.output_version)
        restored = store.undo(first.id, VideoRevisionRequest(revision=changed.revision))
        self.assertEqual(restored.output_version, first.output_version)
        self.assertEqual(restored.file_url, first.file_url)
