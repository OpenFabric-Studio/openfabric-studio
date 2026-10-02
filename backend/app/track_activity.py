"""Small catalog-backed activity projection for feed reloads."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from . import audio_exports, audio_version_store, audio_versions, db
from .contracts import Contract


class TrackActivityResponse(Contract):
    track_ids: list[Annotated[int, Field(gt=0, le=9_007_199_254_740_991)]]


def get_activity() -> TrackActivityResponse:
    candidates = {version.public.track_id for version in audio_version_store.list_active(db.get_db())}
    active = {track_id for track_id in candidates
              if any(version.status in {"queued", "running"}
                     for version in audio_versions.list_versions(track_id).versions)}
    return TrackActivityResponse(track_ids=sorted(active | audio_exports.active_track_ids()))
