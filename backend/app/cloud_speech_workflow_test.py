"""Cloud books, auditions and repairs reuse local publication with mock paid transport."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import audiobooks, audiobook_workflows, cloud_speech, speech_clone, voice_profiles
from app import openrouter_catalog as catalog, openrouter_client, openrouter_requests as receipts, openrouter_settings as settings
from app.audiobook_contracts import AudiobookChapterInput, CreateAudiobookRequest, CreateAudiobookAuditionRequest, CreateAudiobookRepairRequest, AudiobookCloudControlRequest
from app.voice_profile_contracts import CreateCloudSpeechVoiceProfileRequest, CloudSpeechApproval
from app.openrouter_contracts import OpenRouterSettingsRequest, OpenRouterKeyRequest


class CloudWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(audiobooks,"BOOKS_ROOT",self.root/"books"),patch.object(speech_clone,"TRIALS_ROOT",self.root/"trials"),patch.object(voice_profiles,"PROFILES_ROOT",self.root/"profiles"),
            patch.object(settings,"SETTINGS_PATH",self.root/"settings.json"),patch.object(catalog,"CATALOG_PATH",self.root/"catalog.json"),patch.object(receipts,"REQUESTS_ROOT",self.root/"requests"),
            patch.object(settings.credentials,"native_backend",return_value=None),patch.dict(os.environ,{"OPENFABRIC_AUDIOBOOK_SYNC":"0","OPENFABRIC_SPEECH_CLONE_MOCK":"1"})]
        for item in self.patches:
            item.start()
        settings.credentials.clear_session()
        settings.save(OpenRouterSettingsRequest(enabled=True,estimate_limit_usd=1))
        settings.set_credential(OpenRouterKeyRequest(api_key="sk-or-v1-"+"x"*48,persist=False))
        catalog.publish(catalog.normalize_audio({"data":[{"id":"hexgrad/kokoro-82m","name":"Kokoro","architecture":{"output_modalities":["speech"]},"supported_voices":["af_heart","af_bella"],"pricing":{"prompt":"0.000001"}}]},"speech"))
        self.profile = voice_profiles.create_cloud_profile(CreateCloudSpeechVoiceProfileRequest(name="Cloud Reader",model="hexgrad/kokoro-82m",voice="af_heart"))
        source = self.root/"fixture.wav"
        speech_clone._write_silent_wav(source)
        from app import audiobook_publish
        await asyncio.to_thread(audiobook_publish._run,["-y","-i",str(source),str(self.root/"fixture.mp3")])
        self.audio = (self.root/"fixture.mp3").read_bytes()
        self.posts: list[dict[str,object]] = []
        self.ambiguous = False
        def respond(request: httpx.Request) -> httpx.Response:
            self.posts.append(json.loads(request.content))
            if self.ambiguous:
                raise httpx.ReadTimeout("secret provider data",request=request)
            return httpx.Response(200,headers={"content-type":"audio/mpeg","x-generation-id":"gen-fixture"},content=self.audio)
        client = openrouter_client.OpenRouterClient(transport=httpx.MockTransport(respond))
        self.client_patch = patch.object(openrouter_client,"OpenRouterClient",return_value=client)
        self.client_patch.start()
        openrouter_client.start()
        speech_clone.start()
        await audiobooks.start()
        await audiobook_workflows.start()

    async def asyncTearDown(self) -> None:
        await audiobook_workflows.shutdown()
        await audiobooks.shutdown()
        await openrouter_client.shutdown()
        self.client_patch.stop()
        settings.credentials.clear_session()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def body(self) -> CreateAudiobookRequest:
        return CreateAudiobookRequest(title="Cloud book",profile_id=self.profile.id,language="en",chapters=[AudiobookChapterInput(text="Hello from a cloud narrator.")])

    def approve(self, body: CreateAudiobookRequest) -> CreateAudiobookRequest:
        from app.audiobook_cloud import quote_creation
        quote=quote_creation(body)
        return body.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})

    async def test_book_refuses_unapproved_cloud_before_persisting_or_posting(self) -> None:
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            audiobooks.create_book(self.body())
        self.assertEqual(caught.exception.code,"cloud_speech_approval_required")
        self.assertEqual(audiobooks.list_books(),[])
        self.assertEqual(self.posts,[])

    async def test_cloud_book_records_receipt_and_real_pcm_even_when_local_mock_is_enabled(self) -> None:
        created=audiobooks.create_book(self.approve(self.body()))
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"done")
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.posts[0]["voice"],"af_heart")
        self.assertNotIn("input_references",self.posts[0])
        self.assertEqual(receipts.list_receipts()[0].state,"completed")
        self.assertEqual(receipts.list_receipts()[0].remote_id,"gen-fixture")
        passage=audiobook_workflows.get_passages(created.book.id,0).passages[0]
        provenance=cloud_speech.read_provenance(audiobook_workflows.passage_audio_path(created.book.id,passage.id))
        self.assertIsNotNone(provenance)
        self.assertEqual(audiobooks.get_book(created.book.id).cloud_models,["hexgrad/kokoro-82m"])

    async def test_cloud_audition_and_repair_use_owned_pcm_and_fresh_receipts(self) -> None:
        draft=CreateAudiobookAuditionRequest(**self.body().model_dump(),mode="cast")
        quote=audiobook_workflows.quote_audition(draft)
        draft=draft.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})
        audition=audiobook_workflows.start_audition(draft)
        await audiobook_workflows.wait_for(audition.id)
        result=audiobook_workflows.get_audition(audition.id)
        self.assertEqual(result.status,"done",result.detail)
        self.assertFalse(result.clips[0].mock)
        created=audiobooks.create_book(self.approve(self.body()))
        await audiobooks.wait_for_book(created.book.id)
        passages=audiobook_workflows.get_passages(created.book.id,0)
        request=CreateAudiobookRepairRequest(revision=passages.revision,text="A fresh repaired line.")
        quote=audiobook_workflows.quote_repair(created.book.id,0,passages.passages[0].id,request)
        request=request.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})
        repair=audiobook_workflows.start_repair(created.book.id,0,passages.passages[0].id,request)
        await audiobook_workflows.wait_for(repair.id)
        self.assertEqual(audiobook_workflows.get_repair(repair.id).status,"ready")
        self.assertFalse(audiobook_workflows.get_repair(repair.id).mock)
        self.assertEqual(len(self.posts),3)
        self.assertEqual(len(receipts.list_receipts()),3)

    async def test_unknown_paid_book_cannot_retry_or_resume_without_explicit_fresh_redo(self) -> None:
        self.ambiguous=True
        created=audiobooks.create_book(self.approve(self.body()))
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"failed")
        self.assertEqual(receipts.list_receipts()[0].state,"submission_unknown")
        receipts.recover()
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            audiobooks.retry_failed(created.book.id)
        self.assertEqual(caught.exception.code,"cloud_speech_submission_unknown")
        with self.assertRaises(voice_profiles.VoiceProfileError):
            await audiobooks.resume_book(created.book.id)
        self.assertEqual(len(self.posts),1)

    async def test_fresh_redo_makes_a_new_paid_take_and_preserves_frozen_voice_after_profile_edit(self) -> None:
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest,CloudSpeechConfiguration
        from app.audiobook_cloud import quote_control
        created=audiobooks.create_book(self.approve(self.body()))
        voice_profiles.patch_profile(self.profile.id,PatchSpeechVoiceProfileRequest(cloud=CloudSpeechConfiguration(model="hexgrad/kokoro-82m",voice="af_bella")))
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(self.posts[0]["voice"],"af_heart")
        control=AudiobookCloudControlRequest(action="regenerate",chapter_index=0)
        quote=quote_control(created.book.id,control)
        control=control.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})
        audiobooks.regenerate_chapter(created.book.id,0,control)
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"done")
        self.assertEqual([post["voice"] for post in self.posts],["af_heart","af_bella"])
        self.assertEqual(len(receipts.list_receipts()),2)

    async def test_costed_resume_seeds_new_sections_after_text_edit(self) -> None:
        from app.audiobook_cloud import quote_control
        with patch.object(audiobooks,"_schedule_book"):
            created=audiobooks.create_book(self.approve(self.body()))
        audiobooks.pause_book(created.book.id)
        audiobooks.set_chapter_text(created.book.id,0,"Edited narration before the first paid call.")
        control=AudiobookCloudControlRequest(action="resume")
        quote=quote_control(created.book.id,control)
        control=control.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})
        await audiobooks.resume_book(created.book.id,control)
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"done")
        self.assertEqual(self.posts[0]["input"],"Edited narration before the first paid call.")

    async def test_changed_profile_invalidates_paid_quote_before_a_book_is_created(self) -> None:
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest,CloudSpeechConfiguration
        request=self.approve(self.body())
        voice_profiles.patch_profile(self.profile.id,PatchSpeechVoiceProfileRequest(cloud=CloudSpeechConfiguration(model="hexgrad/kokoro-82m",voice="af_bella")))
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            audiobooks.create_book(request)
        self.assertEqual(caught.exception.code,"cloud_speech_quote_changed")
        self.assertEqual(audiobooks.list_books(),[])
        self.assertEqual(self.posts,[])

    async def test_consent_is_checked_inside_provider_submission_boundary(self) -> None:
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        from app.openrouter_errors import OpenRouterError
        request=self.approve(self.body())
        original=catalog.validate_quote
        def revoke(quote_id,body):
            voice_profiles.patch_profile(self.profile.id,PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            return original(quote_id,body)
        with patch.object(catalog,"validate_quote",side_effect=revoke):
            created=audiobooks.create_book(request)
            await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(self.posts,[])
        self.assertEqual(audiobooks.get_book(created.book.id).status,"failed")
        self.assertEqual(receipts.list_receipts()[0].state,"intent")

    async def test_provenance_failure_keeps_completed_receipt_unpublished_and_blocks_another_charge(self) -> None:
        from app import atomic_files
        original=atomic_files.write_object
        def fail_provenance(path,value):
            if path.name.endswith('.cloud.json'):
                raise OSError('private filesystem failure')
            return original(path,value)
        with patch.object(atomic_files,"write_object",side_effect=fail_provenance):
            created=audiobooks.create_book(self.approve(self.body()))
            await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"failed")
        self.assertEqual(receipts.list_receipts()[0].state,"completed")
        with cloud_speech._connect() as connection:
            self.assertEqual(connection.execute("SELECT normalized FROM renders").fetchone()[0],0)
        with self.assertRaises(voice_profiles.VoiceProfileError):
            audiobooks.retry_failed(created.book.id)
        self.assertEqual(len(self.posts),1)

    async def test_long_cloud_chapter_uses_exact_trimmed_section_text_for_every_paid_quote(self) -> None:
        request=CreateAudiobookRequest(title="Long cloud book",profile_id=self.profile.id,language="en",chapters=[AudiobookChapterInput(text="Hello world. "*140)])
        created=audiobooks.create_book(self.approve(request))
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"done")
        self.assertEqual(len(self.posts),2)
        self.assertTrue(all(post['input']==post['input'].strip() for post in self.posts))

    async def test_mixed_cloud_and_local_casts_join_without_changing_dry_passage_audio(self) -> None:
        from app.audiobook_contracts import CastMember,AcceptAudiobookRepairRequest
        from app.audiobook_cloud import quote_creation
        local=voice_profiles.create_profile(name="Alice",consent_confirmed=True,audio_bytes=(self.root/"fixture.wav").read_bytes(),filename="ref.wav",reference_transcript="Reference words.")
        body=CreateAudiobookRequest(title="Mixed cast",profile_id=self.profile.id,language="en",cast=[CastMember(name="Alice",profile_id=local.id)],chapters=[AudiobookChapterInput(text="Cloud narrator line.\nAlice: Local actor line.")])
        created=audiobooks.create_book(self.approve(body))
        await audiobooks.wait_for_book(created.book.id)
        self.assertEqual(audiobooks.get_book(created.book.id).status,"done")
        import wave
        passages=audiobook_workflows.get_passages(created.book.id,0)
        dry_rates=[]
        for passage in passages.passages:
            with wave.open(str(audiobook_workflows.passage_audio_path(created.book.id,passage.id)),"rb") as audio:
                dry_rates.append(audio.getframerate())
        self.assertEqual(dry_rates,[24000,16000])
        draft=CreateAudiobookAuditionRequest(**body.model_dump(),mode="scene")
        quote=audiobook_workflows.quote_audition(draft)
        audition=audiobook_workflows.start_audition(draft.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)}))
        await audiobook_workflows.wait_for(audition.id)
        self.assertEqual(audiobook_workflows.get_audition(audition.id).status,"done")
        request=CreateAudiobookRepairRequest(revision=passages.revision,text="Repaired cloud narrator.")
        quote=audiobook_workflows.quote_repair(created.book.id,0,passages.passages[0].id,request)
        repair=audiobook_workflows.start_repair(created.book.id,0,passages.passages[0].id,request.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)}))
        await audiobook_workflows.wait_for(repair.id)
        accepted=await asyncio.to_thread(audiobook_workflows.accept_repair,repair.id,AcceptAudiobookRepairRequest(revision=passages.revision))
        self.assertEqual(accepted.passages[0].text,"Repaired cloud narrator.")
        self.assertEqual(len(self.posts),3)

    async def test_paid_trials_rediscover_after_reload_and_ignore_invalid_or_symlink_metadata(self) -> None:
        import time
        from app.voice_profile_contracts import SpeechCloneTrialRequest,PatchSpeechVoiceProfileRequest
        body=SpeechCloneTrialRequest(profile_id=self.profile.id,text="A recoverable paid trial.",text_language="en")
        quote=cloud_speech.quote_inputs([(body.text,self.profile.id,"en")])
        body=body.model_copy(update={"cloud_approval":CloudSpeechApproval(quote_id=quote.id,transfers_confirmed=True)})
        trial=await asyncio.to_thread(speech_clone.start_trial,body)
        self.assertIsNotNone(trial.trial_id)
        self.assertEqual(trial.engine,"openrouter")
        listed=cloud_speech.list_trials(self.profile.id)
        self.assertEqual([item.id for item in listed],[trial.trial_id])
        self.assertNotIn(str(self.root),listed[0].model_dump_json())
        from app.api.routes_speech_clone import get_speech_clone_trial_audio
        from fastapi import HTTPException
        self.assertEqual(get_speech_clone_trial_audio(listed[0].id).media_type,"audio/wav")
        root=speech_clone.trials_root()
        malformed=root/f"{'f'*32}.cloud.json"
        malformed.write_text('{"private":"not a public provenance"}')
        (root/f"{'f'*32}.wav").write_bytes((self.root/"fixture.wav").read_bytes())
        (root/f"{'e'*32}.cloud.json").symlink_to(root/f"{trial.trial_id}.cloud.json")
        (root/f"{'e'*32}.wav").write_bytes((self.root/"fixture.wav").read_bytes())
        for index in range(25):
            identifier=f"{index:032x}"
            metadata=root/f"{identifier}.cloud.json"
            metadata.write_text(listed[0].provenance.model_dump_json())
            (root/f"{identifier}.wav").write_bytes((self.root/"fixture.wav").read_bytes())
            os.utime(metadata,(time.time()+index+1,time.time()+index+1))
        newest=cloud_speech.list_trials(self.profile.id)
        self.assertEqual(len(newest),20)
        self.assertEqual(newest[0].id,f"{24:032x}")
        self.assertEqual(len(self.posts),1)
        voice_profiles.patch_profile(self.profile.id,PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        self.assertEqual(cloud_speech.list_trials(self.profile.id),[])
        with self.assertRaises(HTTPException) as caught:
            get_speech_clone_trial_audio(listed[0].id)
        self.assertEqual(caught.exception.status_code,403)
        metadata=root/f"{listed[0].id}.cloud.json"
        metadata.unlink()
        metadata.symlink_to(root/"missing-sidecar")
        with self.assertRaises(HTTPException) as unsafe:
            get_speech_clone_trial_audio(listed[0].id)
        self.assertEqual(unsafe.exception.status_code,503)
