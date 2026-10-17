"""Copy a trained ACE adapter into a new destination; never delete or overwrite."""
from __future__ import annotations

import asyncio
import errno
import logging
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import sys

from . import config
from .lora_export_contracts import LoraExportRequest, LoraExportResponse, LoraTrainedCheckpoint, LoraTrainedFilesRequest, LoraTrainedFilesResponse
from .job_lifecycle import await_cleanup, kill_process_tree, spawn_process
from .module_catalog import available_bytes, configured_environment
from .video_process import WorkerOutputError, read_owned_output

logger = logging.getLogger(__name__)
_PICK_SCRIPT = 'POSIX path of (choose folder with prompt "Choose a parent folder for the LoRA export")'
_picker_processes: set[asyncio.subprocess.Process] = set()


class LoraExportError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _invalid_path(value: str) -> bool:
    return not value or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value) or '..' in Path(value).parts


def _protected_roots() -> list[Path]:
    environment = configured_environment()
    return [environment.root, environment.data_dir, *environment.paths.values(), Path(__file__).resolve().parents[2] / 'external']


def _destination(value: str) -> Path:
    candidate = Path(value)
    if _invalid_path(value) or not candidate.is_absolute():
        raise LoraExportError('lora_export_destination_unsafe')
    destination = candidate.parent.resolve(strict=True) / candidate.name
    if not destination.parent.is_dir() or any(
        destination.is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination)
        for root in _protected_roots()
    ):
        raise LoraExportError('lora_export_destination_unsafe')
    if os.path.lexists(destination):
        raise LoraExportError('lora_export_destination_exists', 409)
    return destination


def _source(value: str) -> Path:
    relative = Path(value)
    if (_invalid_path(value) or relative.is_absolute() or PureWindowsPath(value).drive
            or '\\' in value or not relative.parts):
        raise LoraExportError('lora_export_source_unsafe')
    engine = config.ACE_STEP_DIR.resolve()
    output = engine / relative
    for candidate in (output, *output.parents):
        if candidate == engine:
            break
        if candidate.is_symlink():
            raise LoraExportError('lora_export_source_unsafe')
    if output == engine or not output.resolve().is_relative_to(engine):
        raise LoraExportError('lora_export_source_unsafe')
    final = output / 'final'
    final_exists = os.path.lexists(final)
    if final_exists and _complete_adapter(final):
        return final
    checkpoints = output / 'checkpoints'
    if checkpoints.is_symlink():
        raise LoraExportError('lora_export_source_unsafe')
    available = [(int(match[1]), child) for child in checkpoints.iterdir()
                 if (match := re.fullmatch(r'epoch_([0-9]+)(?:_loss_-?[0-9]+(?:\.[0-9]+)?)?', child.name))] if checkpoints.is_dir() else []
    for _, source in sorted(available, key=lambda item: item[0], reverse=True):
        if _complete_adapter(source):
            return source
    raise LoraExportError('lora_export_adapter_missing' if final_exists or available else 'lora_export_source_missing', 404)


def _complete_adapter(source: Path) -> bool:
    if source.is_symlink() or not source.is_dir() or source.resolve() != source:
        raise LoraExportError('lora_export_source_unsafe')
    def unreadable(error: OSError) -> None:
        raise error
    for folder, directories, files in os.walk(source, followlinks=False, onerror=unreadable):
        for name in (*directories, *files):
            mode = (Path(folder) / name).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise LoraExportError('lora_export_source_unsafe')
    adapter = source / 'adapter'
    return (adapter / 'adapter_config.json').is_file() and any(
        (adapter / name).is_file() for name in ('adapter_model.safetensors', 'adapter_model.bin')
    )


def _plan(body: LoraExportRequest) -> tuple[Path, Path, int, int]:
    try:
        source, destination = _source(body.lora_output_dir), _destination(body.export_path)
        # Preserve every checkpoint file and reserve 64 MiB for filesystem and metadata writes.
        required = sum(path.stat().st_size for path in source.rglob('*') if path.is_file()) + 64 * 1024**2
        free = available_bytes(destination.parent)
        if free is None or free < required:
            raise LoraExportError('insufficient_disk', 409)
        return source, destination, required, free
    except FileNotFoundError as exc:
        raise LoraExportError('lora_export_destination_unsafe') from exc
    except (OSError, ValueError) as exc:
        logger.exception('LoRA export validation failed')
        raise LoraExportError('lora_export_storage_unavailable', 503) from exc


def _response(source: Path, destination: Path, message: str, required: int, free: int) -> LoraExportResponse:
    return LoraExportResponse(export_path=str(destination), adapter_path=str(destination / 'adapter'), source=str(source), message=message,
        required_free_bytes=required, free_bytes=free)


def preview_export(body: LoraExportRequest) -> LoraExportResponse:
    source, destination, required, free = _plan(body)
    return _response(source, destination, 'Ready to export into a new folder', required, free)


def check_trained_files(body: LoraTrainedFilesRequest) -> LoraTrainedFilesResponse:
    try:
        source = _source(body.lora_output_dir)
        return LoraTrainedFilesResponse(checkpoint=LoraTrainedCheckpoint(
            source=str(source), adapter_path=str(source / 'adapter'),
            kind='final' if source.name == 'final' else 'checkpoint',
        ))
    except LoraExportError as exc:
        if exc.code not in {'lora_export_source_missing', 'lora_export_adapter_missing'}:
            raise
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.exception('LoRA trained-file check failed')
        raise LoraExportError('lora_export_storage_unavailable', 503) from exc
    return LoraTrainedFilesResponse(checkpoint=None)


def _copy_file(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> str:
    path = Path(source)
    if path.resolve(strict=True) != path:
        raise LoraExportError('lora_export_source_unsafe')
    copied_path = Path(destination)
    if copied_path.parent.resolve(strict=True) != copied_path.parent or not copied_path.parent.is_dir():
        raise LoraExportError('lora_export_destination_unsafe')
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as original:
        if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
            raise LoraExportError('lora_export_source_unsafe')
        with copied_path.open('xb') as copied:
            shutil.copyfileobj(original, copied)
    return str(destination)


def _copy_tree(source: Path, destination: Path) -> None:
    # shortcut: path checks do not pin directory handles; use platform handle-based I/O if hostile local writers enter scope.
    if source.resolve(strict=True) != source or not stat.S_ISDIR(source.lstat().st_mode):
        raise LoraExportError('lora_export_source_unsafe')
    if destination.parent.resolve(strict=True) != destination.parent or not destination.parent.is_dir():
        raise LoraExportError('lora_export_destination_unsafe')
    destination.mkdir()
    for child in source.iterdir():
        mode = child.lstat().st_mode
        if stat.S_ISDIR(mode):
            _copy_tree(child, destination / child.name)
        elif stat.S_ISREG(mode):
            _copy_file(child, destination / child.name)
        else:
            raise LoraExportError('lora_export_source_unsafe')


def export_lora(body: LoraExportRequest) -> LoraExportResponse:
    source, destination, required, free = _plan(body)
    response = _response(source, destination, 'LoRA exported successfully', required, free)
    try:
        _copy_tree(source, destination)
    except FileExistsError as exc:
        raise LoraExportError('lora_export_destination_exists', 409) from exc
    except OSError as exc:
        # Leave only the newly created partial copy; cleanup must never delete a racing user file.
        logger.exception('LoRA export copy failed')
        raise LoraExportError('insufficient_disk' if exc.errno == errno.ENOSPC else 'lora_export_copy_failed', 503) from exc
    return response


async def _stop_picker(process: asyncio.subprocess.Process) -> None:
    await await_cleanup(kill_process_tree(process))
    _picker_processes.discard(process)


async def shutdown_pickers() -> None:
    for process in tuple(_picker_processes):
        await _stop_picker(process)


async def pick_parent() -> str | None:
    if sys.platform != 'darwin':
        raise LoraExportError('lora_export_picker_unavailable', 400)
    try:
        process = await spawn_process('osascript', '-e', _PICK_SCRIPT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        _picker_processes.add(process)
        try:
            output = await read_owned_output(process, max_bytes=16384, timeout=600)
        finally:
            await _stop_picker(process)
        value = output.decode('utf-8', errors='strict')
        if process.returncode != 0:
            if 'cancel' in value.lower() or '(-128)' in value:
                return None
            raise LoraExportError('lora_export_picker_failed', 503)
        chosen = value.rstrip('\r\n')
        path = Path(chosen)
        if _invalid_path(chosen) or not path.is_absolute() or not path.is_dir():
            raise LoraExportError('lora_export_picker_failed', 503)
        return str(path.resolve(strict=True))
    except (OSError, ValueError, TimeoutError, WorkerOutputError) as exc:
        logger.exception('LoRA export folder picker failed')
        raise LoraExportError('lora_export_picker_failed', 503) from exc
