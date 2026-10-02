"""Exports use private immutable sources and real CPU encoders."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app import audio_exports as exports
from app import db
from app.audio_encoding import AudioEncodingSettings
from app.video_process import WorkerIdentity, WorkerOutputError


class AudioExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, "FILES_DIR", self.root / "files"))
        self.enterContext(patch.object(db, "DB_PATH", self.root / "catalog.db"))
        self.enterContext(patch.object(db, "_db", None))
        self.source = self.root / "files" / "source.wav"
        self.source.parent.mkdir()
        with wave.open(str(self.source), "wb") as writer:
            writer.setnchannels(2)
            writer.setsampwidth(2)
            writer.setframerate(48000)
            writer.writeframes(b"\x11\x01\x11\x01" * 4800)
        self.original = self.source.read_bytes()
        cursor = db.get_db().execute(
            "INSERT INTO tracks(model,created_at,title,lyrics,params_json,audio_path) VALUES('ace_step','2026-10-01','Fixture','','{}',?)",
            (str(self.source),),
        )
        db.get_db().commit()
        self.track_id = cursor.lastrowid
        self.assertIsNotNone(self.track_id)
        self.version_id = "a" * 32
        self.enterContext(
            patch("app.audio_exports.resolve_source", return_value=self.source)
        )
        self.enterContext(patch.object(exports, "_capacity", asyncio.Semaphore(2)))
        self.enterContext(patch.object(exports, "_lock", asyncio.Lock()))

    async def asyncTearDown(self) -> None:
        await exports.shutdown_exports()
        if db._db is not None:
            db._db.close()

    async def done(self, identifier: str) -> exports.AudioExportResponse:
        for _ in range(400):
            response = exports.get_export(self.track_id, self.version_id, identifier)
            if response.status not in {"queued", "running"}:
                return response
            await asyncio.sleep(0.01)
        self.fail("export did not finish")

    async def test_real_encoders_capture_settings_preserve_source_and_reuse(
        self,
    ) -> None:
        for format, codec, bits in [
            ("mp3", "mp3", 0),
            ("wav", "pcm_s24le", 24),
            ("flac", "flac", 24),
        ]:
            with self.subTest(format=format):
                job = await exports.create_export(
                    self.track_id, self.version_id, format
                )
                result = await self.done(job.id)
                self.assertEqual(result.status, "done", result.error_code)
                path = exports.export_file(self.track_id, self.version_id, job.id)
                probe = json.loads(
                    await asyncio.to_thread(
                        subprocess.check_output,
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_streams",
                            "-of",
                            "json",
                            str(path),
                        ],
                    )
                )["streams"][0]
                self.assertEqual(probe["codec_name"], codec)
                self.assertEqual(probe["sample_rate"], "48000")
                self.assertEqual(probe["channels"], 2)
                if bits:
                    self.assertEqual(
                        int(
                            probe.get("bits_per_raw_sample") or probe["bits_per_sample"]
                        ),
                        bits,
                    )
                else:
                    self.assertEqual(probe["bit_rate"], "320000")
                duplicate = await exports.create_export(
                    self.track_id, self.version_id, format
                )
                self.assertEqual(duplicate.id, job.id)
        self.assertEqual(self.source.read_bytes(), self.original)

    async def test_queued_cancel_and_metadata_failure_do_not_leak_ownership(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        result = await exports.cancel_export(self.track_id, self.version_id, job.id)
        self.assertEqual(result.status, "cancelled")
        self.assertFalse(exports.work_busy())
        with (
            patch(
                "app.audio_exports.write_object",
                side_effect=OSError("private path disk full"),
            ),
            self.assertRaises(exports.AudioExportError) as caught,
        ):
            await exports.create_export(self.track_id, self.version_id, "flac")
        self.assertEqual(caught.exception.code, "export_write_failed")
        self.assertFalse(exports.work_busy())

    async def test_recovery_adopts_atomic_output_after_interrupted_publication(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        self.assertEqual((await self.done(job.id)).status, "done")
        document = exports.load_document(self.track_id, job.id)
        document.status = "running"
        exports.save_document(document)
        await exports.recover_exports()
        result = exports.get_export(self.track_id, self.version_id, job.id)
        self.assertEqual(result.status, "done")
        self.assertTrue(
            exports.export_file(self.track_id, self.version_id, job.id).is_file()
        )

    async def test_primary_promotion_is_guarded_against_late_voice_switch(self) -> None:
        job = await exports.create_export(
            self.track_id, self.version_id, "flac", update_default=True
        )
        voice = self.root / "files" / "voice.wav"
        voice.write_bytes(self.original)
        db.get_db().execute(
            "UPDATE tracks SET audio_path=? WHERE id=?", (str(voice), self.track_id)
        )
        db.get_db().commit()
        self.assertEqual((await self.done(job.id)).status, "done")
        self.assertEqual(db.get_track(self.track_id)["audio_path"], str(voice))

    async def test_deletion_reservation_rejects_new_jobs_and_drains(self) -> None:
        async with exports.protect_track_exports_removal(self.track_id):
            with self.assertRaises(exports.AudioExportError) as caught:
                await exports.create_export(self.track_id, self.version_id, "wav")
            self.assertEqual(caught.exception.code, "track_busy")
        self.assertFalse(exports.work_busy())

    async def test_symlink_output_and_invalid_identifiers_are_rejected(self) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await self.done(job.id)
        path = exports.export_file(self.track_id, self.version_id, job.id)
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaises(exports.AudioExportError):
            exports.export_file(self.track_id, self.version_id, job.id)
        with self.assertRaises(exports.AudioExportError):
            exports.get_export(self.track_id, self.version_id, "../outside")

    async def test_api_create_list_download_and_wrong_version_are_bounded(self) -> None:
        from app.api import routes_audio_exports

        app = FastAPI()
        app.include_router(routes_audio_exports.router)
        prefix = f"/api/tracks/{self.track_id}/versions/{self.version_id}/exports"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(prefix, json={"format": "wav"})
            self.assertEqual(response.status_code, 200)
            identifier = response.json()["id"]
            await self.done(identifier)
            listing = await client.get(prefix)
            self.assertEqual(len(listing.json()["exports"]), 1)
            audio = await client.get(f"{prefix}/{identifier}/audio")
            self.assertEqual(audio.status_code, 200)
            self.assertTrue(audio.content.startswith(b"RIFF"))
            wrong = await client.get(
                f"/api/tracks/{self.track_id}/versions/{'b' * 32}/exports/{identifier}/audio"
            )
            self.assertEqual(wrong.status_code, 404)
            invalid = await client.post(
                prefix, json={"format": "wav", "unknown": "/private/file"}
            )
            self.assertEqual(invalid.status_code, 422)

    async def test_dedupe_adds_primary_promotion_to_queued_export_and_retry_preserves_guard(
        self,
    ) -> None:
        manual = await exports.create_export(self.track_id, self.version_id, "wav")
        automatic = await exports.create_export(
            self.track_id, self.version_id, "wav", update_default=True
        )
        self.assertEqual(manual.id, automatic.id)
        self.assertEqual((await self.done(manual.id)).status, "done")
        self.assertEqual(
            db.get_track(self.track_id)["audio_path"],
            str(exports.export_file(self.track_id, self.version_id, manual.id)),
        )
        failed = await exports.create_export(
            self.track_id, self.version_id, "flac", update_default=True
        )
        await exports.cancel_export(self.track_id, self.version_id, failed.id)
        document = exports.load_document(self.track_id, failed.id)
        expected = document.expected_primary
        voice = self.root / "files" / "voice.wav"
        voice.write_bytes(self.original)
        db.get_db().execute(
            "UPDATE tracks SET audio_path=? WHERE id=?", (str(voice), self.track_id)
        )
        db.get_db().commit()
        retry = await exports.retry_export(self.track_id, self.version_id, failed.id)
        self.assertEqual(
            exports.load_document(self.track_id, retry.id).expected_primary, expected
        )
        self.assertEqual((await self.done(retry.id)).status, "done")
        self.assertEqual(db.get_track(self.track_id)["audio_path"], str(voice))

    async def test_completed_artifact_recovers_when_terminal_metadata_write_failed(
        self,
    ) -> None:
        def fail_publication(document: exports.ExportDocument) -> None:
            raise exports.AudioExportError("export_write_failed")

        with patch("app.audio_exports._publish", side_effect=fail_publication):
            job = await exports.create_export(self.track_id, self.version_id, "wav")
            self.assertEqual((await self.done(job.id)).status, "failed")
        self.assertTrue(
            exports._artifact(exports.load_document(self.track_id, job.id)).exists()
        )
        await exports.recover_exports()
        self.assertEqual(
            exports.get_export(self.track_id, self.version_id, job.id).status, "done"
        )

    async def test_cancelled_job_stays_cancelled_after_restart(self) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await exports.cancel_export(self.track_id, self.version_id, job.id)
        await exports.recover_exports()
        self.assertEqual(
            exports.get_export(self.track_id, self.version_id, job.id).status,
            "cancelled",
        )

    async def test_captured_settings_and_remaining_real_bit_depths(self) -> None:
        profiles = AudioEncodingSettings()
        for format, bits in [("wav", 16), ("wav", 32), ("flac", 16), ("mp3", 0)]:
            profile = (
                profiles.wav.model_copy(
                    update={"bit_depth": bits, "sample_rate": 44100, "channels": 1}
                )
                if format == "wav"
                else (
                    profiles.flac.model_copy(
                        update={
                            "bit_depth": bits,
                            "sample_rate": 44100,
                            "channels": 1,
                            "compression_level": 8,
                        }
                    )
                    if format == "flac"
                    else profiles.mp3.model_copy(
                        update={
                            "mode": "vbr",
                            "vbr_quality": 0,
                            "sample_rate": 44100,
                            "channels": 1,
                        }
                    )
                )
            )
            settings = profiles.model_copy(update={format: profile})
            with patch("app.audio_exports.load_settings", return_value=settings):
                job = await exports.create_export(
                    self.track_id, self.version_id, format
                )
            with patch("app.audio_exports.load_settings", return_value=profiles):
                result = await self.done(job.id)
            self.assertEqual(result.status, "done", result.error_code)
            self.assertEqual(result.settings, settings)
            output = exports.export_file(self.track_id, self.version_id, job.id)
            probe = json.loads(
                await asyncio.to_thread(
                    subprocess.check_output,
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-of",
                        "json",
                        str(output),
                    ],
                )
            )["streams"][0]
            self.assertEqual(probe["sample_rate"], "44100")
            self.assertEqual(probe["channels"], 1)
            if bits:
                self.assertEqual(
                    int(probe.get("bits_per_raw_sample") or probe["bits_per_sample"]),
                    bits,
                )
            if bits == 32:
                self.assertEqual(probe["codec_name"], "pcm_f32le")

    async def test_unverified_worker_remains_blocked_across_repeated_recovery(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await exports.cancel_export(self.track_id, self.version_id, job.id)
        document = exports.load_document(self.track_id, job.id)
        document.worker = WorkerIdentity(
            pid=999999,
            token="b" * 32,
            receipt=str(exports._directory(self.track_id, job.id) / "worker.json"),
        )
        exports.save_document(document)
        with patch(
            "app.audio_exports.terminate_verified", new=AsyncMock(return_value=False)
        ) as verify:
            await exports.recover_exports()
            exports._unverified.clear()
            await exports.recover_exports()
            self.assertTrue(exports.work_busy())
            self.assertEqual(verify.await_count, 2)
            with self.assertRaises(exports.AudioExportError):
                async with exports.protect_track_exports_removal(self.track_id):
                    self.fail("unsafe deletion admitted")
        exports._unverified.clear()
        document.worker = None
        exports.save_document(document)

    async def test_real_cpu_child_retained_after_kill_failure_and_retry_drains(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await exports.cancel_export(self.track_id, self.version_id, job.id)
        document = exports.load_document(self.track_id, job.id)
        task = asyncio.create_task(
            exports._command(
                document, [sys.executable, "-c", "import time; time.sleep(60)"], 90
            )
        )
        for _ in range(200):
            if document.id in exports._processes:
                break
            await asyncio.sleep(0.01)
        proc = exports._processes[document.id]
        with patch(
            "app.audio_exports.kill_process_tree",
            new=AsyncMock(side_effect=OSError("cannot kill")),
        ):
            task.cancel()
            with self.assertRaises(OSError):
                await task
        self.assertIsNone(proc.returncode)
        self.assertIs(exports._processes.get(job.id), proc)
        with self.assertRaises(exports.AudioExportError):
            await exports.retry_export(self.track_id, self.version_id, job.id)
        await exports.cancel_export(self.track_id, self.version_id, job.id)
        self.assertIsNotNone(proc.returncode)
        self.assertFalse(exports.work_busy())

    async def test_changed_source_and_oversized_child_output_cannot_publish(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        self.source.write_bytes(self.original + b"changed")
        result = await self.done(job.id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "source_changed")
        document = exports.load_document(self.track_id, job.id)
        with self.assertRaises(WorkerOutputError):
            await asyncio.wait_for(
                exports._command(
                    document,
                    [
                        sys.executable,
                        "-c",
                        'import sys; sys.stdout.buffer.write(b"x"*(2*1024*1024)); sys.stdout.flush()',
                    ],
                    10,
                ),
                15,
            )
        self.assertFalse(exports.work_busy())

    async def test_corrupted_record_blocks_deletion_instead_of_discarding_proof(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await exports.cancel_export(self.track_id, self.version_id, job.id)
        path = exports._directory(self.track_id, job.id) / "export.json"
        saved = path.read_bytes()
        path.write_text("{broken")
        with self.assertRaises(exports.AudioExportError) as caught:
            async with exports.protect_track_exports_removal(self.track_id):
                self.fail("deletion must refuse unknown process proof")
        self.assertEqual(caught.exception.code, "export_record_invalid")
        path.write_bytes(saved)

    async def test_modified_completed_export_is_not_served_or_reused(self) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await self.done(job.id)
        path = exports.export_file(self.track_id, self.version_id, job.id)
        path.write_bytes(b"corrupted audio")
        with self.assertRaises(exports.AudioExportError):
            exports.export_file(self.track_id, self.version_id, job.id)
        new = await exports.create_export(self.track_id, self.version_id, "wav")
        self.assertNotEqual(job.id, new.id)
        self.assertEqual((await self.done(new.id)).status, "done")

    async def test_automatic_save_retry_preserves_prior_primary_guard(self) -> None:
        first = await exports.create_export(
            self.track_id, self.version_id, "flac", update_default=True
        )
        await exports.cancel_export(self.track_id, self.version_id, first.id)
        failed = exports.load_document(self.track_id, first.id)
        failed.status = "failed"
        failed.error_code = "encode_failed"
        exports.save_document(failed)
        voice = self.root / "files" / "voice.wav"
        voice.write_bytes(self.original)
        db.get_db().execute(
            "UPDATE tracks SET audio_path=? WHERE id=?", (str(voice), self.track_id)
        )
        db.get_db().commit()
        retried = await exports.create_export(
            self.track_id, self.version_id, "flac", update_default=True
        )
        self.assertNotEqual(retried.id, first.id)
        self.assertEqual(
            exports.load_document(self.track_id, retried.id).expected_primary,
            str(self.source),
        )
        self.assertEqual((await self.done(retried.id)).status, "done")
        self.assertEqual(db.get_track(self.track_id)["audio_path"], str(voice))

    async def test_recovery_rejects_different_valid_audio_in_completed_export(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "wav")
        await self.done(job.id)
        path = exports.export_file(self.track_id, self.version_id, job.id)
        await asyncio.to_thread(
            subprocess.check_call,
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=900:duration=0.1",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s24le",
                str(path),
            ],
        )
        with self.assertRaises(exports.AudioExportError):
            exports.export_file(self.track_id, self.version_id, job.id)
        await exports.recover_exports()
        result = exports.get_export(self.track_id, self.version_id, job.id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "export_modified")
        with self.assertRaises(exports.AudioExportError):
            exports.export_file(self.track_id, self.version_id, job.id)

    async def test_byte_identical_copied_export_recovers_after_file_identity_changes(
        self,
    ) -> None:
        job = await exports.create_export(self.track_id, self.version_id, "flac")
        await self.done(job.id)
        path = exports.export_file(self.track_id, self.version_id, job.id)
        before = path.read_bytes()
        temporary = path.with_suffix(".moved")
        temporary.write_bytes(before)
        temporary.replace(path)
        self.assertFalse(
            exports._output_current(exports.load_document(self.track_id, job.id))
        )
        await exports.recover_exports()
        self.assertEqual(
            exports.get_export(self.track_id, self.version_id, job.id).status, "done"
        )
        self.assertEqual(
            exports.export_file(self.track_id, self.version_id, job.id).read_bytes(),
            before,
        )
