"""Check native cloud geometry/timing before explicit silent studio conformance."""
from __future__ import annotations
import math
from pathlib import Path
from .video_media import MediaInfo, probe_media, tool, validate_media
from .video_projects import VideoProjectError


async def conform(project_id: str, source: Path, output: Path, *, source_seconds: int,
    target_seconds: int, size: tuple[int,int], trim_confirmed: bool) -> MediaInfo:
    if not 1 <= target_seconds <= source_seconds <= 60:
        raise VideoProjectError('cloud_duration_unsupported')
    if source_seconds != target_seconds and not trim_confirmed:
        raise VideoProjectError('cloud_trim_confirmation_required')
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 250*1024*1024:
        raise VideoProjectError('cloud_output_mismatch')
    info=await probe_media(source)
    if (info.width,info.height) != size or not math.isfinite(info.fps) or not 5 <= info.fps <= 120 or abs(info.video_duration-source_seconds) > max(.15,2/info.fps):
        raise VideoProjectError('cloud_output_mismatch')
    from .video_render import _command
    # Decode the complete delivered clip before discarding its optional audio
    # or explicitly approved tail. A valid prefix cannot hide a corrupt tail.
    await _command(project_id,[tool('ffmpeg'),'-v','error','-xerror','-i',str(source),'-map','0:v:0','-f','null','-'],'checking_cloud',timeout=180)
    await _command(project_id,[tool('ffmpeg'),'-v','error','-xerror','-y','-i',str(source),'-map','0:v:0','-an',
        '-t',str(target_seconds),'-vf','fps=24','-c:v','libx264','-pix_fmt','yuv420p',str(output)],'conforming_cloud',timeout=180)
    await validate_media(output,target_seconds,size)
    return info
