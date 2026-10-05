"""Provider changes preserve local configuration and reject checkpoint transfer."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import video_projects as store
from app.video_contracts import CreateVideoProjectRequest, UpdateVideoProjectRequest, VideoShotDraft, OpenRouterVideoProviderConfig, VideoRevisionRequest


class CloudVideoProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store,'DATA_DIR',self.root))
        self.project=await store.create(CreateVideoProjectRequest(duration_sec=12))

    async def test_cloud_selection_is_saved_in_history_without_replacing_ltx_settings(self) -> None:
        original=self.project.settings.model_copy()
        cloud=store.update(self.project.id,UpdateVideoProjectRequest(revision=1,provider_config=OpenRouterVideoProviderConfig(model_id='google/veo-3.1-fast',size='1280x720')))
        self.assertEqual(cloud.provider_config.provider,'openrouter')
        self.assertEqual(cloud.settings,original)
        self.assertEqual(store.get(cloud.id).provider_config,cloud.provider_config)
        restored=store.undo(cloud.id, VideoRevisionRequest(revision=cloud.revision))
        self.assertEqual(restored.provider_config.provider,'local')

    async def test_cloud_rejects_a_retained_local_adapter_without_mutating_the_project(self) -> None:
        document=store.load(self.project.id);document.project.character_adapter_id='b'*32;store.save(document)
        before=(store.project_dir(self.project.id)/'project.json').read_bytes()
        with self.assertRaisesRegex(store.VideoProjectError,'cloud_lora_unsupported'):
            store.update(self.project.id,UpdateVideoProjectRequest(revision=1,provider_config=OpenRouterVideoProviderConfig(model_id='google/veo-3.1-fast',size='1280x720')))
        self.assertEqual((store.project_dir(self.project.id)/'project.json').read_bytes(),before)

    async def test_non_ltx_durations_are_native_cloud_timing_but_local_still_rejects_them(self) -> None:
        shot=VideoShotDraft(id='c'*32,start_sec=0,seconds=5,prompt='Native five-second shot')
        with self.assertRaisesRegex(store.VideoProjectError,'bad_length'):
            store.update(self.project.id,UpdateVideoProjectRequest(revision=1,shots=[shot]))
        cloud=store.update(self.project.id,UpdateVideoProjectRequest(revision=1,provider_config=OpenRouterVideoProviderConfig(model_id='kwaivgi/kling-video-o1',size='1280x720'),shots=[shot]))
        self.assertEqual(cloud.shots[0].seconds,5)

    async def test_detaching_a_local_adapter_is_reversible_and_does_not_delete_the_checkpoint(self) -> None:
        document=store.load(self.project.id);document.project.character_adapter_id='b'*32;store.save(document)
        checkpoint=self.root/'video_character_training'/('b'*32)/'adapter.safetensors'
        checkpoint.parent.mkdir(parents=True);checkpoint.write_bytes(b'held local checkpoint')
        detached=store.clear_character_adapter(self.project.id,VideoRevisionRequest(revision=1))
        self.assertIsNone(detached.character_adapter_id)
        self.assertEqual(checkpoint.read_bytes(),b'held local checkpoint')
        restored=store.undo(self.project.id,VideoRevisionRequest(revision=detached.revision))
        self.assertEqual(restored.character_adapter_id,'b'*32)

    async def test_unquoted_video_speech_refuses_cloud_clones_before_synthesis(self) -> None:
        from app.voice_profile_contracts import SpeechVoiceProfile, CloudSpeechConfiguration
        from app.video_contracts import VideoSpeechLineRequest
        profile=SpeechVoiceProfile(id='f'*32,name='Cloud clone',consent_confirmed=True,renderer='openrouter',reference_audio_path='reference.wav',
            cloud=CloudSpeechConfiguration(model='fish-audio/s2.1-pro',clone_reference=True,reference_transfer_confirmed=True),created_at='today',updated_at='today')
        with patch('app.video_characters.require_voice',return_value=profile), patch('app.speech_clone.synthesize_to_path',side_effect=AssertionError('unquoted synthesis must not be called')) as synthesize:
            with self.assertRaisesRegex(store.VideoProjectError,'cloud_speech_quote_required'):
                await store.speak_line(self.project.id,VideoSpeechLineRequest(revision=self.project.revision,profile_id=profile.id,text='Hello'))
            synthesize.assert_not_called()
