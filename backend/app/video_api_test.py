"""Workspace HTTP boundaries use temp state and never execute models."""

from __future__ import annotations
import shutil, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock


class VideoApiTests(unittest.TestCase):
    def test_create_returns_safe_dependency_and_probe_errors_without_writing_project(self) -> None:
        from app import video_projects as p
        from app.api.routes_videos import router
        from app.video_jobs import VideoJobError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / 'song.wav'
            audio.write_bytes(b'fixture')
            app = FastAPI(); app.include_router(router)
            with patch.object(p, 'DATA_DIR', root), patch.object(p.db, 'get_track', return_value={
                    'id':1, 'title':'Song', 'audio_path':str(audio), 'duration_ms':4000}), TestClient(app) as client:
                for error, code in ((VideoJobError('ffmpeg_missing'), 'ffmpeg_missing'),
                                    (TimeoutError('private details'), 'processing_failed')):
                    with self.subTest(code=code), patch('app.video_jobs._probe_duration', new=AsyncMock(side_effect=error)):
                        result = client.post('/api/videos/projects', json={'track_id':1})
                        self.assertEqual(result.status_code, 400, result.text)
                        self.assertEqual(result.json()['detail'], code)
                        self.assertEqual(p.list_projects(), [])

    def test_create_patch_revision_conflict_and_route_precedence(self) -> None:
        from app import video_projects as p
        from app.api.routes_videos import router

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "song.wav"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=4",
                    str(audio),
                ],
                check=True,
            )
            app = FastAPI()
            app.include_router(router)
            with (
                patch.object(p, "DATA_DIR", root),
                patch.object(
                    p.db,
                    "get_track",
                    return_value={
                        "id": 1,
                        "title": "Song",
                        "audio_path": str(audio),
                        "duration_ms": 4000,
                    },
                ),
                TestClient(app) as client,
            ):
                response = client.post(
                    "/api/videos/projects", json={"track_id": 1, "mode": "cover"}
                )
                self.assertEqual(response.status_code, 200, response.text)
                project = response.json()
                self.assertEqual(client.get("/api/videos/projects").status_code, 200)
                response = client.patch(
                    "/api/videos/projects/" + project["id"],
                    json={"revision": project["revision"], "name": "Saved"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                stale = client.patch(
                    "/api/videos/projects/" + project["id"],
                    json={"revision": project["revision"], "name": "Overwrite"},
                )
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(stale.json()["detail"], "revision_conflict")
                self.assertEqual(
                    client.get("/api/videos/projects/../private").status_code, 404
                )
                self.assertEqual(
                    client.get("/api/videos/projects/" + project["id"]).json()["name"],
                    "Saved",
                )
