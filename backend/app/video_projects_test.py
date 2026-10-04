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

