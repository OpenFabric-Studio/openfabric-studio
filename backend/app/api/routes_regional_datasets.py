"""Bounded local review and retained-copy preparation; never accepts filesystem paths."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import FileResponse, Response
from starlette.types import Message

from .. import regional_datasets as service, regional_evaluation as evaluation
from ..module_security import require_local_origin
from ..music_analysis_contracts import MusicAnalysisId, MusicAnalysisSha256
from ..regional_dataset_contracts import (RegionalDatasetError, RegionalDatasetInspectionRequest,
    RegionalDatasetPreview, RegionalDatasetReview, RegionalDatasetReviewRequest, RegionalManifestSelection,
    RegionalPreparationJob, RegionalPreparationJobs, RegionalPreparationRequest, RegionalPreparedManifest,
    RegionalRecordingInspection, RegionalPreparedSelection, RegionalTrainingSelection, RegionalTrainingSubmission)
from ..stable_lora_contracts import StableLoraTrainingJob
from ..music_lora_contracts import MusicLoraError
from ..local_music_contracts import LocalMusicError
from ..regional_evaluation_contracts import (RegionalBlindLabel, RegionalBlindReveal, RegionalBlindReview,
    RegionalBlindSession, RegionalBlindSessionRequest, RegionalComparisonArmId, RegionalComparisonPlan,
    RegionalComparisonState, RegionalComparisons, RegionalEvaluationRequest, RegionalListeningReview,
    RegionalModelCard)
from ..regional_acoustic_contracts import RegionalAcousticScanRequest, RegionalAcousticJob, RegionalAcousticJobs
from ..video_projects import VideoProjectError
from .routes_music_terms import require_usage

logger = logging.getLogger(__name__)


class RegionalDatasetRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            count = 0

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > 8 * 1024**2:
                        raise HTTPException(413, 'regional_dataset_limit')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except (RequestValidationError, ValidationError) as error:
                raise HTTPException(422, 'regional_dataset_invalid') from error
            except RegionalDatasetError as error:
                raise HTTPException(error.status, error.code) from error
            except (MusicLoraError, LocalMusicError) as error:
                raise HTTPException(error.status, error.code) from error
            except VideoProjectError as error:
                raise HTTPException(409, 'regional_dataset_source_changed') from error
            except (OSError, sqlite3.Error, ValueError) as error:
                logger.exception('Regional dataset storage operation failed')
                raise HTTPException(503, 'regional_dataset_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music/regional-datasets', tags=['regional-datasets'], route_class=RegionalDatasetRoute)


@router.post('/acoustic-scans', response_model=RegionalAcousticJob, status_code=202)
async def acoustic_start(body: RegionalAcousticScanRequest) -> RegionalAcousticJob:
    return await service.start_acoustic_scan(body)


@router.get('/acoustic-scans', response_model=RegionalAcousticJobs)
async def acoustic_scans(dataset_id: MusicAnalysisId | None = None) -> RegionalAcousticJobs:
    return service.list_acoustic_scans(dataset_id)


@router.get('/acoustic-scans/{identifier}', response_model=RegionalAcousticJob)
async def acoustic_get(identifier: MusicAnalysisId) -> RegionalAcousticJob:
    return service.get_acoustic_scan(identifier)


@router.post('/acoustic-scans/{identifier}/cancel', response_model=RegionalAcousticJob)
async def acoustic_cancel(identifier: MusicAnalysisId) -> RegionalAcousticJob:
    return await service.cancel_acoustic_scan(identifier)


@router.get('/acoustic-scans/{identifier}/candidates/{candidate_id}/audio/{side}')
async def acoustic_audio(identifier: MusicAnalysisId, candidate_id: MusicAnalysisSha256, side: Literal['left', 'right']) -> FileResponse:
    return FileResponse(await service.acoustic_audition(identifier, candidate_id, side), media_type='audio/wav',
        filename=candidate_id + '-' + side + '.wav', headers={'Cache-Control': 'no-store'})


@router.post('/inspect', response_model=RegionalRecordingInspection)
async def inspect(body: RegionalDatasetInspectionRequest) -> RegionalRecordingInspection:
    return await service.inspect_recording(body)


@router.put('/reviews', response_model=RegionalDatasetReview)
async def review(body: RegionalDatasetReviewRequest) -> RegionalDatasetReview:
    return await service.save_review(body)


@router.get('/reviews/{dataset_id}', response_model=RegionalDatasetReview)
async def get_review(dataset_id: MusicAnalysisId) -> RegionalDatasetReview:
    return service.get_review(dataset_id)


@router.post('/preview', response_model=RegionalDatasetPreview)
async def preview(body: RegionalManifestSelection) -> RegionalDatasetPreview:
    return await service.preview(body)


@router.get('/preparations', response_model=RegionalPreparationJobs)
async def preparations(dataset_id: MusicAnalysisId | None = None) -> RegionalPreparationJobs:
    return service.list_preparations(dataset_id)


@router.post('/preparations', response_model=RegionalPreparationJob)
async def start(body: RegionalPreparationRequest) -> RegionalPreparationJob:
    return await service.start(body)


@router.get('/preparations/{identifier}', response_model=RegionalPreparationJob)
async def get(identifier: MusicAnalysisId) -> RegionalPreparationJob:
    return service.get(identifier)


@router.post('/preparations/{identifier}/cancel', response_model=RegionalPreparationJob)
async def cancel(identifier: MusicAnalysisId) -> RegionalPreparationJob:
    return await service.cancel(identifier)


@router.post('/preparations/{identifier}/retry-save', response_model=RegionalPreparationJob)
async def retry_save(identifier: MusicAnalysisId) -> RegionalPreparationJob:
    return await service.retry_save(identifier)


@router.get('/preparations/{identifier}/manifest', response_model=RegionalPreparedManifest)
async def manifest(identifier: MusicAnalysisId) -> RegionalPreparedManifest:
    return service.get_manifest(identifier)


@router.get('/preparations/{identifier}/excerpts/{excerpt_id}')
async def excerpt_audio(identifier: MusicAnalysisId, excerpt_id: MusicAnalysisId) -> FileResponse:
    return FileResponse(await service.excerpt_audio(identifier, excerpt_id), media_type='audio/wav',
        filename=excerpt_id + '.wav', headers={'Cache-Control': 'no-store'})


@router.post('/training-selection', response_model=RegionalTrainingSelection)
async def training_selection(body: RegionalPreparedSelection) -> RegionalTrainingSelection:
    return service.training_selection(body)


@router.post('/training', response_model=StableLoraTrainingJob)
async def training(body: RegionalTrainingSubmission) -> StableLoraTrainingJob:
    require_usage('stable_audio3', 'training')
    return await service.submit_training(body)


@router.post('/comparisons', response_model=RegionalComparisonPlan)
async def create_comparison(body: RegionalEvaluationRequest) -> RegionalComparisonPlan:
    return await evaluation.create(body)


@router.get('/comparisons', response_model=RegionalComparisons)
async def comparisons(manifest_id: MusicAnalysisId) -> RegionalComparisons:
    return evaluation.list_comparisons(manifest_id)


@router.get('/comparisons/{identifier}', response_model=RegionalComparisonState)
async def comparison(identifier: MusicAnalysisId) -> RegionalComparisonState:
    return evaluation.get(identifier)


@router.post('/comparisons/{identifier}/arms/{arm}/submit', response_model=RegionalComparisonState)
async def submit_arm(identifier: MusicAnalysisId, arm: RegionalComparisonArmId) -> RegionalComparisonState:
    require_usage('stable_audio3', 'generation')
    return await evaluation.submit_arm(identifier, arm)


@router.post('/comparisons/{identifier}/arms/{arm}/collect', response_model=RegionalComparisonState)
async def collect_arm(identifier: MusicAnalysisId, arm: RegionalComparisonArmId) -> RegionalComparisonState:
    return await evaluation.collect(identifier, arm)


@router.get('/comparisons/{identifier}/audio/{arm}')
async def comparison_audio(identifier: MusicAnalysisId, arm: RegionalComparisonArmId) -> FileResponse:
    return FileResponse(await evaluation.audio(identifier, arm), media_type='audio/wav', headers={'Cache-Control': 'no-store'})


@router.post('/comparisons/{identifier}/reviews', response_model=RegionalComparisonState)
async def listening_review(identifier: MusicAnalysisId, body: RegionalListeningReview) -> RegionalComparisonState:
    return await evaluation.review(identifier, body)


@router.post('/comparisons/{identifier}/blind-sessions', response_model=RegionalBlindSession)
async def blind_session(identifier: MusicAnalysisId, body: RegionalBlindSessionRequest) -> RegionalBlindSession:
    return await evaluation.open_blind_session(identifier, body.reviewer_reference)


@router.get('/comparisons/{identifier}/blind/{reviewer}/audio/{label}')
async def blind_comparison_audio(identifier: MusicAnalysisId, reviewer: str, label: RegionalBlindLabel) -> FileResponse:
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', reviewer) is None:
        raise HTTPException(422, 'regional_dataset_invalid')
    return FileResponse(await evaluation.blind_audio(identifier, reviewer, label), media_type='audio/wav',
        filename=evaluation.blind_download_name(label), content_disposition_type='inline', headers={'Cache-Control': 'no-store'})


@router.post('/comparisons/{identifier}/blind-reviews', response_model=RegionalBlindReveal)
async def blind_review(identifier: MusicAnalysisId, body: RegionalBlindReview) -> RegionalBlindReveal:
    return await evaluation.submit_blind_review(identifier, body)


@router.get('/comparisons/{identifier}/model-card', response_model=RegionalModelCard)
async def comparison_model_card(identifier: MusicAnalysisId) -> RegionalModelCard:
    return await evaluation.model_card(identifier)
