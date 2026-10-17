"""Compose advisory CPU measurements under the replacement attempt's owner."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Literal

from .audio_quality import RunCommand
from .replacement_contracts import ReplacementErrorCode, ReplacementPlanResponse
from .replacement_diagnostics_contracts import ReplacementDiagnostics, diagnostic_digest
from .replacement_diagnostics_runtime import pitch_python
from .replacement_pitch import PitchDiagnosticError, analyze_owned_pitch
from .replacement_pitch_contracts import DiagnosticArtifactIdentity, PitchWorkerArtifact, PitchWorkerInput
from .replacement_tonal import TonalError, analyze_owned_tonal
from .replacement_tonal_contracts import ReplacementTonalWorkerRequest

logger = logging.getLogger(__name__)


class DiagnosticWorkerUnavailable(Exception):
    """Only the owner may raise this after proving that its child drained."""


class DiagnosticsError(Exception):
    def __init__(self, code: ReplacementErrorCode) -> None:
        self.code = code
        super().__init__(code)


async def measure_diagnostics(plan: ReplacementPlanResponse, attempt_id: str, artifact_root: Path, work: Path,
                              reference: DiagnosticArtifactIdentity, backing: DiagnosticArtifactIdentity,
                              run: RunCommand, *, fit_input: DiagnosticArtifactIdentity | None = None,
                              fitted: bool = False, converted: DiagnosticArtifactIdentity | None = None,
                              matched: DiagnosticArtifactIdentity | None = None) -> ReplacementDiagnostics:
    artifacts = [PitchWorkerArtifact(identity=reference, role='reference')]
    if fit_input is not None and fit_input.artifact_id != reference.artifact_id:
        artifacts.append(PitchWorkerArtifact(identity=fit_input, role='candidate',
            timeline='reference' if fitted else 'prepared',
            applied_pitch_semitones=plan.request.fit.pitch_semitones if fitted else 0.0))
    if converted is not None and converted.artifact_id not in {item.identity.artifact_id for item in artifacts}:
        artifacts.append(PitchWorkerArtifact(identity=converted, role='converted',
            applied_pitch_semitones=plan.request.fit.pitch_semitones))
    alignment = plan.alignment
    pitch_request = PitchWorkerInput(artifact_root=str(artifact_root), artifacts=artifacts,
        alignment_anchors=alignment.anchors if alignment and fit_input and not fitted else [],
        alignment_reviewed=plan.alignment_reviewed, current_pitch_semitones=plan.request.fit.pitch_semitones,
        prepared_recommendation=plan.request.mode != 'convert')
    unavailable: list[Literal['pitch', 'tonal']] = []
    pitch_report = None
    try:
        python = await pitch_python(run)
        if python is None:
            unavailable.append('pitch')
        else:
            pitch_report = await analyze_owned_pitch(pitch_request, work, run, python=python)
    except DiagnosticWorkerUnavailable:
        unavailable.append('pitch')
    except PitchDiagnosticError as error:
        if error.code == 'pitch_artifact_changed':
            raise DiagnosticsError('replacement_artifact_changed') from error
        if error.code in {'pitch_input_invalid', 'pitch_worker_output_invalid'}:
            raise DiagnosticsError('replacement_output_invalid') from error
        logger.warning('Replacement pitch diagnostic unavailable: %s', error.code)
        unavailable.append('pitch')
    tonal_report = None
    try:
        tonal_report = await analyze_owned_tonal(ReplacementTonalWorkerRequest(plan_id=plan.id, revision=plan.revision,
            artifact_root=str(artifact_root), source_vocal=reference, backing=backing,
            new_vocal=converted or fit_input, new_vocal_aligned=fitted, matched_vocal=matched), work, run)
    except DiagnosticWorkerUnavailable:
        unavailable.append('tonal')
    except TonalError as error:
        if error.code == 'replacement_tonal_source_changed':
            raise DiagnosticsError('replacement_artifact_changed') from error
        if error.code == 'replacement_tonal_invalid_audio':
            raise DiagnosticsError('replacement_output_invalid') from error
        logger.warning('Replacement tonal diagnostic unavailable: %s', error.code)
        unavailable.append('tonal')
    return ReplacementDiagnostics(revision=plan.revision,
        request_sha256=diagnostic_digest(plan.request.model_dump(mode='json')),
        alignment_sha256=diagnostic_digest({'alignment': alignment.model_dump(mode='json'),
            'reviewed_phrase_ids': plan.reviewed_phrase_ids, 'alignment_reviewed': plan.alignment_reviewed}) if alignment else None,
        attempt_id=attempt_id, created_at=datetime.now(timezone.utc).isoformat(),
        pitch=pitch_report, tonal=tonal_report, unavailable=unavailable)
