"""Real CPU media validation, with generated temporary audiovisual fixtures."""

from __future__ import annotations
import shutil, subprocess, tempfile, unittest
from pathlib import Path


class VideoMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_size_accounts_for_rotation_and_anamorphic_pixels(self) -> None:
        from app.video_media import probe_media, MediaInfo, VideoMediaError
        with tempfile.TemporaryDirectory() as tmp:
            raw, rotated, anamorphic = (Path(tmp)/name for name in ('raw.mp4', 'rotated.mov', 'anamorphic.avi'))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=2',
                            '-c:v', 'libx264', str(raw)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-display_rotation', '90', '-i', str(raw),
                            '-c', 'copy', str(rotated)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=2',
                            '-vf', 'setsar=2/1', '-c:v', 'mpeg4', str(anamorphic)], check=True)
            self.assertEqual((await probe_media(rotated, input_format='mov')).display_size, (64, 96))
            self.assertEqual((await probe_media(anamorphic, input_format='avi')).display_size, (192, 64))
            with self.assertRaises(VideoMediaError):
                _ = MediaInfo(96, 64, 24, 2, 0, rotation=45).display_size

    async def test_explicit_avi_probe_preserves_duration_and_rejects_disguised_playlist(self) -> None:
        from app.video_media import probe_media, VideoMediaError
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'movie.avi'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=96x64:r=24:d=2',
                            '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', str(output)], check=True)
            result = await probe_media(output, input_format='avi')
            self.assertAlmostEqual(result.video_duration, 2, places=1)
            self.assertAlmostEqual(result.audio_duration, 2, places=1)
            playlist = Path(tmp) / 'playlist.avi'
            playlist.write_text(f"ffconcat version 1.0\nfile '{output}'\n")
            with self.assertRaises(VideoMediaError):
                await probe_media(playlist, input_format='avi')

    async def test_webm_uses_container_duration_when_stream_duration_is_absent(self) -> None:
        from app.video_media import probe_media
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'source.webm'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=96x64:r=24:d=2',
                            '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'libvpx-vp9', '-c:a', 'libopus', str(output)], check=True)
            result = await probe_media(output)
            self.assertAlmostEqual(result.video_duration, 2, places=1)
            self.assertAlmostEqual(result.audio_duration, 2, places=1)

    async def test_short_video_cannot_publish_with_long_audio(self) -> None:
        from app.video_media import validate_media, VideoMediaError

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "short.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=704x448:r=24:d=0.5",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=4",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(output),
                ],
                check=True,
            )
            with self.assertRaises(VideoMediaError):
                await validate_media(output, 4, (704, 448), require_audio=True)

    async def test_valid_cpu_video_and_dimensions(self) -> None:
        from app.video_media import validate_media, VideoMediaError

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "valid.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=704x448:r=24:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=duration=2",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(output),
                ],
                check=True,
            )
            info = await validate_media(output, 2, (704, 448), require_audio=True)
            self.assertAlmostEqual(info.video_duration, 2, places=1)
            with self.assertRaises(VideoMediaError):
                await validate_media(output, 2, (768, 512), require_audio=True)
