from __future__ import annotations
import httpx
from fastapi import FastAPI
from app.speaker_review_test import _SpeakerFixture
from app.api.routes_speaker_review import router
from app import speaker_review as review


class SpeakerReviewApiTests(_SpeakerFixture):
    async def asyncSetUp(self)->None:
        await super().asyncSetUp()
        app=FastAPI();app.include_router(router)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)

    async def test_unapproved_or_cross_origin_request_cannot_start_screening(self)->None:
        path=f"/api/audiobooks/{self.book}/chapters/0/passages/{self.target.passages[0].id}/speaker-review"
        body=self.request().model_dump();body["reference_reviewed"]=False
        invalid=await self.client.post(path,json=body)
        self.assertEqual(invalid.status_code,422);self.assertFalse(review.work_busy())
        denied=await self.client.post(path,json=self.request().model_dump(),headers={"Origin":"https://untrusted.example"})
        self.assertEqual(denied.status_code,403);self.assertFalse(review.work_busy())

    async def test_recovery_lists_and_polls_are_read_only(self)->None:
        from unittest.mock import patch
        from app.speaker_review_contracts import SpeakerReviewCapability
        with patch.object(review,"capability",return_value=SpeakerReviewCapability(available=False)):
            result=await self.create()
        original=review._path(result.id).read_bytes()
        listed=await self.client.get(f"/api/audiobooks/{self.book}/speaker-reviews")
        polled=await self.client.get(f"/api/audiobooks/speaker-review/{result.id}")
        self.assertEqual(listed.status_code,200);self.assertEqual(polled.status_code,200)
        self.assertEqual(listed.json()["reviews"][0]["id"],result.id)
        self.assertEqual(review._path(result.id).read_bytes(),original)

    async def test_invalid_chapter_indices_are_rejected_before_sqlite_or_job_admission(self)->None:
        for chapter in (10**80,-1,100):
            base=f"/api/audiobooks/{self.book}/chapters/{chapter}/passages/{self.target.passages[0].id}"
            listed=await self.client.get(base+"/speaker-references",params={"revision":self.target.revision,"render_identity":self.target.passages[0].render_identity})
            started=await self.client.post(base+"/speaker-review",json=self.request().model_dump())
            self.assertEqual(listed.status_code,422);self.assertEqual(started.status_code,422)
            self.assertFalse(review.work_busy())
