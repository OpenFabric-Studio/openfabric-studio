"""Local-only artist evidence and independently sourced conversion, no team auth claim."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError
from starlette.responses import Response

from .. import artist_production as evidence, artists, db, production_render, voice_build, voice_retention
from ..artist_contracts import (ArtistConsentRequest, ArtistConsentVersion, ArtistConsentsResponse,
    ArtistProfile, ArtistsResponse, BindArtistVoiceRequest, CreateArtistRequest)
from ..artist_production_contracts import (CleanRenderRequest, ModelUsageLedger, ProductionRun,
    ProductionRunsResponse, ReleaseReviewRequest, RightsReviewRequest, SourceAsset,
    SourceAssetsResponse, SourceDeclaration)
from ..voice_replacement import ReplacementRoute, _copy_upload, _duration, _filename
from ..voice_retention_contracts import ConfirmVoiceDeletionRequest, VoiceDeletionPlan, VoiceDeletionReceipt
from .. import recording_sessions
from ..recording_contracts import RecordingScript, RecordingSession, RecordingSessionsResponse, InspectRecordingRequest
from ..job_lifecycle import run_owned_thread
from .. import royalty_accounting, singer_ranges
from ..royalty_contracts import (AgreementCreate, Agreement, AgreementsResponse, ReleaseCreate, Release, ReleasesResponse,
    CsvImportRequest, CsvImportCommit, ImportPreview, ImportResult, AdjustmentCreate, Adjustment, PaymentCreate, Payment, RoyaltyStatement)
from ..singer_range_contracts import SingerRangeRequest, SingerRangeProfile, PlannedRangeRequest, RangeAssessment
from ..artist_production_contracts import AmendSourceRequest

logger = logging.getLogger(__name__)


class EvidenceRoute(ReplacementRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except artists.ArtistError as exc:
                raise HTTPException(exc.status_code, detail=exc.code) from exc
            except evidence.ProductionError as exc:
                raise HTTPException(409, detail=exc.code) from exc
            except recording_sessions.RecordingError as exc:
                raise HTTPException(exc.status_code, detail=exc.code) from exc
            except (royalty_accounting.RoyaltyError, singer_ranges.SingerRangeError) as exc:
                raise HTTPException(exc.status_code, detail=exc.code) from exc
            except (ValueError, TimeoutError):
                logger.exception('Artist evidence validation failed')
                raise HTTPException(409, detail='artist_evidence_validation_failed') from None
            except (OSError, sqlite3.Error):
                logger.exception('Artist evidence storage failed')
                raise HTTPException(503, detail='artist_storage_failed') from None
        return safe


router = APIRouter(prefix='/api', tags=['artist-production'], route_class=EvidenceRoute)


@router.get('/artists/recording-script', response_model=RecordingScript)
def recording_script() -> RecordingScript:
    return recording_sessions.recording_script()


@router.get('/artists/{artist_id}/range', response_model=SingerRangeProfile | None)
def singer_range(artist_id: str) -> SingerRangeProfile | None:
    artists.get_artist(artist_id)
    return singer_ranges.get_profile(artist_id)


@router.post('/artists/{artist_id}/range', response_model=SingerRangeProfile)
def save_singer_range(artist_id: str, body: SingerRangeRequest) -> SingerRangeProfile:
    return singer_ranges.save_profile(artist_id, body)


@router.post('/artists/{artist_id}/range/assess', response_model=RangeAssessment)
def assess_singer_range(artist_id: str, body: PlannedRangeRequest) -> RangeAssessment:
    return singer_ranges.assess_notes(artist_id, body.notes, body.approval)


@router.get('/artist-accounting/agreements', response_model=AgreementsResponse)
def agreements() -> AgreementsResponse:
    return AgreementsResponse(agreements=royalty_accounting.list_agreements())


@router.post('/artist-accounting/agreements', response_model=Agreement)
def create_agreement(body: AgreementCreate) -> Agreement:
    return royalty_accounting.create_agreement(body)


@router.get('/artist-accounting/releases', response_model=ReleasesResponse)
def releases() -> ReleasesResponse:
    return ReleasesResponse(releases=royalty_accounting.list_releases())


@router.post('/artist-accounting/releases', response_model=Release)
def create_release(body: ReleaseCreate) -> Release:
    return royalty_accounting.create_release(body)


@router.post('/artist-accounting/imports/preview', response_model=ImportPreview)
def preview_revenue(body: CsvImportRequest) -> ImportPreview:
    return royalty_accounting.preview_import(body)


@router.post('/artist-accounting/imports/commit', response_model=ImportResult)
def commit_revenue(body: CsvImportCommit) -> ImportResult:
    return royalty_accounting.commit_import(body)


@router.post('/artist-accounting/adjustments', response_model=Adjustment)
def add_adjustment(body: AdjustmentCreate) -> Adjustment:
    return royalty_accounting.create_adjustment(body)


@router.post('/artist-accounting/payments', response_model=Payment)
def add_payment(body: PaymentCreate) -> Payment:
    return royalty_accounting.record_payment(body)


@router.get('/artist-accounting/agreements/{agreement_id}/statement', response_model=RoyaltyStatement)
def statement(agreement_id: str) -> RoyaltyStatement:
    return royalty_accounting.statement(agreement_id)


@router.get('/voices/{voice_id}/recording-sessions', response_model=RecordingSessionsResponse)
def recording_qc(voice_id: str) -> RecordingSessionsResponse:
    voice_build.voice_dir(voice_id)
    return RecordingSessionsResponse(voice_id=voice_id, sessions=recording_sessions.list_sessions(voice_id))


@router.post('/voices/{voice_id}/recording-sessions', response_model=RecordingSession)
def inspect_recording(voice_id: str, body: InspectRecordingRequest) -> RecordingSession:
    from .. import voice_authorization
    voice_authorization.authorize(voice_id, 'train' if body.purpose == 'training' else 'convert',
        use='production' if body.purpose == 'training' else 'evaluation')
    root = voice_build.voice_dir(voice_id)
    folder = root / 'recordings' if body.purpose == 'training' else root / 'trials' / 'sources'
    return recording_sessions.inspect_file(folder / body.filename, voice_id, body.purpose, body.labels)


@router.get('/artists', response_model=ArtistsResponse)
def list_artists() -> ArtistsResponse:
    return ArtistsResponse(artists=artists.list_artists())


@router.post('/artists', response_model=ArtistProfile)
def create_artist(body: CreateArtistRequest) -> ArtistProfile:
    if body.voice_id is not None:
        voice_build.voice_dir(body.voice_id)
    return artists.create_artist(body)


@router.post('/artists/{artist_id}/voice', response_model=ArtistProfile)
def bind_voice(artist_id: str, body: BindArtistVoiceRequest) -> ArtistProfile:
    voice_build.voice_dir(body.voice_id)
    return artists.bind_voice(artist_id, body.voice_id)


@router.get('/artists/{artist_id}/consents', response_model=ArtistConsentsResponse)
def list_consents(artist_id: str) -> ArtistConsentsResponse:
    artists.get_artist(artist_id)
    return ArtistConsentsResponse(artist_id=artist_id, versions=artists.list_consents(artist_id))


@router.post('/artists/{artist_id}/consents', response_model=ArtistConsentVersion)
async def add_consent(artist_id: str, body: ArtistConsentRequest) -> ArtistConsentVersion:
    permission = artists.add_consent_revision(artist_id, body)
    profile = artists.get_artist(artist_id)
    if body.status == 'revoked' and profile.voice_id is not None:
        # Authorization changes are durable before active workers are drained.
        from ..voice_comparisons import release_voice as release_comparisons
        await release_comparisons(profile.voice_id)
        await voice_build.release_voice(profile.voice_id)
    return permission


@router.get('/artist-production/assets', response_model=SourceAssetsResponse)
def list_assets() -> SourceAssetsResponse:
    return SourceAssetsResponse(assets=evidence.list_assets())


@router.post('/artist-production/assets', response_model=SourceAsset)
async def upload_asset(audio: Annotated[UploadFile, File()],
                       declaration: Annotated[str, Form(max_length=8192)]) -> SourceAsset:
    try:
        request = SourceDeclaration.model_validate_json(declaration)
    except ValidationError:
        await audio.close()
        raise HTTPException(422, detail='invalid_source_declaration') from None
    filename, extension = _filename(audio.filename)
    if db.FILES_DIR.is_symlink():
        await audio.close()
        raise HTTPException(409, detail='invalid_storage')
    root = db.FILES_DIR.resolve() / 'artist-production' / 'sources'
    if root.resolve() != root:
        raise HTTPException(409, detail='invalid_storage')
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'{uuid.uuid4().hex}.{extension}'
    accepted = False
    def register() -> SourceAsset:
        nonlocal accepted
        # Retain a valid original if registration or cancellation leaves commit status uncertain.
        accepted = True
        try:
            source = evidence.register_source(target, request.model_copy(update={'original_filename': filename}))
        except evidence.ProductionError:
            accepted = False
            raise
        accepted = evidence.asset_path(source.id) == target
        return source
    try:
        await _copy_upload(audio, target)
        await _duration(target)
        return await run_owned_thread(register)
    except FileExistsError:
        accepted = True
        raise HTTPException(409, detail='source_upload_conflict') from None
    finally:
        await audio.close()
        if not accepted:
            target.unlink(missing_ok=True)


@router.post('/artist-production/assets/{asset_id}/reviews', response_model=SourceAsset)
def review_asset(asset_id: str, body: RightsReviewRequest) -> SourceAsset:
    return evidence.review_asset(asset_id, body)


@router.post('/artist-production/assets/{asset_id}/declarations', response_model=SourceAsset)
def amend_source(asset_id: str, body: AmendSourceRequest) -> SourceAsset:
    return evidence.amend_source(asset_id, body)


def _view(run: ProductionRun) -> ProductionRun:
    if run.status == 'done' and run.output_asset_id is not None:
        run.gate = evidence.validate_graph(run.output_asset_id)
        if run.audio_version_id is not None:
            try:
                proof = evidence.production_provenance(run.audio_version_id)
                if proof is not None:
                    run.gate = proof.gate
            except (evidence.ProductionError, artists.ArtistError) as exc:
                run.gate.approved_for_release = False
                run.gate.blockers.append(exc.code)
    return run


@router.get('/artist-production/runs', response_model=ProductionRunsResponse)
def list_runs() -> ProductionRunsResponse:
    return ProductionRunsResponse(runs=[_view(run) for run in evidence.list_runs()])


@router.post('/artist-production/runs', response_model=ProductionRun)
async def create_run(body: CleanRenderRequest) -> ProductionRun:
    return await production_render.start_clean_render(body)


@router.post('/artist-production/runs/{run_id}/release-review', response_model=ProductionRun)
def review_release(run_id: str, body: ReleaseReviewRequest) -> ProductionRun:
    return _view(evidence.review_release(run_id, body))


@router.get('/artist-production/usage-ledger', response_model=ModelUsageLedger)
def usage_ledger() -> ModelUsageLedger:
    return evidence.usage_ledger()


@router.post('/voices/{voice_id}/retention', response_model=VoiceDeletionPlan)
def deletion_plan(voice_id: str) -> VoiceDeletionPlan:
    return voice_retention.plan_deletion(voice_id)


@router.get('/voice-retention/{plan_id}', response_model=VoiceDeletionPlan)
def get_deletion_plan(plan_id: str) -> VoiceDeletionPlan:
    return voice_retention.get_plan(plan_id)


@router.get('/voice-retention/{plan_id}/receipt', response_model=VoiceDeletionReceipt)
def deletion_receipt(plan_id: str) -> VoiceDeletionReceipt:
    return voice_retention.get_receipt(plan_id)


@router.post('/voice-retention/{plan_id}/confirm', response_model=VoiceDeletionReceipt)
async def confirm_deletion(plan_id: str, body: ConfirmVoiceDeletionRequest) -> VoiceDeletionReceipt:
    return await voice_retention.execute_deletion(plan_id, body)
