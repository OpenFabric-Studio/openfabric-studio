"""Read-only capability/source preflight; existing local-music routes own admission."""
from __future__ import annotations

from fastapi import APIRouter

from .. import music_conditioning
from ..local_music_contracts import LocalMusicRequest
from ..music_conditioning_contracts import (MusicConditioningCapabilities, MusicConditioningPreview,
    MusicConditioningSourceInfo, MusicConditioningSourceRequest)
from .routes_local_music import LocalMusicRoute

router = APIRouter(prefix='/api/music-conditioning', tags=['music-conditioning'], route_class=LocalMusicRoute)


@router.get('/capabilities', response_model=MusicConditioningCapabilities)
async def capabilities() -> MusicConditioningCapabilities:
    return music_conditioning.capabilities()


@router.post('/source', response_model=MusicConditioningSourceInfo)
async def inspect_source(body: MusicConditioningSourceRequest) -> MusicConditioningSourceInfo:
    return await music_conditioning.inspect_source(body.source)


@router.post('/preview', response_model=MusicConditioningPreview)
async def preview(body: LocalMusicRequest) -> MusicConditioningPreview:
    return await music_conditioning.preview(body)
