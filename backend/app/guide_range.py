"""Owned CPU worker: bounded pYIN windows and honest original-time occupancy."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .singer_range_contracts import GuideRangeInput, PlannedNote, RangeAssessment
from .singer_ranges import coverage_notes, evaluate_range
from .voice_contracts import VoiceCoverageMeasurement
from .voice_coverage import CoverageError, summarize_coverage

MAX_GUIDE_SECONDS = 7200
MAX_GUIDE_BYTES = 2 * 1024 * 1024 * 1024
WINDOW_SECONDS = 5
MAX_MANIFEST_BYTES = 64 * 1024


class GuideRangeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_GUIDE_BYTES:
        raise GuideRangeError('guide_invalid_file')
    result, total = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_GUIDE_BYTES:
                raise GuideRangeError('guide_decode_limit')
            result.update(chunk)
    return result.hexdigest()


def analyze_guide(request: GuideRangeInput) -> RangeAssessment:
    from . import voice_coverage
    notes: list[PlannedNote] = []
    measurements: list[VoiceCoverageMeasurement] = []
    failed, error, digest = 0, '', None
    try:
        digest = _digest(Path(request.guide_path))
        if digest != request.guide_sha256:
            raise GuideRangeError('guide_identity_changed')
        import numpy as np
        import soundfile as sf
        info = sf.info(request.guide_path)
        if not 1 <= info.samplerate <= 192000 or not 1 <= info.channels <= 8 or not 0 < info.frames <= MAX_GUIDE_SECONDS * info.samplerate:
            raise GuideRangeError('guide_decode_limit')
        position = 0
        with sf.SoundFile(request.guide_path) as source:
            while position < info.frames:
                count = min(WINDOW_SECONDS * info.samplerate, info.frames - position)
                audio = source.read(count, dtype='float32', always_2d=True)
                if audio.shape != (count, info.channels) or not np.isfinite(audio).all():
                    raise GuideRangeError('guide_invalid_audio')
                try:
                    measurement = voice_coverage.analyze_coverage(audio, info.samplerate)
                    notes.extend(coverage_notes(measurement, position / info.samplerate))
                    measurements.append(measurement)
                except (CoverageError, ValueError, RuntimeError, ImportError):
                    failed += 1
                    error = 'guide_analysis_failed'
                    break
                position += count
        if digest != _digest(Path(request.guide_path)):
            error = 'guide_identity_changed'
    except GuideRangeError as exc:
        error = exc.code
    except Exception:
        error = 'guide_analysis_failed'
    summary = summarize_coverage(measurements, unavailable_segments=failed)
    assessment = evaluate_range(request.artist_id, request.profile, notes, approval=request.approval,
        analysis_complete=not error and failed == 0, coverage=summary, source_sha256=digest,
        chosen_key=request.chosen_key or None, error_code=error)
    if error:
        assessment = RangeAssessment.model_validate({**assessment.model_dump(),
            'warnings': sorted(set(assessment.warnings) | {'analysis_failed'})})
    return assessment


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != '--manifest':
        print(json.dumps({'error_code': 'invalid_guide_range_arguments'}), file=sys.stderr)
        return 2
    try:
        with Path(argv[2]).open('rb') as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('oversized_manifest')
        request = GuideRangeInput.model_validate_json(raw)
    except (OSError, ValueError):
        print(json.dumps({'error_code': 'invalid_guide_range_manifest'}), file=sys.stderr)
        return 2
    print(analyze_guide(request).model_dump_json(), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
