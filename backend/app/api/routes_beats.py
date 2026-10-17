"""Local beat documents and durable CPU renders; no caller-supplied paths."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Path as PathParameter, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import FileResponse, Response
from starlette.types import Message

from .. import beat_jobs, beat_presets, beat_projects, beat_render, beat_store
from ..beat_compile import compile_events
from ..beat_contracts import (BeatArchiveResponse, BeatCreateRequest, BeatDocument, BeatDocuments,
    BeatError, BeatHumanizeRequest, BeatInstrumentManifest, BeatJob, BeatJobRequest, BeatJobs, BeatKits, BeatRestoreRequest,
    BeatRevisions, BeatStoragePreview, BeatUpdateRequest, BeatVariationRequest)
from ..beat_preset_contracts import (BeatPresetCatalog, BeatPresetCompilation, BeatPresetCompileRequest)
from ..beat_project_contracts import MusicProjectPublication
from ..job_lifecycle import run_owned_thread
from ..module_security import require_local_origin

logger = logging.getLogger(__name__)


class BeatRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            count = 0
            maximum = 520 * 1024 * 1024 if request.url.path.endswith('/kits/import') else 8 * 1024 * 1024

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > maximum:
                        raise HTTPException(413, 'beat_render_limit')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'beat_pattern_invalid') from error
            except BeatError as error:
                status = 404 if error.code in ('beat_not_found', 'beat_job_not_found') else 503 if error.code == 'beat_storage_unavailable' else 409
                raise HTTPException(status, error.code) from error
            except beat_presets.BeatPresetError as error:
                raise HTTPException(409, error.code) from error
            except (OSError, sqlite3.Error, ValidationError, ValueError, RuntimeError) as error:
                logger.exception('Beat storage or validation failed')
                raise HTTPException(503, 'beat_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/beats', tags=['beats'], route_class=BeatRoute)


@router.get('', response_model=BeatDocuments)
async def list_documents() -> BeatDocuments:
    return BeatDocuments(documents=beat_store.list_documents())


@router.post('', response_model=BeatDocument, status_code=201)
async def create(body: BeatCreateRequest) -> BeatDocument:
    # Database transactions stay on the event-loop thread. Only CPU/file work
    # is moved out; create_document performs one small atomic insertion.
    kit_id = body.document.kit_id if body.document is not None else body.kit_id
    digest = body.document.kit_manifest_sha256 if body.document is not None else None
    await run_owned_thread(beat_render.load_kit, kit_id, digest)
    return beat_store.create_document(body)


@router.get('/kits', response_model=BeatKits)
async def kits() -> BeatKits:
    return BeatKits(kits=await run_owned_thread(beat_render.list_kits))


@router.post('/kits/import', response_model=BeatKits)
async def import_kit(manifest: Annotated[UploadFile, File()], archive: Annotated[UploadFile, File()]) -> BeatKits:
    try:
        content = await manifest.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise HTTPException(413, 'beat_render_limit')
        value = BeatInstrumentManifest.model_validate_json(content)
        await run_owned_thread(beat_render.import_kit, value, archive.file)
        return BeatKits(kits=await run_owned_thread(beat_render.list_kits))
    except ValidationError as exc:
        raise HTTPException(422, 'beat_kit_invalid') from exc
    finally:
        await manifest.close()
        await archive.close()


@router.get('/presets', response_model=BeatPresetCatalog)
async def presets() -> BeatPresetCatalog:
    installed = await run_owned_thread(beat_render.list_kits)
    return await run_owned_thread(beat_presets.catalog, manifests=[item.manifest for item in installed])


@router.post('/presets/compile', response_model=BeatPresetCompilation)
async def compile_preset(body: BeatPresetCompileRequest) -> BeatPresetCompilation:
    try:
        manifest = await run_owned_thread(beat_render.load_kit, body.kit_id)
    except BeatError as error:
        if error.code != 'beat_instrument_missing':
            raise
        manifest = None
    return await run_owned_thread(beat_presets.compile_preset, body, manifest=manifest)


@router.get('/renders', response_model=BeatJobs)
async def jobs(beat_id: str | None = None) -> BeatJobs:
    return BeatJobs(jobs=beat_jobs.list_jobs(beat_id))


@router.get('/renders/{render_id}', response_model=BeatJob)
async def get_job(render_id: str) -> BeatJob:
    return beat_jobs.get(render_id)


async def _empty(request: Request) -> None:
    if (await request.body()).strip() not in (b'', b'{}'):
        raise HTTPException(422, 'beat_pattern_invalid')


@router.post('/renders/{render_id}/cancel', response_model=BeatJob)
async def cancel(render_id: str, request: Request) -> BeatJob:
    await _empty(request)
    return await beat_jobs.cancel(render_id)


@router.post('/renders/{render_id}/retry-save', response_model=BeatJob)
async def retry_save(render_id: str, request: Request) -> BeatJob:
    await _empty(request)
    return await beat_jobs.retry_save(render_id)


@router.post('/renders/{render_id}/project', response_model=MusicProjectPublication)
async def create_project(render_id: str, request: Request) -> MusicProjectPublication:
    await _empty(request)
    return await beat_projects.publish_beat_render(render_id)


@router.get('/renders/{render_id}/artifacts/{artifact_id}')
async def artifact(render_id: str, artifact_id: str) -> FileResponse:
    path, item = await run_owned_thread(beat_jobs.artifact, render_id, artifact_id)
    return FileResponse(path, media_type=item.media_type, filename=item.filename)


@router.get('/{beat_id}', response_model=BeatDocument)
async def get_document(beat_id: str) -> BeatDocument:
    return beat_store.get(beat_id)


@router.patch('/{beat_id}', response_model=BeatDocument)
async def update(beat_id: str, body: BeatUpdateRequest) -> BeatDocument:
    kit = await run_owned_thread(beat_render.load_kit, body.document.kit_id, body.document.kit_manifest_sha256)
    await run_owned_thread(compile_events, body.document, beat_render.RATE, kit)
    return beat_store.update(beat_id, body.expected_revision, body.document)


@router.delete('/{beat_id}', response_model=BeatArchiveResponse)
async def archive(beat_id: str, expected_revision: Annotated[int, Query(ge=0, le=9007199254740991)]) -> BeatArchiveResponse:
    beat_store.archive(beat_id, expected_revision)
    return BeatArchiveResponse(beat_id=beat_id)


@router.get('/{beat_id}/revisions', response_model=BeatRevisions)
async def revisions(beat_id: str) -> BeatRevisions:
    return BeatRevisions(revisions=beat_store.revisions(beat_id))


@router.get('/{beat_id}/revisions/{revision}', response_model=BeatDocument)
async def revision(beat_id: str, revision: Annotated[int, PathParameter(ge=0, le=9007199254740991)]) -> BeatDocument:
    return beat_store.get_revision(beat_id, revision)


@router.post('/{beat_id}/restore', response_model=BeatDocument)
async def restore(beat_id: str, body: BeatRestoreRequest) -> BeatDocument:
    return beat_store.restore(beat_id, body.expected_revision, body.revision)


@router.post('/{beat_id}/variation', response_model=BeatDocument)
async def variation(beat_id: str, body: BeatVariationRequest) -> BeatDocument:
    return beat_store.variation(beat_id, body)


@router.post('/{beat_id}/humanize', response_model=BeatDocument)
async def humanize(beat_id: str, body: BeatHumanizeRequest) -> BeatDocument:
    return beat_store.humanize(beat_id, body)


def _match(beat_id: str, body: BeatJobRequest) -> None:
    if body.beat_id != beat_id:
        raise BeatError('beat_pattern_invalid')


@router.post('/{beat_id}/storage-preview', response_model=BeatStoragePreview)
async def storage_preview(beat_id: str, body: BeatJobRequest) -> BeatStoragePreview:
    _match(beat_id, body)
    return await beat_jobs.preview(body)


@router.post('/{beat_id}/renders', response_model=BeatJob, status_code=202)
async def render(beat_id: str, body: BeatJobRequest) -> BeatJob:
    _match(beat_id, body)
    return await beat_jobs.start(body)
