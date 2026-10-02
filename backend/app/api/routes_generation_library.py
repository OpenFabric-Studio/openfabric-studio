"""Validated history, retention and independently revisioned preset management."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Coroutine
from typing import Annotated, TypeVar

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from .. import db, generation_library as library
from ..generation_contracts import (
    CreateGenerationPresetRequest, DuplicateGenerationPresetRequest, GenerationDeleteResponse,
    GenerationEngine, GenerationHistoryResponse, GenerationLibrarySettings, GenerationPreset,
    GenerationPresetImportResponse, GenerationPresetsResponse, ImportGenerationPresetsRequest,
    UpdateGenerationLibrarySettingsRequest, UpdateGenerationPresetRequest,
)

class GenerationRoute(APIRoute):
    """Stable validation errors also cover JSON's nonstandard NaN/Infinity values."""
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail='invalid_generation_request') from exc

        return handle


router = APIRouter(prefix='/api/generation', tags=['generation library'], route_class=GenerationRoute)
RecordId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]
Result = TypeVar('Result')


def _run(operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except library.GenerationLibraryError as exc:
        status = 404 if exc.code == 'generation_record_not_found' else 409
        raise HTTPException(status, detail=exc.code) from exc
    except (sqlite3.Error, OSError, ValueError) as exc:
        logging.getLogger(__name__).exception('Generation library operation failed')
        raise HTTPException(503, detail='generation_library_unavailable') from exc


@router.get('/history', response_model=GenerationHistoryResponse)
async def list_history(
    engine: GenerationEngine | None = None,
    search: Annotated[str, Query(max_length=500)] = '',
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> GenerationHistoryResponse:
    return _run(lambda: library.list_history(db.get_db(), engine, search, limit, offset))


@router.delete('/history/{record_id}', response_model=GenerationDeleteResponse)
async def delete_history(record_id: RecordId) -> GenerationDeleteResponse:
    _run(lambda: library.delete_history(db.get_db(), record_id))
    return GenerationDeleteResponse()


@router.get('/settings', response_model=GenerationLibrarySettings)
async def get_settings() -> GenerationLibrarySettings:
    return _run(lambda: library.get_settings(db.get_db()))


@router.put('/settings', response_model=GenerationLibrarySettings)
async def update_settings(request: UpdateGenerationLibrarySettingsRequest) -> GenerationLibrarySettings:
    return _run(lambda: library.update_settings(db.get_db(), request))


@router.get('/presets', response_model=GenerationPresetsResponse)
async def list_presets(engine: GenerationEngine | None = None,
                       search: Annotated[str, Query(max_length=500)] = '',
                       limit: Annotated[int, Query(ge=1, le=1000)] = 100,
                       offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0) -> GenerationPresetsResponse:
    return _run(lambda: library.list_presets(db.get_db(), engine, search, limit, offset))


@router.post('/presets', response_model=GenerationPreset)
async def create_preset(request: CreateGenerationPresetRequest) -> GenerationPreset:
    return _run(lambda: library.create_preset(db.get_db(), request))


@router.get('/presets/{record_id}', response_model=GenerationPreset)
async def get_preset(record_id: RecordId) -> GenerationPreset:
    return _run(lambda: library.get_preset(db.get_db(), record_id))


@router.post('/presets/import', response_model=GenerationPresetImportResponse)
async def import_presets(request: ImportGenerationPresetsRequest) -> GenerationPresetImportResponse:
    return _run(lambda: library.import_presets(db.get_db(), request))


@router.put('/presets/{preset_id}', response_model=GenerationPreset)
async def update_preset(preset_id: RecordId, request: UpdateGenerationPresetRequest) -> GenerationPreset:
    return _run(lambda: library.update_preset(db.get_db(), preset_id, request))


@router.post('/presets/{preset_id}/duplicate', response_model=GenerationPreset)
async def duplicate_preset(preset_id: RecordId, request: DuplicateGenerationPresetRequest) -> GenerationPreset:
    return _run(lambda: library.duplicate_preset(db.get_db(), preset_id, request.name, request.revision))


@router.delete('/presets/{preset_id}', response_model=GenerationDeleteResponse)
async def delete_preset(preset_id: RecordId,
                        revision: Annotated[int, Query(ge=1, le=9_007_199_254_740_991)]) -> GenerationDeleteResponse:
    _run(lambda: library.delete_preset(db.get_db(), preset_id, revision))
    return GenerationDeleteResponse()
