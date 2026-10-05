"""Optional cloud music API; paid actions require a matching confirmed quote."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from .routes_openrouter import ProviderRoute
from .. import cloud_music, openrouter_catalog
from ..cloud_music_contracts import CloudMusicJob, CloudMusicJobs, CloudMusicSubmitRequest
from ..openrouter_contracts import OpenRouterMusicRequest, OpenRouterQuote
from ..openrouter_errors import OpenRouterError
router=APIRouter(prefix='/api/cloud-music',tags=['cloud-music'],route_class=ProviderRoute)

@router.post('/quote',response_model=OpenRouterQuote)
async def quote(body: OpenRouterMusicRequest) -> OpenRouterQuote:
    try: return openrouter_catalog.quote_music(body)
    except OpenRouterError as error: raise HTTPException(error.status,detail=error.code) from error

@router.post('/jobs',response_model=CloudMusicJob)
async def submit(body: CloudMusicSubmitRequest) -> CloudMusicJob:
    try: return cloud_music.submit(body)
    except OpenRouterError as error: raise HTTPException(error.status,detail=error.code) from error

@router.get('/jobs',response_model=CloudMusicJobs)
async def jobs() -> CloudMusicJobs:
    try: return cloud_music.list_jobs()
    except OpenRouterError as error: raise HTTPException(error.status,detail=error.code) from error

@router.post('/jobs/{identifier}/cancel',response_model=CloudMusicJob)
async def cancel(identifier: str) -> CloudMusicJob:
    try: return await cloud_music.cancel(identifier)
    except OpenRouterError as error: raise HTTPException(error.status,detail=error.code) from error

@router.post('/jobs/{identifier}/retry-save',response_model=CloudMusicJob)
async def retry_save(identifier: str) -> CloudMusicJob:
    try: return cloud_music.retry_save(identifier)
    except OpenRouterError as error: raise HTTPException(error.status,detail=error.code) from error
