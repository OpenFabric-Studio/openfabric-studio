"""Dialogue reels copy completed cast PCM without model calls or hidden cuts."""
from __future__ import annotations
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from app.video_contracts import CreateDialogueReelRequest, DialogueReelSelection
from app.audiobook_contracts import AudiobookPassage, AudiobookPassagesResponse


class DialogueReelTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, root: Path, seconds: float = 1) -> tuple[Path, AudiobookPassagesResponse]:
        path = root / 'passage.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(struct.pack('<h', 1200) * int(seconds * 8000))
        passages = AudiobookPassagesResponse(book_id='a'*32, chapter_index=0, revision=2,
            passages=[AudiobookPassage(id='b'*32, section_index=0, text='Hello there', profile_id='c'*32,
                speaker='Actor', start_ms=0, end_ms=int(seconds*1000), status='done', render_identity='d'*64)])
        return path, passages

    async def test_import_persists_original_pcm_padding_captions_and_provenance(self) -> None:
        from app import video_dialogue as dialogue, video_projects as store, audiobook_workflows as source, audiobooks
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, passages = self.fixture(root)
            with patch.object(store, 'DATA_DIR', root), patch.object(audiobooks, 'BOOKS_ROOT', root/'books'), \
                patch.object(source, 'get_passages', return_value=passages), patch.object(source, 'passage_audio_path', return_value=path), \
                patch.object(dialogue, 'require_voice'):
                project = await dialogue.create(CreateDialogueReelRequest(book_id='a'*32, chapter_index=0, revision=2,
                    selections=[DialogueReelSelection(passage_id='b'*32)]))
                self.assertEqual(project.duration_sec, 2)
                self.assertEqual(project.dialogue_cues[0].render_identity, 'd'*64)
                self.assertEqual(project.overlays[0].text, 'Hello there')
                self.assertEqual(project.overlays[0].end_sec, 1)
                self.assertEqual(store.get(project.id).dialogue_cues, project.dialogue_cues)
                with wave.open(str(store.speech_file(project.id)), 'rb') as audio:
                    self.assertEqual(audio.getnframes(), 16000)
                    self.assertEqual(audio.readframes(8000), struct.pack('<h', 1200)*8000)
                    self.assertEqual(audio.readframes(8000), bytes(16000))
                self.assertTrue(project.export_settings.attach_speech)
                self.assertEqual(project.warnings, ['dialogue_video_is_silent'])

    async def test_refresh_changes_one_cue_and_retains_other_approval_and_original_history_audio(self) -> None:
        from app import video_dialogue as dialogue, video_projects as store, audiobook_workflows as source, audiobooks
        from app.video_contracts import RefreshDialogueCueRequest
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); path, passages=self.fixture(root)
            passages.passages.append(passages.passages[0].model_copy(update={"id": "e"*32, "speaker":"Other"}))
            with patch.object(store,'DATA_DIR',root), patch.object(audiobooks,'BOOKS_ROOT',root/'books'), \
                patch.object(source,'get_passages',return_value=passages), patch.object(source,'passage_audio_path',return_value=path), \
                patch.object(dialogue,'require_voice'):
                project=await dialogue.create(CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=2,
                    selections=[DialogueReelSelection(passage_id='b'*32),DialogueReelSelection(passage_id='e'*32)]))
                original=store.speech_file(project.id)
                document=store.load(project.id)
                document.project.shots[0].approved_variant_id='1'*32
                document.project.shots[1].approved_variant_id='2'*32
                store.save(document)
                passages.revision=3
                passages.passages[0].text='A repaired line'
                passages.passages[0].render_identity='f'*64
                refreshed=await dialogue.refresh(project.id,project.shots[0].id,RefreshDialogueCueRequest(revision=1,
                    source=CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=3,
                        selections=[DialogueReelSelection(passage_id='b'*32)])))
                self.assertIsNone(refreshed.shots[0].approved_variant_id)
                self.assertEqual(refreshed.shots[1].approved_variant_id,'2'*32)
                self.assertEqual(refreshed.dialogue_cues[0].text,'A repaired line')
                self.assertEqual(refreshed.dialogue_cues[1],project.dialogue_cues[1])
                self.assertNotEqual(store.speech_file(project.id),original)
                self.assertTrue(original.exists())
                self.assertTrue(refreshed.undo_available)

    async def test_stale_or_long_unselected_audio_cannot_publish_partial_project(self) -> None:
        from app import video_dialogue as dialogue, video_projects as store, audiobook_workflows as source, audiobooks
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); path, passages=self.fixture(root, 7)
            with patch.object(store, 'DATA_DIR', root), patch.object(audiobooks, 'BOOKS_ROOT', root/'books'), \
                patch.object(source,'get_passages',return_value=passages), patch.object(source,'passage_audio_path',return_value=path), \
                patch.object(dialogue,'require_voice'):
                request=CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=2,selections=[DialogueReelSelection(passage_id='b'*32)])
                with self.assertRaisesRegex(store.VideoProjectError,'dialogue_clip_bounds'):
                    await dialogue.create(request)
                self.assertEqual(store.list_projects(), [])
                request.selections[0].clip_end_ms=6000
                with self.assertRaisesRegex(store.VideoProjectError,'dialogue_clip_caption_required'):
                    await dialogue.create(request)
                self.assertEqual(store.list_projects(), [])
                request.revision=1
                with self.assertRaisesRegex(store.VideoProjectError,'passage_changed'):
                    await dialogue.create(request)
                self.assertEqual(store.list_projects(), [])

    async def test_cleanup_failure_does_not_mask_a_committed_refresh_or_primary_validation_error(self) -> None:
        from app import video_dialogue as dialogue, video_projects as store, audiobook_workflows as source, audiobooks
        from app.video_contracts import RefreshDialogueCueRequest
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); path, passages=self.fixture(root)
            with patch.object(store,'DATA_DIR',root), patch.object(audiobooks,'BOOKS_ROOT',root/'books'), patch.object(source,'get_passages',return_value=passages), patch.object(source,'passage_audio_path',return_value=path), patch.object(dialogue,'require_voice'):
                request=CreateDialogueReelRequest(book_id='a'*32,chapter_index=0,revision=2,selections=[DialogueReelSelection(passage_id='b'*32)])
                project=await dialogue.create(request)
                passages.passages[0].text='New accepted words'
                with patch.object(dialogue.shutil,'rmtree',side_effect=OSError('isolated cleanup failure')):
                    refreshed=await dialogue.refresh(project.id,project.shots[0].id,RefreshDialogueCueRequest(revision=1,source=request))
                    self.assertEqual(refreshed.dialogue_cues[0].text,'New accepted words')
                    self.assertEqual(store.get(project.id).revision,refreshed.revision)
                    request.revision=0
                    with self.assertRaisesRegex(store.VideoProjectError,'passage_changed'):
                        await dialogue.create(request)
