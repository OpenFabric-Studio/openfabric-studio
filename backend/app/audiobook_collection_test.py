"""Snapshot cache cleanup must respect queued and streaming downloads."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from starlette.responses import Response
from starlette.types import Message, Scope

from app import audiobook_collection, audiobooks, voice_profiles
from app.audiobook_contracts import AudiobookChapterInput, CreateAudiobookRequest
from app.api.routes_audiobooks import download_audiobook_collection, router


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "method": "GET", "headers": headers or [],
            "extensions": {"http.response.pathsend": {}}}


async def _receive() -> Message:
    return {"type": "http.disconnect"}


class CollectionDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [
            patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"),
            patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"),
            patch.dict(os.environ, {"OPENFABRIC_AUDIOBOOK_SYNC": "1", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"}),
        ]
        for item in self.patches:
            item.start()
        profile = voice_profiles.create_profile(
            name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", reference_transcript="Reference.", notes="Reference.",
        )
        self.book_id = audiobooks.create_book(CreateAudiobookRequest(
            title="Field Notes", profile_id=profile.id,
            chapters=[AudiobookChapterInput(title="One", text="A short narration.")],
        )).book.id
        self.book_root = audiobooks.book_dir(self.book_id)

    def tearDown(self) -> None:
        self.assertFalse(audiobook_collection._ACTIVE)
        self.assertFalse(audiobook_collection._LATEST)
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    async def _deliver(self, response: Response, headers: list[tuple[bytes, bytes]] | None = None) -> list[Message]:
        messages: list[Message] = []

        async def send(message: Message) -> None:
            messages.append(message)

        # Other endpoints may use pathsend, but the collection's cache lease
        # must cover actual bytes, rather than a server's later path open.
        await response(_scope(headers), _receive, send)
        return messages

    def _revise(self) -> None:
        audiobooks.set_languages(self.book_id, "fr", [(0, "fr")])

    async def test_new_download_prunes_obsolete_completed_snapshot(self) -> None:
        await self._deliver(download_audiobook_collection(self.book_id))
        first = next(self.book_root.glob("collection-*.zip"))
        self._revise()
        await self._deliver(download_audiobook_collection(self.book_id))
        self.assertFalse(first.exists())
        self.assertEqual(len(list(self.book_root.glob("collection-*.zip"))), 1)
        self.assertTrue(audiobooks.export_path_for(self.book_id).is_file())
        self.assertTrue(audiobooks.chapter_audio_path(self.book_id, 0).is_file())

    async def test_same_snapshot_stays_until_both_queued_responses_finish(self) -> None:
        waiting = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        async def deliver(index: int) -> None:
            async def send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    waiting[index].set()
                    await release[index].wait()
            await download_audiobook_collection(self.book_id)(_scope(), _receive, send)

        tasks = [asyncio.create_task(deliver(index)) for index in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in waiting)), 3)
            first = next(self.book_root.glob("collection-*.zip"))
            self._revise()
            await self._deliver(download_audiobook_collection(self.book_id))
            release[0].set()
            await tasks[0]
            self.assertTrue(first.exists())
            release[1].set()
            await tasks[1]
            self.assertFalse(first.exists())
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancelled_prepare_drains_thread_and_releases_lease(self) -> None:
        waiting, release = threading.Event(), threading.Event()
        original = audiobook_collection._write_collection

        def blocked(book_id: str) -> Path:
            waiting.set()
            if not release.wait(3):
                raise RuntimeError("Test preparation was not released")
            return original(book_id)

        with patch.object(audiobook_collection, "_write_collection", side_effect=blocked):
            pending = asyncio.create_task(self._deliver(download_audiobook_collection(self.book_id)))
            try:
                self.assertTrue(await asyncio.to_thread(waiting.wait, 3))
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                await asyncio.sleep(0)
                self.assertFalse(pending.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
        self.assertFalse(audiobook_collection._ACTIVE)
        self.assertFalse(audiobook_collection._LATEST)
        self.assertEqual(len(list(self.book_root.glob("collection-*.zip"))), 1)

    async def test_cancelled_send_releases_obsolete_snapshot(self) -> None:
        waiting = asyncio.Event()

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                waiting.set()
                await asyncio.Event().wait()

        pending = asyncio.create_task(download_audiobook_collection(self.book_id)(_scope(), _receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            first = next(self.book_root.glob("collection-*.zip"))
            self._revise()
            await self._deliver(download_audiobook_collection(self.book_id))
        finally:
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertFalse(first.exists())
        self.assertEqual(len(list(self.book_root.glob("collection-*.zip"))), 1)

    async def test_send_failure_releases_lease(self) -> None:
        async def send(message: Message) -> None:
            raise OSError("Test client disconnected")

        with self.assertRaises(OSError):
            await download_audiobook_collection(self.book_id)(_scope(), _receive, send)
        self.assertFalse(audiobook_collection._ACTIVE)

    async def test_streaming_body_retains_snapshot_until_delivery_finishes(self) -> None:
        waiting, release = asyncio.Event(), asyncio.Event()
        delivered: list[bytes] = []

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                delivered.append(message.get("body", b""))
                waiting.set()
                await release.wait()

        pending = asyncio.create_task(download_audiobook_collection(self.book_id)(_scope(), _receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            first = next(self.book_root.glob("collection-*.zip"))
            original = first.read_bytes()
            self._revise()
            await self._deliver(download_audiobook_collection(self.book_id))
            self.assertTrue(first.is_file())
        finally:
            release.set()
            await pending
        self.assertEqual(b"".join(delivered), original)
        self.assertFalse(first.exists())

    async def test_failed_publication_keeps_completed_archive(self) -> None:
        await self._deliver(download_audiobook_collection(self.book_id))
        first = next(self.book_root.glob("collection-*.zip"))
        original = first.read_bytes()
        self._revise()
        with patch.object(audiobook_collection.zipfile, "ZipFile", side_effect=OSError("disk full")):
            with self.assertLogs(audiobook_collection._LOG, level="ERROR"):
                with self.assertRaises(HTTPException) as failed:
                    await self._deliver(download_audiobook_collection(self.book_id))
        self.assertEqual(failed.exception.detail, "audiobook_storage_unavailable")
        self.assertEqual(first.read_bytes(), original)
        self.assertFalse(list(self.book_root.glob("*.partial")))

    async def test_pruning_recovers_unleased_caches_and_preserves_user_files_and_symlinks(self) -> None:
        stale = self.book_root / ("collection-" + "a" * 64 + ".zip")
        stale.write_bytes(b"previous process cache")
        user_file = self.book_root / "collection-family.zip"
        user_file.write_bytes(b"user archive")
        external = self.root / "private.zip"
        external.write_bytes(b"external data")
        linked = self.book_root / ("collection-" + "b" * 64 + ".zip")
        linked.symlink_to(external)
        await self._deliver(download_audiobook_collection(self.book_id))
        self.assertFalse(stale.exists())
        self.assertEqual(user_file.read_bytes(), b"user archive")
        self.assertTrue(linked.is_symlink())
        self.assertEqual(external.read_bytes(), b"external data")

    async def test_prune_failure_keeps_new_download_valid(self) -> None:
        await self._deliver(download_audiobook_collection(self.book_id))
        first = next(self.book_root.glob("collection-*.zip"))
        self._revise()
        original = Path.unlink

        def denied(path: Path, missing_ok: bool = False) -> None:
            if path.resolve() == first.resolve():
                raise PermissionError("Test cache temporarily locked")
            original(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", denied), self.assertLogs(audiobook_collection._LOG, level="WARNING"):
            messages = await self._deliver(download_audiobook_collection(self.book_id))
        self.assertEqual(messages[0]["status"], 200)
        self.assertTrue(first.exists())
        await self._deliver(download_audiobook_collection(self.book_id))
        self.assertFalse(first.exists())

    async def test_http_download_preserves_ranges_and_structured_errors(self) -> None:
        application = FastAPI()
        application.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://test") as client:
            whole = await client.get(f"/api/audiobooks/{self.book_id}/exports/collection")
            part = await client.get(f"/api/audiobooks/{self.book_id}/exports/collection", headers={"Range": "bytes=0-49"})
            self.assertEqual(whole.status_code, 200)
            self.assertEqual(part.status_code, 206)
            self.assertEqual(part.content, whole.content[:50])
            self.assertEqual(part.headers["content-range"], f"bytes 0-49/{len(whole.content)}")
            self.assertEqual(part.headers["content-type"], "application/zip")
            missing = await client.get("/api/audiobooks/bad/exports/collection")
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json(), {"detail": "invalid_book_id"})

    async def test_head_sends_no_body_and_releases_snapshot(self) -> None:
        scope = _scope()
        scope["method"] = "HEAD"
        messages: list[Message] = []

        async def send(message: Message) -> None:
            messages.append(message)

        await download_audiobook_collection(self.book_id)(scope, _receive, send)
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(b"".join(message.get("body", b"") for message in messages), b"")
        self.assertFalse(audiobook_collection._ACTIVE)
        self.assertEqual(len(list(self.book_root.glob("collection-*.zip"))), 1)
        self.assertTrue(audiobooks.export_path_for(self.book_id).is_file())
        self.assertTrue(audiobooks.chapter_audio_path(self.book_id, 0).is_file())

    async def test_queued_download_survives_revision_and_releases_old_snapshot(self) -> None:
        waiting, release = asyncio.Event(), asyncio.Event()
        messages: list[Message] = []

        async def send(message: Message) -> None:
            messages.append(message)
            if message["type"] == "http.response.start":
                waiting.set()
                await release.wait()

        first_response = download_audiobook_collection(self.book_id)
        pending = asyncio.create_task(first_response(_scope(), _receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            first = next(self.book_root.glob("collection-*.zip"))
            original = first.read_bytes()
            self._revise()
            await self._deliver(download_audiobook_collection(self.book_id))
            self.assertEqual(first.read_bytes(), original)
        finally:
            release.set()
            await pending
        self.assertFalse(any(message["type"] == "http.response.pathsend" for message in messages))
        self.assertEqual(b"".join(message.get("body", b"") for message in messages), original)
        self.assertFalse(first.exists())
