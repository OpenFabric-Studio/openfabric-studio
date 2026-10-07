"""Persistent project revisions and path boundaries use isolated libraries."""

from __future__ import annotations
import asyncio, io, os, shutil, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch


class VideoProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import video_projects as p

        self.p = p
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.audio = self.root / "song.wav"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=5",
                str(self.audio),
            ],
            check=True,
        )
        self.enterContext(patch.object(p, "DATA_DIR", self.root))
        self.enterContext(
            patch.object(
                p.db,
                "get_track",
                return_value={
                    "id": 1,
                    "title": "Song",
                    "audio_path": str(self.audio),
                    "duration_ms": 5000,
                },
            )
        )

    async def test_revision_conflict_preserves_previous_edit_and_reload(self) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
        )

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        edited = self.p.update(
            project.id,
            UpdateVideoProjectRequest(revision=project.revision, name="Saved"),
        )
        with self.assertRaises(self.p.VideoProjectError) as error:
            self.p.update(
                project.id,
                UpdateVideoProjectRequest(revision=project.revision, name="Lost"),
            )
        self.assertEqual(error.exception.code, "revision_conflict")
        self.assertEqual(self.p.get(project.id).name, "Saved")
        self.assertGreater(edited.revision, project.revision)

    async def test_get_is_read_only_and_detects_changed_source(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        path = self.p.project_dir(project.id) / "project.json"
        before = path.read_bytes()
        self.audio.write_bytes(b"changed")
        self.assertTrue(self.p.get(project.id).source_changed)
        self.assertEqual(path.read_bytes(), before)

    async def test_source_read_access_time_does_not_prevent_project_creation(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest

        # Reading a stable source can update atime; it is not a content mutation.
        os.utime(self.audio, ns=(1, self.audio.stat().st_mtime_ns))
        with patch("app.video_jobs._probe_duration", return_value=5):
            project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        self.assertFalse(project.source_changed)
        self.assertEqual(self.p.load(project.id).source.sha256, self.p.file_hash(self.audio))

    async def test_source_replaced_during_probe_does_not_bind_old_duration(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest

        async def replaced_duration(path: Path) -> float:
            replacement = path.with_name("replacement.wav")
            replacement.write_bytes(b"a different source")
            replacement.replace(path)
            return 5

        with patch("app.video_jobs._probe_duration", side_effect=replaced_duration):
            with self.assertRaises(self.p.VideoProjectError) as error:
                await self.p.create(CreateVideoProjectRequest(track_id=1))
        self.assertEqual(error.exception.code, "source_changed")
        self.assertEqual(self.p.list_projects(), [])

    async def test_symlink_escape_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.p.projects_root().mkdir()
        self.p.projects_root().joinpath("a" * 32).symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaises(self.p.VideoProjectError):
            self.p.project_dir("a" * 32)

    async def test_duplicate_preserves_draft_without_reusing_outputs(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest, VideoRevisionRequest

        project = await self.p.create(CreateVideoProjectRequest(track_id=1, seed=123))
        clone = self.p.duplicate(
            project.id, VideoRevisionRequest(revision=project.revision)
        )
        self.assertNotEqual(clone.id, project.id)
        self.assertEqual(clone.seed, 123)
        self.assertEqual(clone.file_url, "")

    async def test_reference_formats_are_normalized_and_survive_duplication(self) -> None:
        from fastapi import UploadFile
        from PIL import Image
        from app.video_contracts import CreateVideoProjectRequest, VideoRevisionRequest

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        for image_format, extension in (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")):
            buffer = io.BytesIO()
            Image.new("RGB", (80, 60), (30, 50, 90)).save(buffer, image_format)
            payload = buffer.getvalue()
            upload = UploadFile(io.BytesIO(payload), filename=f"reference.{extension}")
            project = await self.p.upload_reference(project.id, project.revision, upload)
            reference = project.references[-1]
            self.assertEqual((reference.width, reference.height), (80, 60))
            self.assertEqual(reference.bytes, len(payload))
            self.assertTrue(upload.file.closed)
            normalized = self.p.reference_file(project.id, reference.id)
            self.assertEqual(normalized.with_suffix('.original').read_bytes(), payload)
            self.assertTrue(normalized.with_suffix('.normalization.json').is_file())
            with Image.open(self.p.reference_file(project.id, reference.id)) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (80, 60))

        duplicate = self.p.duplicate(project.id, VideoRevisionRequest(revision=project.revision))
        for original, copied in zip(project.references, duplicate.references, strict=True):
            self.assertIn(duplicate.id, copied.url)
            self.assertEqual(
                self.p.reference_file(project.id, original.id).read_bytes(),
                self.p.reference_file(duplicate.id, copied.id).read_bytes(),
            )
            self.assertEqual(
                self.p.reference_file(project.id, original.id).with_suffix('.original').read_bytes(),
                self.p.reference_file(duplicate.id, copied.id).with_suffix('.original').read_bytes(),
            )
        self.assertEqual(list(self.p.project_dir(project.id).glob("*.upload")), [])

    async def test_reference_path_rejection_closes_upload_and_releases_owner(self) -> None:
        from fastapi import UploadFile
        from app.video_contracts import CreateVideoProjectRequest

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        outside = self.root / "outside"
        outside.mkdir()
        (self.p.project_dir(project.id) / "references").symlink_to(outside, target_is_directory=True)
        upload = UploadFile(io.BytesIO(b"untrusted payload"), filename="reference.png")
        with self.assertRaises(self.p.VideoProjectError) as failure:
            await self.p.upload_reference(project.id, project.revision, upload)
        self.assertEqual(failure.exception.code, "not_found")
        self.assertTrue(upload.file.closed)
        self.assertNotIn(project.id, self.p.reference_project_ids())
        self.assertEqual(list(outside.iterdir()), [])

    async def test_reference_revision_conflict_removes_only_unpublished_upload(self) -> None:
        from fastapi import UploadFile
        from PIL import Image
        from app.video_contracts import CreateVideoProjectRequest, UpdateVideoProjectRequest

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        saved = self.p.update(project.id, UpdateVideoProjectRequest(revision=project.revision, name="Saved"))
        buffer = io.BytesIO()
        Image.new("RGB", (80, 60), "blue").save(buffer, "PNG")
        upload = UploadFile(io.BytesIO(buffer.getvalue()), filename="reference.png")
        with self.assertRaises(self.p.VideoProjectError) as error:
            await self.p.upload_reference(project.id, project.revision, upload)
        self.assertEqual(error.exception.code, "revision_conflict")
        self.assertEqual(self.p.get(project.id), saved)
        self.assertTrue(upload.file.closed)
        self.assertEqual(list(self.p.project_dir(project.id).rglob("*.png")), [])
        self.assertEqual(list(self.p.project_dir(project.id).glob("*.upload")), [])

    async def test_unchanged_full_snapshot_and_name_preserve_approval_and_publication(
        self,
    ) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
            VideoProjectShot,
            VideoVariant,
            VideoShotDraft,
        )

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        document = self.p.load(project.id)
        document.project.shots = [
            VideoProjectShot(
                id="a" * 32,
                start_sec=0,
                seconds=4,
                prompt="one",
                approved_variant_id="b" * 32,
                variants=[
                    VideoVariant(id="b" * 32, seed=0, status="ready", created_at="")
                ],
            )
        ]
        document.project.file_url = "/published"
        self.p.save(document)
        shot = document.project.shots[0]
        draft = VideoShotDraft.model_validate(
            shot.model_dump(exclude={"variants", "approved_variant_id"})
        )
        saved = self.p.update(
            project.id,
            UpdateVideoProjectRequest(
                revision=project.revision,
                name="Renamed",
                mode=project.mode,
                direction=project.direction,
                seed=project.seed,
                settings=project.settings,
                export_settings=project.export_settings,
                shots=[draft],
                overlays=[],
                markers=[],
            ),
        )
        self.assertEqual(saved.shots[0].approved_variant_id, "b" * 32)
        self.assertEqual(saved.file_url, "/published")

    async def test_export_edit_keeps_clip_approval_but_invalidates_final_publication(
        self,
    ) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
            VideoProjectShot,
            VideoExportSettings,
        )

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        document = self.p.load(project.id)
        document.project.shots = [
            VideoProjectShot(
                id="a" * 32,
                start_sec=0,
                seconds=4,
                prompt="one",
                approved_variant_id="b" * 32,
            )
        ]
        document.project.file_url = "/published"
        self.p.save(document)
        saved = self.p.update(
            project.id,
            UpdateVideoProjectRequest(
                revision=project.revision,
                export_settings=VideoExportSettings(aspect="portrait"),
            ),
        )
        self.assertEqual(saved.shots[0].approved_variant_id, "b" * 32)
        self.assertEqual(saved.file_url, "")

    async def test_concurrent_edits_serialize_revision_read_modify_write(self) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
        )

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.p.update,
                    project.id,
                    UpdateVideoProjectRequest(revision=project.revision, name=name),
                )
                for name in ("First", "Second")
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(not isinstance(result, Exception) for result in results), 1
        )
        self.assertEqual(
            sum(
                isinstance(result, self.p.VideoProjectError)
                and result.code == "revision_conflict"
                for result in results
            ),
            1,
        )
        self.assertEqual(self.p.get(project.id).revision, 2)

    async def test_atomic_replace_failure_preserves_original_metadata(self) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
        )

        project = await self.p.create(CreateVideoProjectRequest(track_id=1))
        path = self.p.project_dir(project.id) / "project.json"
        before = path.read_bytes()
        with patch.object(self.p.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(self.p.VideoProjectError) as error:
                self.p.update(
                    project.id,
                    UpdateVideoProjectRequest(revision=project.revision, name="Lost"),
                )
        self.assertEqual(error.exception.code, "storage_failed")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])


class PictureProjectTests(VideoProjectTests):
    async def test_reel_starts_without_a_song_at_a_vertical_frame(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest

        project = await self.p.create(CreateVideoProjectRequest(preset="reel", name="Reel"))
        self.assertIsNone(project.track_id)
        self.assertEqual(project.preset, "reel")
        self.assertEqual((project.settings.width, project.settings.height), (704, 1280))
        self.assertEqual(project.export_settings.aspect, "portrait")
        self.assertEqual(project.duration_sec, 12)
        self.assertEqual([shot.seconds for shot in project.shots], [4, 4, 4])
        self.assertFalse(project.source_changed)
        self.assertEqual(project.mode, "generated")

    async def test_silent_project_has_no_song_and_no_reel_shots(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest

        project = await self.p.create(
            CreateVideoProjectRequest(name="Silent", duration_sec=8)
        )
        self.assertIsNone(project.track_id)
        self.assertEqual(project.preset, "none")
        self.assertEqual(project.duration_sec, 8)
        self.assertEqual(project.shots, [])
        self.assertFalse(project.source_changed)

    async def test_reel_rejects_a_song_and_a_landscape_frame(self) -> None:
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
            VideoProjectSettings,
        )

        with self.assertRaises(self.p.VideoProjectError) as created:
            await self.p.create(CreateVideoProjectRequest(track_id=1, preset="reel"))
        self.assertEqual(created.exception.code, "reel_has_song")
        project = await self.p.create(CreateVideoProjectRequest(preset="reel"))
        with self.assertRaises(self.p.VideoProjectError) as edited:
            self.p.update(
                project.id,
                UpdateVideoProjectRequest(
                    revision=project.revision,
                    settings=VideoProjectSettings(width=704, height=448),
                ),
            )
        self.assertEqual(edited.exception.code, "reel_size")
        self.assertEqual(self.p.get(project.id).settings.height, 1280)

    async def test_reel_duration_must_stay_between_eight_and_fifteen(self) -> None:
        from app.video_contracts import CreateVideoProjectRequest, UpdateVideoProjectRequest, VideoShotDraft

        with self.assertRaises(self.p.VideoProjectError) as created:
            await self.p.create(CreateVideoProjectRequest(preset="reel", duration_sec=9))
        self.assertEqual(created.exception.code, "reel_duration")
        project = await self.p.create(CreateVideoProjectRequest(preset="reel", duration_sec=8))
        self.assertEqual([shot.seconds for shot in project.shots], [4, 4])
        short = [
            VideoShotDraft(id=shot.id, start_sec=index * 2, seconds=2, prompt=shot.prompt, seed=shot.seed)
            for index, shot in enumerate(project.shots)
        ]
        with self.assertRaises(self.p.VideoProjectError) as edited:
            self.p.update(project.id, UpdateVideoProjectRequest(revision=project.revision, shots=short))
        self.assertEqual(edited.exception.code, "reel_duration")



    async def test_character_lock_requires_one_still_and_ignores_songs(self) -> None:
        from fastapi import UploadFile
        from app.video_contracts import (
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
            VideoExportSettings,
            VideoShotDraft,
        )

        song = await self.p.create(CreateVideoProjectRequest(track_id=1))
        with self.assertRaises(self.p.VideoProjectError) as locked:
            self.p.update(
                song.id,
                UpdateVideoProjectRequest(revision=song.revision, character_lock=True),
            )
        self.assertEqual(locked.exception.code, "character_lock_picture_only")
        with self.assertRaises(self.p.VideoProjectError) as speech:
            self.p.update(
                song.id,
                UpdateVideoProjectRequest(
                    revision=song.revision,
                    export_settings=VideoExportSettings(attach_speech=True),
                ),
            )
        self.assertEqual(speech.exception.code, "speech_picture_only")
        self.assertFalse(self.p.get(song.id).character_lock)
        self.assertFalse(self.p.get(song.id).export_settings.attach_speech)

        project = await self.p.create(CreateVideoProjectRequest(name="Silent", duration_sec=8))
        shot = VideoShotDraft(id="a" * 32, start_sec=0, seconds=4, prompt="A person walks.")
        project = self.p.update(
            project.id,
            UpdateVideoProjectRequest(
                revision=project.revision, character_lock=True, shots=[shot]
            ),
        )
        self.assertIn("character_still_missing", project.warnings)
        with self.assertRaises(self.p.VideoProjectError) as missing:
            self.p.ensure_character_lock(project)
        self.assertEqual(missing.exception.code, "character_still_missing")

        still = self.root / "still.png"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=32x32",
                "-frames:v",
                "1",
                str(still),
            ],
            check=True,
        )
        project = await self.p.upload_reference(
            project.id,
            project.revision,
            UploadFile(io.BytesIO(still.read_bytes()), filename="face.png"),
        )
        self.assertNotIn("character_still_missing", project.warnings)
        still_id, strength = self.p.effective_still(project, project.shots[0])
        self.assertEqual(still_id, project.references[0].id)
        self.assertEqual(strength, self.p.CHARACTER_LOCK_STRENGTH)
        self.p.ensure_character_lock(project)

    async def test_speech_clip_is_stored_and_can_be_cleared(self) -> None:
        from fastapi import UploadFile
        from app.video_contracts import CreateVideoProjectRequest, VideoRevisionRequest

        project = await self.p.create(CreateVideoProjectRequest(name="Silent", duration_sec=8))
        project = await self.p.upload_speech(
            project.id,
            project.revision,
            UploadFile(io.BytesIO(self.audio.read_bytes()), filename="line.wav"),
        )
        self.assertIsNotNone(project.speech_clip)
        assert project.speech_clip is not None
        self.assertGreater(project.speech_clip.duration_sec, 1)
        self.assertTrue(project.export_settings.attach_speech)
        self.assertEqual(project.speech_clip.kind, "upload")
        self.assertTrue(self.p.speech_file(project.id).is_file())
        cleared = self.p.clear_speech(
            project.id, VideoRevisionRequest(revision=project.revision)
        )
        self.assertIsNone(cleared.speech_clip)
        with self.assertRaises(self.p.VideoProjectError):
            self.p.speech_file(project.id)


    async def test_uploaded_speech_is_mixed_unless_the_export_stays_silent(self) -> None:
        from fastapi import UploadFile
        from app.video_contracts import CreateVideoProjectRequest, VideoExportSettings, UpdateVideoProjectRequest

        project = await self.p.create(CreateVideoProjectRequest(name="Silent", duration_sec=8))
        project = await self.p.upload_speech(
            project.id,
            project.revision,
            UploadFile(io.BytesIO(self.audio.read_bytes()), filename="line.wav"),
        )
        assert project.speech_clip is not None
        self.assertEqual(project.speech_clip.kind, "upload")
        self.assertTrue(project.export_settings.attach_speech)
        silent = self.p.update(
            project.id,
            UpdateVideoProjectRequest(
                revision=project.revision,
                export_settings=VideoExportSettings(attach_speech=False),
            ),
        )
        self.assertFalse(silent.export_settings.attach_speech)
        self.assertIsNotNone(silent.speech_clip)

    async def test_spoken_line_uses_a_saved_voice_and_is_not_model_audio(self) -> None:
        from app import speech_clone, voice_profiles
        from app.video_contracts import CreateVideoProjectRequest, VideoSpeechLineRequest
        from app.video_render import generation_audio

        self.enterContext(patch.object(voice_profiles, "PROFILES_ROOT", self.root / "voice-profiles"))
        profile = voice_profiles.create_profile(
            name="Ada",
            consent_confirmed=True,
            audio_bytes=b"RIFF....WAVE",
            filename="ref.wav",
            notes="Reference.",
            reference_transcript="Reference.",
        )

        def fake_synth(**kwargs):
            speech_clone._write_silent_wav(kwargs["output_path"], duration_s=0.4)
            return speech_clone.SynthesisOutcome(
                status="mock_completed", detail="mock", output_path=kwargs["output_path"]
            )

        song = await self.p.create(CreateVideoProjectRequest(track_id=1))
        with patch("app.speech_clone.synthesize_to_path", side_effect=AssertionError("song")):
            with self.assertRaises(self.p.VideoProjectError) as blocked:
                await self.p.speak_line(
                    song.id,
                    VideoSpeechLineRequest(revision=song.revision, profile_id=profile.id, text="Hello"),
                )
        self.assertEqual(blocked.exception.code, "speech_picture_only")

        project = await self.p.create(CreateVideoProjectRequest(name="Silent", duration_sec=8))
        with patch("app.speech_clone.synthesize_to_path", side_effect=fake_synth):
            spoken = await self.p.speak_line(
                project.id,
                VideoSpeechLineRequest(revision=project.revision, profile_id=profile.id, text="Hello there"),
            )
        assert spoken.speech_clip is not None
        self.assertEqual(spoken.speech_clip.kind, "voice")
        self.assertEqual(spoken.speech_clip.voice_profile_id, profile.id)
        self.assertEqual(spoken.speech_clip.line, "Hello there")
        self.assertTrue(spoken.export_settings.attach_speech)
        self.assertIn("speech_mock", spoken.warnings)
        self.assertIsNone(generation_audio(spoken, self.p.speech_file(project.id)))

    async def test_character_record_locks_one_still_and_keeps_song_projects_alone(self) -> None:
        from fastapi import UploadFile
        from app import video_characters, voice_profiles
        from app.video_contracts import (
            ApplyVideoCharacterRequest,
            CreateVideoProjectRequest,
            UpdateVideoProjectRequest,
            VideoShotDraft,
        )

        self.enterContext(patch.object(voice_profiles, "PROFILES_ROOT", self.root / "voice-profiles"))
        self.enterContext(patch.object(video_characters, "DATA_DIR", self.root))
        profile = voice_profiles.create_profile(
            name="Ada",
            consent_confirmed=True,
            audio_bytes=b"RIFF....WAVE",
            filename="ref.wav",
            notes="Reference.",
            reference_transcript="Reference.",
        )
        still = self.root / "face.png"
        subprocess.run(
            [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=32x32", "-frames:v", "1", str(still)],
            check=True,
        )
        with self.assertRaises(self.p.VideoProjectError) as denied:
            await video_characters.create_character(
                name="Ada",
                voice_profile_id=profile.id,
                consent_confirmed=False,
                upload=UploadFile(io.BytesIO(still.read_bytes()), filename="face.png"),
            )
        self.assertEqual(denied.exception.code, "consent_required")
        character = await video_characters.create_character(
            name="Ada",
            voice_profile_id=profile.id,
            consent_confirmed=True,
            upload=UploadFile(io.BytesIO(still.read_bytes()), filename="face.png"),
        )
        self.assertEqual(character.look, "locked_still")
        self.assertTrue(character.voice_ready)
        self.assertTrue(video_characters.still_file(character.id).is_file())

        song = await self.p.create(CreateVideoProjectRequest(track_id=1))
        with self.assertRaises(self.p.VideoProjectError) as song_block:
            self.p.apply_character(
                song.id, ApplyVideoCharacterRequest(revision=song.revision, character_id=character.id)
            )
        self.assertEqual(song_block.exception.code, "character_picture_only")

        project = await self.p.create(CreateVideoProjectRequest(name="Silent", duration_sec=8))
        shot = VideoShotDraft(id="b" * 32, start_sec=0, seconds=4, prompt="A person walks.")
        project = self.p.update(
            project.id, UpdateVideoProjectRequest(revision=project.revision, shots=[shot])
        )
        applied = self.p.apply_character(
            project.id, ApplyVideoCharacterRequest(revision=project.revision, character_id=character.id)
        )
        self.assertTrue(applied.character_lock)
        self.assertEqual(applied.character_id, character.id)
        self.assertEqual(applied.shots[0].reference_id, applied.references[0].id)
        still_id, strength = self.p.effective_still(applied, applied.shots[0])
        self.assertEqual(still_id, applied.references[0].id)
        self.assertEqual(strength, self.p.CHARACTER_LOCK_STRENGTH)
        self.p.ensure_character_lock(applied)

        from app.video_contracts import VideoVariant
        def approve(document: self.p.StoredVideoProject) -> None:
            document.project.shots[0].variants.append(VideoVariant(id="c" * 32, seed=0, status="ready", created_at=self.p.now()))
            document.project.shots[0].approved_variant_id = "c" * 32
        approved = self.p.mutate(project.id, approve)
        reapplied = self.p.apply_character(project.id, ApplyVideoCharacterRequest(revision=approved.revision, character_id=character.id))
        self.assertIsNone(reapplied.shots[0].approved_variant_id)
        self.assertEqual(len(reapplied.shots[0].variants), 1)

    def test_character_still_refuses_escaping_symlinks(self) -> None:
        from app import video_characters
        self.enterContext(patch.object(video_characters, "DATA_DIR", self.root))
        identifier = "a" * 32
        directory = video_characters.character_dir(identifier)
        directory.mkdir(parents=True)
        outside = self.root / "outside.png"
        outside.write_bytes(b"private")
        (directory / "still.png").symlink_to(outside)
        with self.assertRaises(self.p.VideoProjectError):
            video_characters.still_file(identifier)
