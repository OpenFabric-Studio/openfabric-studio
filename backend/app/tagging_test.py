"""Tagged downloads preserve selected audio and own every temporary artifact."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app import artist_settings, audio_exports, audio_version_store as store, db, tagging
from app.audio_version_contracts import AudioVersion
from app.video_media import VideoMediaError
from app.video_process import spawn_owned


class FormatTags(BaseModel):
    tags: dict[str, str]


class TaggedProbe(BaseModel):
    format: FormatTags


class TaggedDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.files = self.root / "files"
        self.files.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.child_pid_file = self.root / "child.pid"
        self.enterContext(patch.object(db, "FILES_DIR", self.files))
        self.enterContext(patch.object(db, "DB_PATH", self.root / "catalog.db"))
        self.enterContext(patch.object(db, "_db", None))
        self.enterContext(patch.object(tagging, "_TEMP_ROOT", self.scratch))
        self.enterContext(patch.object(tagging, "_capacity", asyncio.Semaphore(2)))
        self.source = self.files / "original.wav"
        self.voice = self.files / "voice.wav"
        self.write_wave(self.source, b"\x11\x01")
        self.write_wave(self.voice, b"\x22\x02")
        self.track_id = db.insert_track(model="ace_step", title="Björk 東京", lyrics="Héllo 世界", seed=42,
            duration_ms=100, wall_ms=1, params={"genre": "Jazz", "bpm": 120}, audio_path=self.source, abc_path=None)
        db.get_db().execute("UPDATE tracks SET created_at=? WHERE id=?", ("2026-03-17T23:42:11Z", self.track_id))
        db.get_db().commit()
        self.original_id = "a" * 32
        self.voice_id = "b" * 32
        self.add_version(self.original_id, "original", self.source)
        self.add_version(self.voice_id, "voice", self.voice)
        artist_settings.save_settings(artist_settings.ArtistSettings(artist="Ártist 日本"))

    def write_wave(self, path: Path, frame: bytes) -> None:
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(48000)
            writer.writeframes(frame * 4800)

    def add_version(self, identifier: str, kind: str, path: Path) -> None:
        version = AudioVersion.model_validate({"id": identifier, "track_id": self.track_id, "kind": kind,
            "status": "done", "created_at": "2026-10-01", "voice_name": "Vóice 日本" if kind == "voice" else None,
            "voice_id": "c" * 32 if kind == "voice" else None,
            "source_version_id": self.original_id if kind == "voice" else None})
        store.insert(db.get_db(), store.StoredAudioVersion(version, path, None))

    async def asyncTearDown(self) -> None:
        await tagging.shutdown()
        await audio_exports.shutdown_exports()
        if db._db is not None:
            db._db.close()

    def pcm(self, path: Path) -> bytes:
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-"])

    def tags(self, path: Path) -> dict[str, str]:
        result = subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)])
        return {key.lower(): value for key, value in TaggedProbe.model_validate_json(result).format.tags.items()}

    async def test_original_voice_and_legacy_download_use_exact_selected_source(self) -> None:
        original_before, voice_before = self.source.read_bytes(), self.voice.read_bytes()
        db.update_track_audio(self.track_id, self.voice)
        for identifier, expected in [(self.original_id, self.source), (self.voice_id, self.voice), (None, self.source)]:
            with self.subTest(identifier=identifier):
                result = await tagging.prepare_download(self.track_id, identifier)
                self.assertEqual(self.pcm(result.path), self.pcm(expected))
                tags = self.tags(result.path)
                self.assertEqual(tags["title"], "Björk 東京")
                self.assertIn("seed=42", tags["comment"])
                self.assertIn("created_at=2026-03-17T23:42:11Z", tags["comment"])
                if identifier == self.voice_id:
                    self.assertIn("Vóice 日本", tags["comment"])
                result.cleanup()
        self.assertEqual(self.source.read_bytes(), original_before)
        self.assertEqual(self.voice.read_bytes(), voice_before)
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_yue_provenance_does_not_invent_weight_license_from_model_label(self) -> None:
        db.get_db().execute("UPDATE tracks SET model='yue2' WHERE id=?", (self.track_id,))
        db.get_db().commit()
        result = await tagging.prepare_download(self.track_id, self.original_id)
        tags = self.tags(result.path)
        self.assertIn("YuE2-3B", tags["comment"])
        self.assertIn("seed=42", tags["comment"])
        self.assertIn("created_at=2026-03-17T23:42:11Z", tags["comment"])
        self.assertIn("Check the terms of the exact model weights used", tags["comment"])
        self.assertNotIn("CC BY-NC", tags["comment"])
        self.assertNotIn("non-commercial", tags["comment"])
        result.cleanup()

    async def test_real_mp3_flac_wav_tags_and_audio_are_unchanged(self) -> None:
        for extension in ("mp3", "flac", "wav"):
            with self.subTest(extension=extension):
                selected = self.files / f"selected.{extension}"
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.source), "-metadata", "comment=Earlier provenance", str(selected)], check=True)
                db.get_db().execute("UPDATE audio_versions SET audio_path=? WHERE id=?", (str(selected), self.original_id))
                db.get_db().commit()
                before = selected.read_bytes()
                result = await tagging.prepare_download(self.track_id, self.original_id,
                    options=tagging.TaggedDownloadOptions(album="Àlbum 世界", track_no=7))
                tags = self.tags(result.path)
                self.assertEqual(tags["title"], "Björk 東京")
                self.assertEqual(tags["artist"], "Ártist 日本")
                self.assertEqual(tags["album"], "Àlbum 世界")
                self.assertIn("Earlier provenance", tags["comment"])
                self.assertIn("ACE-Step 1.5", tags["comment"])
                self.assertEqual(self.pcm(result.path), self.pcm(selected))
                self.assertEqual(selected.read_bytes(), before)
                self.assertTrue(result.filename.endswith(f".{extension}"))
                if extension != "wav":
                    self.assertEqual(tags["lyrics"], "Héllo 世界")
                    self.assertEqual(tags["created_at"], "2026-03-17T23:42:11Z")
                result.cleanup()
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_empty_artist_removes_existing_source_artist_for_every_format(self) -> None:
        artist_settings.save_settings(artist_settings.ArtistSettings(artist=""))
        for extension in ("mp3", "flac", "wav"):
            with self.subTest(extension=extension):
                selected = self.files / f"artist.{extension}"
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.source), "-metadata", "artist=Source Artist", str(selected)], check=True)
                self.assertEqual(self.tags(selected)["artist"], "Source Artist")
                db.get_db().execute("UPDATE audio_versions SET audio_path=? WHERE id=?", (str(selected), self.original_id))
                db.get_db().commit()
                result = await tagging.prepare_download(self.track_id, self.original_id)
                self.assertNotIn("artist", self.tags(result.path))
                result.cleanup()

    async def test_full_large_escaped_lyrics_use_metadata_file_and_small_argv(self) -> None:
        lyrics = ("Héllo=世界;#\\" + "\n" + "[Verse]\r\n\t[End]\n") * 3000
        db.get_db().execute("UPDATE tracks SET lyrics=? WHERE id=?", (lyrics, self.track_id))
        db.get_db().commit()
        for extension in ("mp3", "flac"):
            with self.subTest(extension=extension):
                selected = self.files / f"lyrics.{extension}"
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.source), str(selected)], check=True)
                db.get_db().execute("UPDATE audio_versions SET audio_path=? WHERE id=?", (str(selected), self.original_id))
                db.get_db().commit()
                with patch("app.tagging.spawn_owned", wraps=spawn_owned) as spawned:
                    result = await tagging.prepare_download(self.track_id, self.original_id)
                self.assertEqual(self.tags(result.path)["lyrics"], lyrics)
                for call in spawned.call_args_list:
                    argv = call.args[0]
                    self.assertLess(len(subprocess.list2cmdline(argv)), 8000)
                    self.assertNotIn("-metadata", argv)
                result.cleanup()
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_invalid_and_oversized_metadata_fail_without_partial_files(self) -> None:
        for lyrics, code in [("a\x00b", "metadata_invalid"), ("x" * (1024 * 1024 + 1), "metadata_too_large")]:
            with self.subTest(code=code):
                db.get_db().execute("UPDATE tracks SET lyrics=? WHERE id=?", (lyrics, self.track_id))
                db.get_db().commit()
                with self.assertRaises(tagging.TaggingError) as caught:
                    await tagging.prepare_download(self.track_id, self.original_id)
                self.assertEqual(caught.exception.code, code)
                self.assertFalse(list(self.scratch.iterdir()))
                self.assertFalse(tagging._pending)
                self.assertFalse(tagging._processes)

    async def test_export_selects_exact_export_and_rejects_other_version(self) -> None:
        exported = await audio_exports.create_export(self.track_id, self.voice_id, "flac")
        await audio_exports._tasks[exported.id]
        selected = audio_exports.export_file(self.track_id, self.voice_id, exported.id)
        before = selected.read_bytes()
        result = await tagging.prepare_download(self.track_id, self.voice_id, exported.id)
        self.assertEqual(self.pcm(result.path), self.pcm(selected))
        self.assertEqual(result.path.suffix, ".flac")
        self.assertEqual(selected.read_bytes(), before)
        result.cleanup()
        self.voice.unlink()
        result = await tagging.prepare_download(self.track_id, self.voice_id, exported.id)
        self.assertEqual(self.pcm(result.path), self.pcm(selected))
        result.cleanup()
        with self.assertRaises(tagging.TaggingError) as caught:
            await tagging.prepare_download(self.track_id, self.original_id, exported.id)
        self.assertEqual(caught.exception.code, "export_not_found")

    async def test_invalid_ids_and_symlink_escape_never_run_ffmpeg(self) -> None:
        for identifier in ["../other", "d" * 32]:
            with self.subTest(identifier=identifier), self.assertRaises(tagging.TaggingError):
                await tagging.prepare_download(self.track_id, identifier)
        for value in [-1, 0]:
            with self.subTest(value=value), self.assertRaises(tagging.TaggingError):
                await tagging.prepare_download(value, self.original_id)
        external = self.root / "outside.wav"
        external.write_bytes(self.source.read_bytes())
        self.source.unlink()
        self.source.symlink_to(external)
        with patch("app.tagging.spawn_owned") as spawned, self.assertRaises(tagging.TaggingError):
            await tagging.prepare_download(self.track_id, self.original_id)
        spawned.assert_not_called()
        self.assertFalse(list(self.scratch.iterdir()))
        for data in [{"album": "x" * 121}, {"track_no": 0}, {"track_no": True}, {"extra": 1}]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                tagging.TaggedDownloadOptions.model_validate(data)

    async def test_missing_ffmpeg_and_encoder_failure_leave_no_temporary_files(self) -> None:
        with patch("app.tagging.tool", side_effect=VideoMediaError("ffmpeg_missing")):
            with self.assertRaises(tagging.TaggingError) as caught:
                await tagging.prepare_download(self.track_id, self.original_id)
        self.assertEqual(caught.exception.code, "ffmpeg_missing")
        self.voice.write_bytes(b"broken audio")
        with self.assertRaises(tagging.TaggingError) as failed:
            await tagging.prepare_download(self.track_id, self.voice_id)
        self.assertEqual(failed.exception.code, "tagging_failed")
        self.assertFalse(list(self.scratch.iterdir()))

    async def fake_worker(self, argv: list[str], *, receipt_path: Path, stdout: int) -> asyncio.subprocess.Process:
        self.child_pid_file.unlink(missing_ok=True)
        script = ("import subprocess,sys,time; from pathlib import Path; "
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
                  f"Path({str(self.child_pid_file)!r}).write_text(str(p.pid)); "
                  "print(p.pid,flush=True); time.sleep(60)")
        return await spawn_owned([sys.executable, "-c", script], receipt_path=receipt_path, stdout=stdout)

    async def assert_descendant_terminated(self) -> None:
        if sys.platform == "win32":
            return  # Platform typing is checked separately; ps is POSIX-only.
        self.assertTrue(self.child_pid_file.is_file(), "test worker must spawn a descendant")
        pid = self.child_pid_file.read_text()
        for _ in range(200):
            state = subprocess.run(["ps", "-p", pid, "-o", "stat="], text=True, capture_output=True, check=False).stdout.strip()
            if not state or state.startswith("Z"):
                return
            await asyncio.sleep(0.01)
        self.fail(f"owned descendant {pid} is still running")

    async def test_timeout_cancel_shutdown_and_output_overflow_clean_owned_workers(self) -> None:
        # The real supervisor receives EOF or process-group termination; tests never spawn an unowned engine.
        with patch("app.tagging.spawn_owned", side_effect=self.fake_worker), patch.object(tagging, "_TIMEOUT", 0.5):
            with self.assertRaises(tagging.TaggingError) as timed:
                await tagging.prepare_download(self.track_id, self.original_id)
            self.assertEqual(timed.exception.code, "tagging_timeout")
        await self.assert_descendant_terminated()
        with patch("app.tagging.spawn_owned", side_effect=self.fake_worker):
            task = asyncio.create_task(tagging.prepare_download(self.track_id, self.original_id))
            for _ in range(200):
                if tagging._processes and self.child_pid_file.is_file():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(tagging._processes)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await self.assert_descendant_terminated()
            task = asyncio.create_task(tagging.prepare_download(self.track_id, self.original_id))
            for _ in range(200):
                if tagging._processes and self.child_pid_file.is_file():
                    break
                await asyncio.sleep(0.01)
            await tagging.shutdown()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await self.assert_descendant_terminated()
        async def overflow(argv: list[str], *, receipt_path: Path, stdout: int) -> asyncio.subprocess.Process:
            return await spawn_owned([sys.executable, "-c", "print('x'*1100000)"], receipt_path=receipt_path, stdout=stdout)
        with patch("app.tagging.spawn_owned", side_effect=overflow):
            with self.assertRaises(tagging.TaggingError) as oversized:
                await tagging.prepare_download(self.track_id, self.original_id)
            self.assertEqual(oversized.exception.code, "tagging_output_invalid")
        self.assertFalse(tagging._processes)
        self.assertFalse(tagging._tasks)
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_response_send_failure_and_cancelled_shutdown_release_completed_copy(self) -> None:
        result = await tagging.prepare_download(self.track_id, self.original_id)
        response = tagging.TaggedFileResponse(result)
        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}
        async def send(message: dict[str, object]) -> None:
            raise OSError("client disconnected")
        with self.assertRaises(OSError):
            await response({"type": "http", "method": "GET", "extensions": {}, "headers": []}, receive, send)
        self.assertFalse(result.path.exists())
        result = await tagging.prepare_download(self.track_id, self.original_id)
        with patch("app.tagging.spawn_owned", side_effect=self.fake_worker):
            task = asyncio.create_task(tagging.prepare_download(self.track_id, self.voice_id))
            for _ in range(200):
                if tagging._processes and self.child_pid_file.is_file():
                    break
                await asyncio.sleep(0.01)
            closing = asyncio.create_task(tagging.shutdown())
            await asyncio.sleep(0)
            closing.cancel()
            try:
                await closing
            except asyncio.CancelledError:
                pass
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(result.path.exists())
        self.assertFalse(tagging._pending)
        self.assertFalse(tagging._processes)
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_response_streams_bytes_before_releasing_copy_with_pathsend_extension(self) -> None:
        result = await tagging.prepare_download(self.track_id, self.original_id)
        expected = result.path.read_bytes()
        messages: list[dict[str, object]] = []
        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}
        async def send(message: dict[str, object]) -> None:
            messages.append(message)
        response = tagging.TaggedFileResponse(result)
        await response({"type": "http", "method": "GET", "headers": [], "extensions": {"http.response.pathsend": {}}}, receive, send)
        self.assertFalse(any(message["type"] == "http.response.pathsend" for message in messages))
        self.assertEqual(b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body"), expected)
        self.assertFalse(result.path.exists())

    async def test_http_disconnect_cancels_preparation_and_cleans_worker(self) -> None:
        from app.api.tagged_download_response import tagged_download_response
        disconnected = asyncio.Event()
        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"} if disconnected.is_set() else {"type": "http.request", "body": b"", "more_body": False}
        request = Request({"type": "http", "headers": []}, receive=receive)
        with patch("app.tagging.spawn_owned", side_effect=self.fake_worker):
            task = asyncio.create_task(tagged_download_response(self.track_id, self.voice_id, None,
                tagging.TaggedDownloadOptions(), request))
            for _ in range(200):
                if tagging._processes and self.child_pid_file.is_file():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(tagging._processes)
            disconnected.set()
            with self.assertRaises(HTTPException) as caught:
                await task
        self.assertEqual(caught.exception.detail, "download_cancelled")
        self.assertFalse(tagging._processes)
        self.assertFalse(tagging._pending)
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_api_settings_and_download_cleanup_have_stable_errors(self) -> None:
        from app.api.routes_audio_versions import router as versions_router
        from app.api.routes_audio_exports import router as exports_router
        from app.api.routes_settings import router as settings_router
        app = FastAPI()
        app.include_router(versions_router)
        app.include_router(exports_router)
        app.include_router(settings_router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.get('/api/settings')).json(), {"artist": "Ártist 日本", "album": ""})
            updated = await client.put('/api/settings', json={"artist": "  Updated  \n Artist "})
            self.assertEqual(updated.json(), {"artist": "Updated Artist", "album": ""})
            self.assertEqual((await client.put('/api/settings', json={"artist": "x" * 121})).status_code, 422)
            response = await client.get(f'/api/tracks/{self.track_id}/versions/{self.voice_id}/download?album=Album&track_no=7')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(list(self.scratch.iterdir()))
            self.assertFalse(tagging._pending)
            with patch("app.tagging.tool", side_effect=VideoMediaError("ffmpeg_missing")):
                failed = await client.get(f'/api/tracks/{self.track_id}/versions/{self.voice_id}/download')
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(failed.json(), {"detail": "ffmpeg_missing"})

    async def test_completed_download_stops_watcher_when_request_scope_absorbs_cancellation(self) -> None:
        from app.api import tagged_download_response as download_api

        directory = self.scratch / "completed"
        directory.mkdir()
        target = directory / "download.wav"
        target.write_bytes(self.voice.read_bytes())
        completed = tagging.TaggedAudioDownload("d" * 32, directory, target, "song.wav")
        tagging._pending[completed.identifier] = completed
        release = asyncio.Event()

        async def prepare(
            _track_id: int, _version_id: str | None, _export_id: str | None,
            *, options: tagging.TaggedDownloadOptions,
        ) -> tagging.TaggedAudioDownload:
            # Preparation finishes just before the watcher enters Request's
            # cancelled scope. Both cancel the same receive Future before its
            # task wakes, so AnyIO can absorb the watcher cancellation.
            return completed

        async def receive() -> dict[str, object]:
            if release.is_set():
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request({"type": "http", "headers": []}, receive=receive)
        with patch.object(download_api, "prepare_download", side_effect=prepare):
            response_task = asyncio.create_task(download_api.tagged_download_response(
                self.track_id, self.voice_id, None, tagging.TaggedDownloadOptions(), request,
            ))
            try:
                response = await asyncio.wait_for(asyncio.shield(response_task), timeout=2)
                self.assertIs(response.download, completed)
                self.assertTrue(target.is_file())
            finally:
                # Bound the regression without leaving the old watcher behind
                # when this test fails against the broken implementation.
                release.set()
                if not response_task.done():
                    response_task.cancel()
                await asyncio.wait_for(asyncio.gather(response_task, return_exceptions=True), timeout=2)
                completed.cleanup()
        self.assertFalse(tagging._pending)
        self.assertFalse(list(self.scratch.iterdir()))

    async def test_request_cancellation_stops_scope_watcher_and_releases_completed_copy(self) -> None:
        from app.api import tagged_download_response as download_api

        directory = self.scratch / "completed"
        directory.mkdir()
        target = directory / "download.wav"
        target.write_bytes(self.voice.read_bytes())
        completed = tagging.TaggedAudioDownload("d" * 32, directory, target, "song.wav")
        tagging._pending[completed.identifier] = completed
        release = asyncio.Event()
        response_task: asyncio.Task[tagging.TaggedFileResponse] | None = None
        scheduled = False

        async def prepare(
            _track_id: int, _version_id: str | None, _export_id: str | None,
            *, options: tagging.TaggedDownloadOptions,
        ) -> tagging.TaggedAudioDownload:
            return completed

        async def receive() -> dict[str, object]:
            nonlocal scheduled
            if release.is_set():
                return {"type": "http.disconnect"}
            if not scheduled:
                if response_task is None:
                    raise RuntimeError("response task was not registered")
                scheduled = True
                asyncio.get_running_loop().call_soon(response_task.cancel)
            await asyncio.Event().wait()
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request({"type": "http", "headers": []}, receive=receive)
        with patch.object(download_api, "prepare_download", side_effect=prepare):
            response_task = asyncio.create_task(download_api.tagged_download_response(
                self.track_id, self.voice_id, None, tagging.TaggedDownloadOptions(), request,
            ))
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(response_task), timeout=2)
                self.assertFalse(target.exists())
                self.assertFalse(tagging._pending)
            finally:
                release.set()
                if not response_task.done():
                    response_task.cancel()
                await asyncio.wait_for(asyncio.gather(response_task, return_exceptions=True), timeout=2)
                completed.cleanup()


class TaggingMetadataTests(unittest.TestCase):
    def test_genre_compounds_and_ambiguous_ordinary_words(self) -> None:
        self.assertEqual(tagging.guess_genre({"style": "deep house, warm vocals"}), "Deep House")
        self.assertEqual(tagging.guess_genre({"caption": "gypsy jazz with acoustic guitars"}), "Jazz")
        self.assertEqual(tagging.guess_genre({"style": "house"}), "House")
        self.assertIsNone(tagging.guess_genre({"prompt": "A house on a hill by a country road"}))
        self.assertIsNone(tagging.guess_genre({"caption": "gentle brass and a singer"}))
        self.assertIsNone(tagging.guess_genre({"style": ["house"]}))
        self.assertEqual(tagging.guess_genre({"genre": "folk", "style": "deep house"}), "Folk")
        self.assertEqual(tagging.guess_genre({"genre": "Personal genre", "style": "jazz"}), "Personal genre")
        self.assertIsNone(tagging.guess_genre({}, "A house on a hill"))
