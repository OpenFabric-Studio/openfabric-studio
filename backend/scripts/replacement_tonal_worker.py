"""Private CPU diagnostic worker; the parent owns this process and descendants."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import TypeAdapter, ValidationError

from app.atomic_files import write_object
from app.contracts import JsonValue
from app.music_analysis import MusicAnalysisError
from app.replacement_tonal import analyze_tonal, TonalError
from app.replacement_tonal_contracts import ReplacementTonalWorkerOutcome, ReplacementTonalWorkerRequest
from scripts.music_analysis_worker import run_command

_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


async def main(input_path: Path, output_path: Path) -> int:
    root = input_path.parent
    if (not root.is_absolute() or root.resolve() != root or root.is_symlink() or not root.is_dir()
            or input_path.name != 'input.json' or output_path != root / 'output.json'
            or input_path.is_symlink() or output_path.exists() or output_path.is_symlink()
            or not input_path.is_file() or input_path.stat().st_size > 65536):
        return 1
    content = input_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        request = ReplacementTonalWorkerRequest.model_validate_json(content)
        outcome = ReplacementTonalWorkerOutcome(input_sha256=digest, result=await analyze_tonal(request, root, run_command))
    except TonalError as error:
        outcome = ReplacementTonalWorkerOutcome(input_sha256=digest, error_code=error.code)
    except (ValueError, ValidationError):
        outcome = ReplacementTonalWorkerOutcome(input_sha256=digest, error_code='replacement_tonal_invalid_audio')
    except MemoryError:
        outcome = ReplacementTonalWorkerOutcome(input_sha256=digest, error_code='replacement_tonal_budget_exceeded')
    except (OSError, MusicAnalysisError):
        outcome = ReplacementTonalWorkerOutcome(input_sha256=digest, error_code='replacement_tonal_unavailable')
    payload = _json.validate_json(outcome.model_dump_json())
    if not isinstance(payload, dict):
        return 1
    write_object(output_path, payload)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.input, args.output)))
