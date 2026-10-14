"""Registered artists cannot bypass permission checks through legacy entry points."""
from __future__ import annotations

from fastapi import HTTPException

from .artist_contracts import ArtistConsentVersion, ArtistOperation, ArtistUse


def authorize(voice_id: str, operation: ArtistOperation, *, use: ArtistUse = 'production') -> ArtistConsentVersion | None:
    from .artists import ArtistError, require_authorized_voice
    from .voice_retention import require_not_frozen
    require_not_frozen(voice_id)
    try:
        return require_authorized_voice(voice_id, operation, use=use)
    except ArtistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def recheck(voice_id: str, permission: ArtistConsentVersion | None, operation: ArtistOperation,
            *, use: ArtistUse = 'production') -> None:
    from .artists import ArtistError, require_authorized
    current = authorize(voice_id, operation, use=use)
    if permission is None:
        if current is not None:
            raise HTTPException(status_code=409, detail='artist_binding_changed')
        return
    try:
        require_authorized(permission.artist_id, permission.id, operation, use=use)
    except ArtistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def record(permission: ArtistConsentVersion | None, operation: ArtistOperation, outcome: str,
           identifier: str, *, voice_id: str, track_id: int | None = None, error_code: str = '') -> None:
    if permission is None:
        return
    from .artist_production import append_usage
    from .artist_production_contracts import UsageEventInput
    append_usage(UsageEventInput.model_validate({'idempotency_key': f'{operation}:{identifier}:{outcome}',
        'operation': operation, 'outcome': outcome, 'artist_id': permission.artist_id,
        'consent_version_id': permission.id, 'track_id': track_id, 'error_code': error_code,
        'voice_model_revision': None}))
