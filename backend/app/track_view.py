"""Validated track views shared by uploads, history and durable generation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from .contracts import SavedTrack


def track_response(row: sqlite3.Row) -> SavedTrack:
    audio_path = Path(row["audio_path"])
    return SavedTrack.model_validate({
        "id": row["id"],
        "short_id": row["short_id"],
        "is_favorite": row["is_favorite"] == 1,
        "model": row["model"],
        "created_at": row["created_at"],
        "title": row["title"],
        "lyrics": row["lyrics"],
        "seed": row["seed"],
        "duration_ms": row["duration_ms"],
        "wall_ms": row["wall_ms"],
        "params": json.loads(row["params_json"] or "{}"),
        "filename": audio_path.name,
        "audio_url": f"/api/tracks/{row['id']}/audio",
        "abc_url": f"/api/tracks/{row['id']}/abc" if row["abc_path"] else None,
        "stems": (
            {n: f"/api/tracks/{row['id']}/stems/{n}" for n in json.loads(row["stems_json"]).keys()}
            if row["stems_json"]
            else None
        ),
        "midi": (
            {s: f"/api/tracks/{row['id']}/midi/{s}" for s in json.loads(row["midi_json"]).keys()}
            if row["midi_json"]
            else None
        ),
    })
