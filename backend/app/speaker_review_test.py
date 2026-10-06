"""Local screening owns work and never creates or accepts a paid speech take."""
from __future__ import annotations
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from app import audiobooks,audiobook_workflows,speaker_review as review,voice_profiles,speech_clone
from app.audiobook_workflows_test import _WorkflowFixture,pcm
from app.audiobook_contracts import CreateAudiobookRequest,AudiobookChapterInput
from app.speaker_review_contracts import SpeakerReviewRequest,SpeakerReviewCapability,SpeakerEncoderIdentity
from app.speaker_review_worker import EncoderIdentity,Embedding,WEIGHTS_SHA256
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest


class _SpeakerFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self)->None:
        self.fixture=_WorkflowFixture();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.enterContext(patch.object(speech_clone,"known_engine_identity",return_value="fixture-engine-v1"))
        def synthesize(**kwargs:object)->speech_clone.SynthesisOutcome:
            target=kwargs["output_path"];text=kwargs["text"]
            if not isinstance(target,Path) or not isinstance(text,str):raise TypeError("invalid fixture boundary")
            pcm(target,value=5000 if "First" in text else 7000,rate=16000,frames=64000)
            return speech_clone.SynthesisOutcome(status="completed",detail="",output_path=target)
        self.enterContext(patch.object(speech_clone,"synthesize_to_path",side_effect=synthesize))
        self.book=audiobooks.create_book(CreateAudiobookRequest(title="Book",profile_id=self.fixture.profile.id,
            chapters=[AudiobookChapterInput(text="First accepted speech."),AudiobookChapterInput(text="Second accepted speech.")])).book.id
        self.target=audiobook_workflows.get_passages(self.book,0)
        self.identity=EncoderIdentity(weights_sha256=WEIGHTS_SHA256,speechbrain_version="1.0.3",torch_version="2.6.0",torchaudio_version="2.6.0")
        self.capability=SpeakerReviewCapability(available=True,configured=True,deps_available=True,weights_available=True,
            encoder=SpeakerEncoderIdentity.model_validate(self.identity.to_json()))
        self.enterContext(patch.object(review,"capability",return_value=self.capability))
        await review.start();self.addAsyncCleanup(review.shutdown)

    def request(self)->SpeakerReviewRequest:
        refs=review.references(self.book,0,self.target.passages[0].id,self.target.revision,self.target.passages[0].render_identity or "")
        return SpeakerReviewRequest(revision=self.target.revision,render_identity=self.target.passages[0].render_identity or "",
            reference_id=refs.references[0].id,reference_reviewed=True,threshold=0.72)

    async def create(self):
        return await review.create(self.book,0,self.target.passages[0].id,self.request())

    async def settle(self,identifier:str)->None:
        task=review._TASKS.get(identifier)
        async with asyncio.timeout(5):
            while review.get(identifier).state in {"queued","running"}:await asyncio.sleep(0.01)
            if task is not None:await asyncio.shield(task)


class SpeakerReviewTests(_SpeakerFixture):
    async def test_unavailable_persists_without_launch_or_model_download(self)->None:
        with patch.object(review,"capability",return_value=SpeakerReviewCapability(available=False,reason="speaker_weights_missing")),patch.object(review,"spawn_owned") as spawn:
            item=await self.create()
        self.assertEqual(item.state,"unavailable");spawn.assert_not_called()
        self.assertEqual(review.get(item.id).reason,"speaker_weights_missing")

    async def test_review_requires_explicit_reference_approval_and_threshold(self)->None:
        body=self.request().model_dump()
        for key in ("reference_reviewed","threshold"):
            invalid=dict(body);del invalid[key]
            with self.assertRaises(ValidationError):SpeakerReviewRequest.model_validate(invalid)
        body["reference_reviewed"]=False
        with self.assertRaises(ValidationError):SpeakerReviewRequest.model_validate(body)

    async def test_selected_reference_score_persists_and_restores_without_speech_calls(self)->None:
        async def encode(identifier:str,workspace:Path):
            return Embedding(self.identity,[1.0]+[0.0]*191,4000,4000),Embedding(self.identity,[0.0,1.0]+[0.0]*190,4000,4000)
        with patch.object(review,"_encode",side_effect=encode),patch.object(speech_clone,"synthesize_to_path",side_effect=AssertionError("QA must not synthesize")):
            item=await self.create();await self.settle(item.id)
        result=review.get(item.id)
        self.assertEqual(result.state,"completed");self.assertEqual(result.score,0);self.assertTrue(result.below_threshold)
        self.assertNotIn(str(self.fixture.root),result.model_dump_json())
        await review.shutdown();await review.start()
        self.assertEqual(review.list_reviews(self.book).reviews[0],result)

    async def test_reference_bytes_changed_during_work_skip_publication(self)->None:
        entered,release=asyncio.Event(),asyncio.Event()
        async def encode(identifier:str,workspace:Path):
            entered.set();await release.wait()
            embedding=Embedding(self.identity,[1.0]+[0.0]*191,4000,4000)
            return embedding,embedding
        with patch.object(review,"_encode",side_effect=encode):
            item=await self.create();await entered.wait()
            source=audiobook_workflows.get_passages(self.book,1).passages[0]
            pcm(audiobook_workflows.passage_audio_path(self.book,source.id),value=8000,rate=16000,frames=64000)
            release.set();await self.settle(item.id)
        self.assertEqual(review.get(item.id).state,"skipped");self.assertIsNone(review.get(item.id).score)

    async def test_profile_changes_invalidate_existing_completed_hint(self)->None:
        async def encode(identifier:str,workspace:Path):
            embedding=Embedding(self.identity,[1.0]+[0.0]*191,4000,4000);return embedding,embedding
        with patch.object(review,"_encode",side_effect=encode):item=await self.create();await self.settle(item.id)
        voice_profiles.patch_profile(self.fixture.profile.id,PatchSpeechVoiceProfileRequest(reference_transcript="Changed reference words."))
        self.assertEqual(review.get(item.id).state,"stale");self.assertIsNone(review.get(item.id).score)

    async def test_encoder_changed_with_same_dimension_cannot_publish(self)->None:
        async def encode(identifier:str,workspace:Path):
            first=Embedding(self.identity,[1.0]+[0.0]*191,4000,4000)
            other=EncoderIdentity(weights_sha256="f"*64,speechbrain_version="1.0.3",torch_version="2.6.0",torchaudio_version="2.6.0")
            return first,Embedding(other,[1.0]+[0.0]*191,4000,4000)
        with patch.object(review,"_encode",side_effect=encode):item=await self.create();await self.settle(item.id)
        self.assertEqual(review.get(item.id).state,"failed");self.assertIsNone(review.get(item.id).score)

    async def test_cancel_waits_for_owned_work_and_removes_partial_workspace(self)->None:
        entered,cleaned=asyncio.Event(),asyncio.Event()
        async def encode(identifier:str,workspace:Path):
            try:entered.set();await asyncio.Future[None]()
            finally:cleaned.set()
            raise AssertionError("unreachable")
        with patch.object(review,"_encode",side_effect=encode):
            item=await self.create();await entered.wait();self.assertTrue(review.work_busy())
            result=await review.cancel(item.id)
        self.assertTrue(cleaned.is_set());self.assertEqual(result.state,"canceled");self.assertFalse(review.work_busy())
        self.assertFalse(review.workspace_path(item.id).exists())

    async def test_cancel_before_task_start_cleans_captured_audio(self)->None:
        item=await self.create()
        self.assertTrue(review.workspace_path(item.id).exists())
        canceled=await review.cancel(item.id)
        self.assertEqual(canceled.state,"canceled")
        self.assertFalse(review.workspace_path(item.id).exists())

    async def test_unknown_local_renderer_compares_accepted_bytes_with_explicit_warning(self)->None:
        with audiobooks._connect() as connection:
            from app.speech_references import SpeechRenderSnapshot
            for row in connection.execute("SELECT passage_id,snapshot_json FROM audiobook_sections").fetchall():
                snapshot=SpeechRenderSnapshot.model_validate_json(str(row[1])).model_copy(update={"engine_identity":None})
                connection.execute("UPDATE audiobook_sections SET snapshot_json=? WHERE passage_id=?",(snapshot.model_dump_json(),row[0]))
            connection.commit()
        async def encode(identifier:str,workspace:Path):
            embedding=Embedding(self.identity,[1.0]+[0.0]*191,4000,4000);return embedding,embedding
        with patch.object(review,"_encode",side_effect=encode):item=await self.create();await self.settle(item.id)
        result=review.get(item.id)
        self.assertEqual(result.state,"completed");self.assertFalse(result.renderer_identity_verified)
        self.assertIn("speaker_renderer_unverified",result.warnings)

    async def test_real_owned_worker_cancel_drains_process_before_releasing_registry(self)->None:
        import sys
        async def encode(identifier:str,workspace:Path):
            await review._run_tool(identifier,[sys.executable,"-c","import time;time.sleep(60)"],workspace,120)
            raise AssertionError("sleep worker must be canceled")
        with patch.object(review,"_encode",side_effect=encode):
            item=await self.create()
            async with asyncio.timeout(5):
                while item.id not in review._PROCESSES:await asyncio.sleep(0.01)
            process=review._PROCESSES[item.id]
            canceled=await review.cancel(item.id)
        self.assertEqual(canceled.state,"canceled");self.assertIsNotNone(process.returncode)
        self.assertFalse(review.work_busy());self.assertFalse(review.workspace_path(item.id).exists())

    async def test_restart_retains_unverified_worker_and_blocks_new_review(self)->None:
        from app.video_process import WorkerIdentity
        with patch.object(review,"capability",return_value=SpeakerReviewCapability(available=False)):
            item=await self.create()
        review._worker(item.id,WorkerIdentity(pid=999999,token="a"*32,receipt=str(review._directory(item.id)/"worker.json")))
        with patch.object(review,"terminate_verified",return_value=False):
            await review.shutdown();await review.start()
            self.assertTrue(review.work_busy())
            self.assertEqual(review.get(item.id).reason,"speaker_worker_unverified")
            with self.assertRaises(review.SpeakerReviewError):await self.create()
        # No real worker existed in this recovery fixture; release its fake receipt.
        review._worker(item.id,None);review._UNVERIFIED.discard(item.id);review._CLEANUP_PENDING.discard(item.id)

    async def test_capture_failure_is_structured_and_preserves_original_audio(self)->None:
        original=self.target.passages[0].audio_url
        with patch.object(review.shutil,"copyfile",side_effect=OSError("private source path")):
            with self.assertRaises(review.SpeakerReviewError) as caught:await self.create()
        self.assertEqual(caught.exception.code,"speaker_storage_unavailable")
        self.assertEqual(audiobook_workflows.get_passages(self.book,0).passages[0].audio_url,original)

    async def test_cast_reassignment_invalidates_completed_screening(self)->None:
        async def encode(identifier:str,workspace:Path):
            embedding=Embedding(self.identity,[1.0]+[0.0]*191,4000,4000);return embedding,embedding
        with patch.object(review,"_encode",side_effect=encode):item=await self.create();await self.settle(item.id)
        with audiobooks._connect() as connection:
            connection.execute("UPDATE audiobook_books SET profile_id=? WHERE id=?",("f"*32,self.book));connection.commit()
        result=review.get(item.id)
        self.assertEqual(result.state,"stale");self.assertIsNone(result.score)

    async def test_reviewed_saved_cast_audition_is_an_explicit_reference_choice(self)->None:
        from app.audiobook_contracts import CreateAudiobookAuditionRequest
        audition=audiobook_workflows.start_audition(CreateAudiobookAuditionRequest(title="Saved",profile_id=self.fixture.profile.id,
            chapters=[AudiobookChapterInput(text="Second preview speech.")]),book_id=self.book,revision=self.target.revision)
        choices=review.references(self.book,0,self.target.passages[0].id,self.target.revision,self.target.passages[0].render_identity or "")
        self.assertIn(audition.id,[choice.source_id for choice in choices.references if choice.kind=="audition"])

    async def test_shutdown_before_task_start_cleans_staging_and_records_interruption(self)->None:
        item=await self.create()
        await review.shutdown()
        self.assertEqual(review.get(item.id).state,"canceled")
        self.assertFalse(review.workspace_path(item.id).exists())

    async def test_copy_cleanup_failure_blocks_reentry_until_shutdown_retry(self)->None:
        with patch.object(review.shutil,"copyfile",side_effect=OSError("copy failed")),patch.object(review.shutil,"rmtree",side_effect=OSError("cleanup failed")):
            with self.assertRaises(review.SpeakerReviewError):await self.create()
            self.assertTrue(review.work_busy())
            with self.assertRaises(review.SpeakerReviewError):await self.create()
        await review.shutdown();await review.start()
        self.assertFalse(review.work_busy())

    async def test_missing_and_corrupt_orphan_metadata_do_not_disable_startup_or_history(self)->None:
        first=review._directory("a"*32);first.mkdir()
        second=review._directory("b"*32);second.mkdir();(second/"review.json").write_text("{")
        await review.shutdown();await review.start()
        self.assertFalse(review.work_busy());self.assertEqual(review.list_reviews(self.book).reviews,[])
        self.assertEqual((second/"review.json").read_text(),"{")

    async def test_corrupt_record_with_unknown_worker_receipt_is_retained_and_quarantined(self)->None:
        identifier="a"*32;path=review._directory(identifier);path.mkdir()
        (path/"review.json").write_text("{");receipt=path/"worker.json";receipt.write_text("not a verified worker")
        try:
            await review.shutdown();await review.start()
            self.assertTrue(review.work_busy());self.assertEqual(receipt.read_text(),"not a verified worker")
            self.assertEqual(review.list_reviews(self.book).reviews,[])
            with self.assertRaises(review.SpeakerReviewError):await self.create()
        finally:
            # This fixture has no real worker; release its synthetic quarantine.
            review._UNVERIFIED.discard(identifier);review._CLEANUP_PENDING.discard(identifier)

    async def test_record_without_worker_identity_cannot_hide_unknown_durable_receipt(self)->None:
        with patch.object(review,"capability",return_value=SpeakerReviewCapability(available=False)):item=await self.create()
        receipt=review._directory(item.id)/"worker.json";receipt.write_text("unknown supervisor")
        try:
            await review.shutdown();await review.start()
            self.assertTrue(review.work_busy());self.assertEqual(review.get(item.id).reason,"speaker_worker_unverified")
        finally:
            review._UNVERIFIED.discard(item.id);review._CLEANUP_PENDING.discard(item.id)
            receipt.unlink();review._worker(item.id,None)

    async def test_real_runner_insufficient_audio_error_is_skipped_after_process_drain(self)->None:
        import sys
        async def encode(identifier:str,workspace:Path):
            await review._run_tool(identifier,[sys.executable,"-c",'import sys;print(\'{"error":"speaker_audio_insufficient"}\');sys.exit(2)'],workspace,5,encoder_boundary=True)
            raise AssertionError("error child cannot return embeddings")
        with patch.object(review,"_encode",side_effect=encode):item=await self.create();await self.settle(item.id)
        result=review.get(item.id)
        self.assertEqual(result.state,"skipped");self.assertEqual(result.reason,"speaker_audio_insufficient")
        self.assertIsNone(result.score);self.assertFalse(review.work_busy());self.assertFalse(review.workspace_path(item.id).exists())

    async def test_startup_cleanup_failure_retains_pending_work_without_crashing_backend(self)->None:
        with patch.object(review,"capability",return_value=SpeakerReviewCapability(available=False)):item=await self.create()
        workspace=review.workspace_path(item.id);workspace.mkdir();(workspace/"partial.wav").write_bytes(b"owned partial")
        await review.shutdown()
        with patch.object(review.shutil,"rmtree",side_effect=PermissionError("locked staging")):
            await review.start()
            self.assertTrue(review.work_busy());self.assertTrue(workspace.exists())
        await review.start();self.assertFalse(review.work_busy());self.assertFalse(workspace.exists())

    async def test_invalid_optional_namespace_does_not_abort_startup_or_delete_evidence(self)->None:
        parent=review._directory("0"*32).parent
        parent.rmdir();parent.write_text("unowned evidence")
        try:
            await review.start()
            self.assertTrue(review.work_busy());self.assertEqual(parent.read_text(),"unowned evidence")
        finally:
            parent.unlink();await review.start()
        self.assertFalse(review.work_busy())
