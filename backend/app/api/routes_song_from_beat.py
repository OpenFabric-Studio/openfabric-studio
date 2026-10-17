"""Read-only ACE capability evidence; the existing queue owns preview and work."""
from __future__ import annotations

from fastapi import APIRouter

from .. import song_capabilities
from ..job_lifecycle import run_owned_thread
from ..song_from_beat_contracts import SongFromBeatCapabilities
from .routes_music_queue import MusicQueueRoute

router = APIRouter(prefix='/api/song-from-beat', tags=['song-from-beat'], route_class=MusicQueueRoute)


@router.get('/capabilities', response_model=SongFromBeatCapabilities)
async def capabilities() -> SongFromBeatCapabilities:
    return await run_owned_thread(song_capabilities.capabilities)
