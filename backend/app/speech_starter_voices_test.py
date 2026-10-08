"""Offline speech starter catalog, safe media, and independent import regression tests."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import speech_clone, speech_starter_voices, voice_profiles
from app.api import routes_speech_clone, routes_voice_profiles


class BundledSpeechStarterAssetTests(unittest.TestCase):
    def test_four_bundled_references_match_their_provenance_and_exact_transcripts(self) -> None:
        assets = Path(__file__).parent.parent / "assets" / "starter-voices"
        catalog = json.loads((assets / "catalog.json").read_text(encoding="utf-8"))
        voices = speech_starter_voices.list_starter_voices()
        self.assertEqual(len(voices), 4)
        self.assertEqual(len({voice.id for voice in voices}), 4)
        for entry in catalog["voices"]:
            with self.subTest(voice=entry["id"]):
                path = assets / entry["file_name"]
                self.assertEqual(path.resolve().parent, assets.resolve())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["audio_sha256"])
                with wave.open(str(path), "rb") as recording:
                    self.assertEqual(recording.getnchannels(), 1)
                    self.assertEqual(recording.getsampwidth(), 2)
                    self.assertEqual(recording.getframerate(), entry["sample_rate_hz"])
                    duration = recording.getnframes() / recording.getframerate()
                    self.assertGreaterEqual(duration, 3)
                    self.assertLessEqual(duration, 10)
                    self.assertAlmostEqual(duration, entry["duration_seconds"], places=6)
                    pcm = recording.readframes(recording.getnframes())
                self.assertEqual(hashlib.sha256(pcm).hexdigest(), entry["pcm_sha256"])
                samples = [sample[0] for sample in struct.iter_unpack("<h", pcm)]
                clipped = sum(sample in (-32768, 32767) for sample in samples)
                self.assertEqual(clipped, 0)
                self.assertEqual(clipped, entry["clipped_samples"])
                peak_dbfs = 20 * math.log10(max(abs(sample) for sample in samples) / 32768)
                self.assertAlmostEqual(peak_dbfs, entry["peak_dbfs"], places=3)
                transcript = assets / "transcripts" / Path(entry["transcript_path"]).name
                raw_transcript = transcript.read_bytes()
                self.assertEqual(hashlib.sha256(raw_transcript).hexdigest(), entry["transcript_sha256"])
                self.assertEqual(raw_transcript.decode("utf-8").strip(), entry["transcript"])
                self.assertRegex(entry["source_sha256"], r"^[0-9a-f]{64}$")
        for source_file in catalog["source_files"]:
            self.assertEqual(
                hashlib.sha256((assets / source_file["file_name"]).read_bytes()).hexdigest(),
                source_file["sha256"],
            )


class SpeechStarterVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        recording = io.BytesIO()
        with wave.open(recording, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 56000)
        self.audio = recording.getvalue()
        (self.assets / "p225_001.wav").write_bytes(self.audio)
        self.catalog = {
            "schema_version": 1,
            "source_url": "https://datashare.ed.ac.uk/handle/10283/3443",
            "license_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "VCTK 0.92, Yamagishi, Veaux and MacDonald, University of Edinburgh.",
            "voices": [{
                "id": "vctk-p225", "name": "VCTK p225", "language": "en", "accent": "English",
                "transcript": "An exact reference transcript.", "file_name": "p225_001.wav",
                "duration_seconds": 3.5, "sample_rate_hz": 16000,
                "audio_sha256": hashlib.sha256(self.audio).hexdigest(),
            }],
        }
        self._write_catalog()
        self.assets_patch = patch.object(speech_starter_voices, "ASSETS_ROOT", self.assets)
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles")
        self.trials_patch = patch.object(speech_clone, "TRIALS_ROOT", self.root / "trials")
        self.profiles_patch.start()
        self.trials_patch.start()
        self.assets_patch.start()
        application = FastAPI()
        application.include_router(routes_voice_profiles.router)
        application.include_router(routes_speech_clone.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.profiles_patch.stop()
        self.trials_patch.stop()
        self.assets_patch.stop()
        self.temporary.cleanup()

    def _write_catalog(self) -> None:
        (self.assets / "catalog.json").write_text(json.dumps(self.catalog), encoding="utf-8")

    async def test_catalog_is_available_without_an_engine_or_library_mutation(self) -> None:
        response = await self.client.get("/api/voice-profiles/starter-voices")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["voices"])
        self.assertFalse((self.root / "profiles").exists())
        voice = response.json()["voices"][0]
        self.assertEqual(voice["transcript"], "An exact reference transcript.")
        self.assertEqual(voice["license_name"], "CC BY 4.0")
        self.assertNotIn("file_name", voice)

    async def test_preview_serves_only_the_checked_bundled_recording(self) -> None:
        response = await self.client.get("/api/voice-profiles/starter-voices/vctk-p225/audio")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.audio)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        ranged = await self.client.get(
            "/api/voice-profiles/starter-voices/vctk-p225/audio", headers={"Range": "bytes=0-43"}
        )
        self.assertEqual(ranged.status_code, 206, ranged.text)
        self.assertEqual(ranged.content, self.audio[:44])

    async def test_import_copies_reference_and_transcript_to_an_independent_profile(self) -> None:
        response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 200, response.text)
        profile = response.json()
        self.assertEqual(profile["starter_voice_id"], "vctk-p225")
        self.assertEqual(profile["notes"], "An exact reference transcript.")
        self.assertTrue(profile["consent_confirmed"])
        copied = Path(profile["reference_audio_path"])
        self.assertEqual(copied.parent, self.root / "profiles" / profile["id"])
        self.assertEqual(copied.read_bytes(), self.audio)
        copied.write_bytes(b"a user replacement")
        self.assertEqual((self.assets / "p225_001.wav").read_bytes(), self.audio)

    async def test_repeat_import_preserves_the_user_profile_and_reference_edits(self) -> None:
        created = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(created.status_code, 200, created.text)
        original = created.json()
        await self.client.patch(
            f"/api/voice-profiles/{original['id']}",
            json={"name": "My edited narrator", "notes": "Edited transcript", "consent_confirmed": False},
        )
        Path(original["reference_audio_path"]).write_bytes(b"user-owned reference")
        repeated = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["id"], original["id"])
        self.assertEqual(repeated.json()["name"], "My edited narrator")
        self.assertEqual(repeated.json()["notes"], "Edited transcript")
        self.assertFalse(repeated.json()["consent_confirmed"])
        self.assertEqual(Path(original["reference_audio_path"]).read_bytes(), b"user-owned reference")

    async def test_concurrent_imports_create_one_profile_and_one_copy(self) -> None:
        responses = await asyncio.gather(*[
            self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import") for _ in range(8)
        ])
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len({response.json()["id"] for response in responses}), 1)
        self.assertEqual(len(voice_profiles.list_profiles()), 1)
        self.assertEqual(len(list((self.root / "profiles").glob("*/reference.wav"))), 1)

    async def test_independent_processes_import_one_profile_and_one_copy(self) -> None:
        script = """import sys
from pathlib import Path
from app import voice_profiles, speech_starter_voices
voice_profiles.PROFILES_ROOT = Path(sys.argv[1])
speech_starter_voices.ASSETS_ROOT = Path(sys.argv[2])
print(speech_starter_voices.import_starter_voice('vctk-p225').id)
"""
        environment = dict(
            os.environ,
            OPENFABRIC_CONFIG=str(self.root / "child-config.json"),
            OPENFABRIC_DATA_DIR=str(self.root / "child-library"),
            REMIQORA_CONFIG=str(self.root / "child-config.json"),
            REMIQORA_DATA_DIR=str(self.root / "child-library"),
            SEED_VC_DIR=str(self.root / "child-seed-vc"),
        )
        workers = [subprocess.Popen(
            [sys.executable, "-c", script, str(self.root / "profiles"), str(self.assets)],
            cwd=Path(__file__).parent.parent, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) for _ in range(4)]
        identifiers: set[bytes] = set()
        try:
            for worker in workers:
                output, error = await asyncio.to_thread(worker.communicate, timeout=20)
                self.assertEqual(worker.returncode, 0, error.decode())
                identifiers.add(output.strip())
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=20)
        self.assertEqual(len(identifiers), 1)
        self.assertEqual(len(voice_profiles.list_profiles()), 1)
        self.assertEqual(len(list((self.root / "profiles").glob("*/reference.wav"))), 1)

    async def test_deletion_never_removes_the_source_and_reimport_creates_a_new_copy(self) -> None:
        created = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(created.status_code, 200, created.text)
        profile = created.json()
        deleted = await self.client.delete(f"/api/voice-profiles/{profile['id']}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(Path(profile["reference_audio_path"]).exists())
        self.assertEqual((self.assets / "p225_001.wav").read_bytes(), self.audio)
        imported = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertNotEqual(imported.json()["id"], profile["id"])

    async def test_deletion_rejects_a_replaced_library_directory_symlink(self) -> None:
        created = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(created.status_code, 200, created.text)
        profile = created.json()
        directory = Path(profile["reference_audio_path"]).parent
        directory.rename(directory.with_name(f"saved-{profile['id']}"))
        directory.symlink_to(self.assets, target_is_directory=True)
        response = await self.client.delete(f"/api/voice-profiles/{profile['id']}")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "profile_storage_unavailable")
        self.assertEqual((self.assets / "p225_001.wav").read_bytes(), self.audio)
        self.assertEqual(voice_profiles.get_profile(profile["id"]).id, profile["id"])

    async def test_profile_store_rejects_a_symlinked_root_before_writing_outside(self) -> None:
        outside = self.root / "outside-library"
        outside.mkdir()
        (self.root / "profiles").symlink_to(outside, target_is_directory=True)
        response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "profile_storage_unavailable")
        self.assertEqual(list(outside.iterdir()), [])

    async def test_profile_store_rejects_a_symlinked_database_before_migrating_outside(self) -> None:
        profiles = self.root / "profiles"
        profiles.mkdir()
        outside = self.root / "outside.db"
        with sqlite3.connect(outside) as connection:
            connection.execute("CREATE TABLE original_data (value TEXT NOT NULL)")
            connection.execute("INSERT INTO original_data VALUES ('untouched')")
        original_bytes = outside.read_bytes()
        (profiles / "profiles.db").symlink_to(outside)
        with self.assertRaises(voice_profiles.VoiceProfileError) as raised:
            voice_profiles.list_profiles()
        self.assertEqual(raised.exception.code, "profile_storage_unavailable")
        self.assertEqual(raised.exception.status, 503)
        response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "profile_storage_unavailable")
        self.assertEqual(outside.read_bytes(), original_bytes)
        with sqlite3.connect(outside) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(),
                [("original_data",)],
            )
            self.assertEqual(connection.execute("SELECT value FROM original_data").fetchall(), [("untouched",)])
        self.assertEqual(list(profiles.glob("*/reference.wav")), [])

    async def test_profile_list_returns_a_structured_error_for_an_unsafe_database(self) -> None:
        profiles = self.root / "profiles"
        profiles.mkdir()
        outside = self.root / "outside.db"
        with sqlite3.connect(outside) as connection:
            connection.execute("CREATE TABLE original_data (value TEXT NOT NULL)")
        original_bytes = outside.read_bytes()
        (profiles / "profiles.db").symlink_to(outside)
        application = FastAPI()
        application.include_router(routes_voice_profiles.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/api/voice-profiles")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json(), {"detail": "profile_storage_unavailable"})
        self.assertEqual(outside.read_bytes(), original_bytes)

    async def test_unknown_or_invalid_starter_identifiers_are_not_filesystem_paths(self) -> None:
        for starter_id in ("vctk-p999", "catalog.json", "..%2Fcatalog.json", "p225_001.wav"):
            for suffix in ("audio", "import"):
                response = await self.client.request(
                    "GET" if suffix == "audio" else "POST",
                    f"/api/voice-profiles/starter-voices/{starter_id}/{suffix}",
                )
                self.assertEqual(response.status_code, 404, response.text)
        self.assertFalse((self.root / "profiles").exists())

    async def test_modified_audio_fails_before_creating_a_profile(self) -> None:
        (self.assets / "p225_001.wav").write_bytes(b"corrupt")
        for suffix in ("audio", "import"):
            response = await self.client.request(
                "GET" if suffix == "audio" else "POST",
                f"/api/voice-profiles/starter-voices/vctk-p225/{suffix}",
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], "starter_audio_unavailable")
        self.assertFalse((self.root / "profiles").exists())

    async def test_symlinks_are_rejected_even_when_the_target_is_inside_assets(self) -> None:
        source = self.assets / "p225_001.wav"
        for target in (self.root / "outside.wav", self.assets / "other.wav"):
            target.write_bytes(self.audio)
            source.unlink()
            source.symlink_to(target)
            response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], "starter_audio_unavailable")
        self.assertFalse((self.root / "profiles").exists())

    async def test_invalid_catalog_and_missing_audio_return_stable_public_codes(self) -> None:
        (self.assets / "catalog.json").write_text('{"voices": "internal bad path"}', encoding="utf-8")
        response = await self.client.get("/api/voice-profiles/starter-voices")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "starter_catalog_unavailable")
        self._write_catalog()
        (self.assets / "p225_001.wav").unlink()
        response = await self.client.get("/api/voice-profiles/starter-voices/vctk-p225/audio")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "starter_audio_unavailable")

    async def test_manifest_traversal_duplicate_ids_and_symlinks_are_rejected(self) -> None:
        invalid_catalogs = [
            {**self.catalog, "voices": [{**self.catalog["voices"][0], "file_name": "../outside.wav"}]},
            {**self.catalog, "voices": self.catalog["voices"] * 2},
            {**self.catalog, "voices": [{**self.catalog["voices"][0], "duration_seconds": 0.1}]},
        ]
        for invalid in invalid_catalogs:
            (self.assets / "catalog.json").write_text(json.dumps(invalid), encoding="utf-8")
            response = await self.client.get("/api/voice-profiles/starter-voices")
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], "starter_catalog_unavailable")
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.catalog), encoding="utf-8")
        (self.assets / "catalog.json").unlink()
        (self.assets / "catalog.json").symlink_to(outside)
        response = await self.client.get("/api/voice-profiles/starter-voices")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertFalse((self.root / "profiles").exists())

    async def test_failed_schema_upgrade_rolls_back_and_can_be_retried(self) -> None:
        profiles = self.root / "profiles"
        profiles.mkdir()
        database = profiles / "profiles.db"
        with sqlite3.connect(database) as connection:
            connection.execute("""CREATE TABLE voice_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, consent_confirmed INTEGER NOT NULL,
                reference_audio_path TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, engine_hints TEXT)""")
        original_connect = voice_profiles._connect

        def block_index(
            action: int, first: str | None, second: str | None,
            database_name: str | None, trigger_name: str | None,
        ) -> int:
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK

        def interrupted_connection() -> sqlite3.Connection:
            connection = original_connect()
            connection.set_authorizer(block_index)
            return connection

        with patch.object(voice_profiles, "_connect", side_effect=interrupted_connection):
            response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "profile_storage_unavailable")
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(voice_profiles)")}
            self.assertNotIn("starter_voice_id", columns)
        self.assertEqual(list(profiles.glob("*/reference.wav")), [])
        retry = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(retry.status_code, 200, retry.text)

    async def test_interrupted_copy_leaves_no_row_or_partial_profile(self) -> None:
        original_write = Path.write_bytes

        def interrupted_copy(path: Path, data: bytes) -> int:
            if path.parent.parent == self.root / "profiles":
                raise OSError("private filesystem details")
            return original_write(path, data)

        with patch.object(Path, "write_bytes", new=interrupted_copy):
            response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "profile_storage_unavailable")
        self.assertEqual(voice_profiles.list_profiles(), [])
        self.assertEqual(list((self.root / "profiles").glob("*/reference.wav")), [])
        response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 200, response.text)

    async def test_legacy_schema_upgrade_preserves_profiles_and_adds_unique_nullable_provenance(self) -> None:
        profiles = self.root / "profiles"
        profiles.mkdir()
        profile_id = "a" * 32
        reference = profiles / profile_id / "reference.wav"
        reference.parent.mkdir()
        reference.write_bytes(b"existing user recording")
        with sqlite3.connect(profiles / "profiles.db") as connection:
            connection.execute("""CREATE TABLE voice_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, consent_confirmed INTEGER NOT NULL,
                reference_audio_path TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, engine_hints TEXT)""")
            connection.execute(
                "INSERT INTO voice_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, "Old narrator", 0, str(reference), "Old notes", "2025-01-01", "2025-01-01", '{"mode":"legacy"}'),
            )
        response = await self.client.post("/api/voice-profiles/starter-voices/vctk-p225/import")
        self.assertEqual(response.status_code, 200, response.text)
        old = voice_profiles.get_profile(profile_id)
        self.assertEqual(old.name, "Old narrator")
        self.assertEqual(old.renderer,"local")
        self.assertIsNone(old.cloud)
        self.assertIsNone(old.starter_voice_id)
        self.assertFalse(old.consent_confirmed)
        self.assertEqual(old.engine_hints, {"mode": "legacy"})
        self.assertEqual(reference.read_bytes(), b"existing user recording")
        with sqlite3.connect(profiles / "profiles.db") as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            transcript, language = connection.execute("SELECT reference_transcript, reference_language FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
            self.assertEqual((transcript, language), ("Old notes", "en"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE voice_profiles SET starter_voice_id = 'vctk-p225' WHERE id = ?", (profile_id,)
                )

    async def test_trial_audio_survives_a_new_client_and_never_accepts_unsafe_paths(self) -> None:
        trials = self.root / "trials"
        trials.mkdir()
        trial_id = "b" * 32
        (trials / f"{trial_id}.wav").write_bytes(self.audio)
        response = await self.client.get(f"/api/speech-clone/trials/{trial_id}/audio")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.audio)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        ranged = await self.client.get(
            f"/api/speech-clone/trials/{trial_id}/audio", headers={"Range": "bytes=0-43"}
        )
        self.assertEqual(ranged.status_code, 206, ranged.text)
        self.assertEqual(ranged.content, self.audio[:44])
        for unsafe in ("missing", "../outside", "b" * 31, "b" * 33, "C" * 32):
            response = await self.client.get(f"/api/speech-clone/trials/{unsafe}/audio")
            self.assertEqual(response.status_code, 404, response.text)
        (trials / f"{trial_id}.wav").unlink()
        outside = self.root / "outside.wav"
        outside.write_bytes(b"private")
        (trials / f"{trial_id}.wav").symlink_to(outside)
        response = await self.client.get(f"/api/speech-clone/trials/{trial_id}/audio")
        self.assertEqual(response.status_code, 404, response.text)
