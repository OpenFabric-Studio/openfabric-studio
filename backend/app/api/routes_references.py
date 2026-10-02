"""Optional reference preparation; input paths and tool options stay server-owned."""
from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from .. import reference_imports as service
from ..reference_contracts import (
    ReferenceAbcRequest, ReferenceAbcResponse, ReferenceCapabilities, ReferenceDeleteResponse,
    ReferenceImport, ReferenceImportRequest, ReferenceImportsResponse, ReferenceLyrics,
    ReferenceLyricsRequest, ReferenceProbeRequest, ReferenceSource, ReferenceTrackPreparationRequest,
)
from ..reference_tools import ReferenceToolError, transform_abc, transform_lyrics

logger = logging.getLogger(__name__)


class ReferenceRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail='invalid_reference_request') from exc

        return handle


router = APIRouter(prefix='/api/references', tags=['reference preparation'], route_class=ReferenceRoute)
ImportId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except (service.ReferenceImportError, ReferenceToolError) as exc:
        status = 409 if exc.code == 'busy' else 404 if exc.code in ('track_missing', 'source_missing') else 422
        raise HTTPException(status, detail=exc.code) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Reference operation failed')
        raise HTTPException(503, detail='import_failed') from exc


@router.get('/capabilities', response_model=ReferenceCapabilities)
async def capabilities() -> ReferenceCapabilities:
    with _errors():
        return service.capabilities()


@router.post('/probe', response_model=ReferenceSource)
async def probe(request: ReferenceProbeRequest) -> ReferenceSource:
    with _errors():
        return await service.probe(request.url)


@router.get('/imports', response_model=ReferenceImportsResponse)
async def list_imports() -> ReferenceImportsResponse:
    with _errors():
        return service.list_imports()


@router.post('/imports', response_model=ReferenceImport)
async def create(request: ReferenceImportRequest) -> ReferenceImport:
    with _errors():
        return await service.create(request)


@router.post('/prepare', response_model=ReferenceImport)
async def prepare(request: ReferenceTrackPreparationRequest) -> ReferenceImport:
    with _errors():
        return await service.prepare(request)


@router.get('/imports/{identifier}', response_model=ReferenceImport)
async def get(identifier: ImportId) -> ReferenceImport:
    with _errors():
        return service.get(identifier)


@router.post('/imports/{identifier}/cancel', response_model=ReferenceImport)
async def cancel(identifier: ImportId) -> ReferenceImport:
    with _errors():
        return await service.cancel(identifier)


@router.post('/imports/{identifier}/retry', response_model=ReferenceImport)
async def retry(identifier: ImportId) -> ReferenceImport:
    with _errors():
        return await service.retry(identifier)


@router.delete('/imports/{identifier}', response_model=ReferenceDeleteResponse)
async def delete(identifier: ImportId) -> ReferenceDeleteResponse:
    with _errors():
        return await service.delete(identifier)


@router.get('/imports/{identifier}/vocal')
async def vocal(identifier: ImportId) -> FileResponse:
    with _errors():
        path = service.vocal_path(identifier)
    return FileResponse(path, filename=path.name)


@router.post('/tools/abc', response_model=ReferenceAbcResponse)
async def abc(request: ReferenceAbcRequest) -> ReferenceAbcResponse:
    with _errors():
        return transform_abc(request)


@router.post('/tools/lyrics', response_model=ReferenceLyrics)
async def lyrics(request: ReferenceLyricsRequest) -> ReferenceLyrics:
    with _errors():
        return transform_lyrics(request)
