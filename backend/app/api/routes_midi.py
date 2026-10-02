"""Audio -> MIDI transcription endpoints (MuScriptor) for a saved track.

Same shape as routes_stems.py (start/cancel/status/download per track), but
addressed per *source*: the full mix and each separated stem are transcribed
independently, so "drums only" doesn't mean re-running the whole mix.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import db, midi
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus

from ..client_contracts import MidiStatusResponse

router = APIRouter(prefix="/api/tracks", tags=["midi"])


def _midi_urls(track_id: int) -> dict[str, str]:
    return {source: f"/api/tracks/{track_id}/midi/{source}" for source in db.get_track_midi(track_id)}


def _payload(track_id: int) -> dict:
    # `available` is what the track actually has audio for right now (the mix
    # always, each stem only once Demucs has run), so the UI can list sources
    # without separately querying the stems endpoint.
    available = [s for s in midi.SOURCES if (p := midi.source_audio_path(track_id, s)) and p.exists()]
    return {"sources": midi.status_all(track_id), "urls": _midi_urls(track_id), "available": available}


@router.get("/{track_id}/midi/status", response_model=MidiStatusResponse)
async def midi_status(track_id: int):
    if not db.get_track(track_id):
        raise HTTPException(status_code=404, detail="track not found")
    return _payload(track_id)


@router.post("/{track_id}/midi/{source}/cancel", response_model=MidiStatusResponse)
async def cancel_midi(track_id: int, source: str):
    if source not in midi.SOURCES:
        raise HTTPException(status_code=404, detail="unknown source")
    await midi.cancel(track_id, source)
    return _payload(track_id)


@router.post("/{track_id}/midi/{source}", response_model=MidiStatusResponse)
async def start_midi(track_id: int, source: str, force: bool = False):
    if source not in midi.SOURCES:
        raise HTTPException(status_code=404, detail="unknown source")
    if not db.get_track(track_id):
        raise HTTPException(status_code=404, detail="track not found")
    # MuScriptor is loaded into YuE2's server, so there is nothing to talk to
    # unless YuE2 is the active model - refuse now instead of queueing a job
    # that can only fail.
    if manager.state.models["yue2"].status != ModelStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="MIDI-транскрипция работает через сервер YuE2 — сначала сделайте YuE2 активной моделью",
        )
    path = midi.source_audio_path(track_id, source)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"audio for source '{source}' not found")
    await midi.start(track_id, source, force=force)
    return _payload(track_id)


@router.delete("/{track_id}/midi")
async def delete_midi(track_id: int):
    if not db.get_track(track_id):
        raise HTTPException(status_code=404, detail="track not found")
    if midi.any_active(track_id):
        raise HTTPException(status_code=409, detail="transcription is in progress")
    if not db.delete_track_midi(track_id):
        raise HTTPException(status_code=404, detail="no MIDI to delete")
    midi.forget(track_id)
    return {"deleted": True}


@router.get("/{track_id}/midi/{source}")
async def midi_file(track_id: int, source: str):
    if source not in midi.SOURCES:
        raise HTTPException(status_code=404, detail="unknown source")
    path = db.get_track_midi(track_id).get(source)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="MIDI file not found")
    return FileResponse(path, media_type="audio/midi", filename=f"{source}_{track_id}.mid")
