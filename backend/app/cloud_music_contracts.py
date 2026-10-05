"""Cloud song generation shares the track library, not local-engine settings."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, StrictBool
from .contracts import SavedTrack
from .openrouter_contracts import Digest, Identifier, OpenRouterMusicRequest, OpenRouterReceipt, ProviderContract

class CloudMusicSubmitRequest(OpenRouterMusicRequest):
    title: str = Field(default='',max_length=500)
    quote_id: Identifier
    transfers_confirmed: StrictBool

class CloudMusicJob(ProviderContract):
    id: Identifier
    status: Literal['queued','running','done','failed','submission_unknown','canceled_tracking','interrupted']
    created_at: str
    title: str = Field(max_length=500)
    request: OpenRouterMusicRequest
    receipt_id: Identifier
    receipt: OpenRouterReceipt | None = None
    track: SavedTrack | None = None
    output_sha256: Digest | None = None
    can_retry_save: bool = False
    error_code: str = Field(default='',max_length=80)

class CloudMusicJobs(ProviderContract):
    history_incomplete: bool = False
    jobs: list[CloudMusicJob] = Field(max_length=1000)

CLOUD_MUSIC_CLIENT_MODELS: list[type[BaseModel]] = [CloudMusicSubmitRequest,CloudMusicJob,CloudMusicJobs]
