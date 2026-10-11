"""App-owned provider contracts. Provider wire payloads never become client types."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

MediaKind = Literal['video', 'speech', 'music', 'image']
ProviderId = Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9._-]{0,79}/[a-z0-9][a-z0-9._:-]{0,119}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Usd = Annotated[float, Field(ge=0, le=1000000, allow_inf_nan=False)]

class ProviderContract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class OpenRouterSettingsRequest(ProviderContract):
    enabled: StrictBool = False
    estimate_limit_usd: Annotated[float, Field(gt=0, le=1000)] | None = 1.0

class OpenRouterKeyRequest(ProviderContract):
    api_key: str = Field(min_length=16, max_length=512, repr=False, pattern=r'^[A-Za-z0-9_-]+$')
    persist: StrictBool = True

class OpenRouterStatus(OpenRouterSettingsRequest):
    credential_configured: bool = False
    credential_source: Literal['none','session','environment','secure_store'] = 'none'
    secure_storage_available: bool = False

class OpenRouterConnection(ProviderContract):
    connected: bool
    limit_usd: Usd | None = None
    limit_remaining_usd: Usd | None = None
    usage_usd: Usd = 0
    is_free_tier: bool = False
    checked_at: str

class OpenRouterPrice(ProviderContract):
    unit: Literal['second','character','utf8_byte','request']
    rate_usd: Usd
    source: Literal['live_catalog','published_model_page']
    resolution: str | None = Field(default=None, max_length=20)
    generate_audio: bool | None = None
    minimum_usd: Usd = 0

class OpenRouterModel(ProviderContract):
    id: ProviderId
    name: str = Field(min_length=1,max_length=160)
    kind: MediaKind
    fingerprint: Digest
    prices: list[OpenRouterPrice] = Field(min_length=1,max_length=32)
    supported_durations: list[int] = Field(default_factory=list,max_length=60)
    supported_resolutions: list[str] = Field(default_factory=list,max_length=20)
    supported_aspect_ratios: list[str] = Field(default_factory=list,max_length=20)
    supported_sizes: list[str] = Field(default_factory=list,max_length=40)
    supported_frame_images: list[Literal['first_frame','last_frame']] = Field(default_factory=list,max_length=2)
    supports_generate_audio: bool = False
    supports_seed: bool = False
    supports_image_editing: bool = False
    supports_video_editing: bool = False
    supports_voice_cloning: bool = False
    supported_voices: list[str] = Field(default_factory=list,max_length=256)
    input_character_limit: int = Field(default=4096,ge=1,le=10000)
    warnings: list[str] = Field(default_factory=list,max_length=10)

class OpenRouterCatalog(ProviderContract):
    models: list[OpenRouterModel] = Field(default_factory=list,max_length=20)
    fingerprint: Digest
    fetched_at: str
    expires_at: float = Field(ge=0)

class OpenRouterQuoteRequest(ProviderContract):
    kind: MediaKind
    workflow: Literal['generate','image_edit','video_edit'] = 'generate'
    model_id: ProviderId
    text: str = Field(default='',max_length=10000,repr=False)
    duration_seconds: int | None = Field(default=None,ge=1,le=60)
    size: str | None = Field(default=None,max_length=20,pattern=r'^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$')
    resolution: str | None = Field(default=None,max_length=20)
    aspect_ratio: str | None = Field(default=None,max_length=10)
    generate_audio: StrictBool = False
    voice: str | None = Field(default=None,max_length=160)
    seed: int | None = Field(default=None,ge=0,le=2147483647)
    reference_bytes: int = Field(default=0,ge=0,le=15*1024*1024)
    reference_sha256: Digest | None = None
    reference_transcript: str | None = Field(default=None,max_length=10000,repr=False)
    source_bytes: int = Field(default=0,ge=0,le=20*1024*1024)
    source_sha256: Digest | None = None

    @model_validator(mode='after')
    def reference_identity(self) -> OpenRouterQuoteRequest:
        if bool(self.reference_bytes) != bool(self.reference_sha256):
            raise ValueError('reference_identity_required')
        if bool(self.source_bytes) != bool(self.source_sha256):
            raise ValueError('source_identity_required')
        return self

class OpenRouterQuote(ProviderContract):
    id: Identifier
    kind: MediaKind
    model_id: ProviderId
    model_fingerprint: Digest
    request_fingerprint: Digest
    estimated_usd: Usd
    expires_at: float = Field(ge=0)
    ceiling_is_estimate: Literal[True] = True
    transfers: list[Literal['prompt','text','reference_image','reference_audio','source_frame','reference_video']] = Field(max_length=4)
    warnings: list[str] = Field(default_factory=list,max_length=10)

class OpenRouterVideoRequest(ProviderContract):
    model: ProviderId
    prompt: str = Field(min_length=1,max_length=4000,repr=False)
    duration: int = Field(ge=1,le=60)
    size: str = Field(pattern=r'^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$')
    resolution: str | None = Field(default=None,max_length=20)
    aspect_ratio: str | None = Field(default=None,max_length=10)
    generate_audio: StrictBool = False
    seed: int | None = Field(default=None,ge=0,le=2147483647)
    reference_image: str | None = Field(default=None,max_length=21*1024*1024,repr=False)

class OpenRouterImageEditRequest(ProviderContract):
    model: Literal['bytedance-seed/seedream-4.5'] = 'bytedance-seed/seedream-4.5'
    prompt: str = Field(min_length=1,max_length=4000,repr=False)
    source_frame: str = Field(min_length=1,max_length=5000000,repr=False)
    reference_image: str = Field(min_length=1,max_length=5000000,repr=False)
    aspect_ratio: str = Field(max_length=10,pattern=r'^[1-9][0-9]?:[1-9][0-9]?$')
    seed: int = Field(ge=0,le=2147483647,strict=True)

class OpenRouterVideoEditRequest(ProviderContract):
    model: Literal['runway/aleph-2'] = 'runway/aleph-2'
    prompt: str = Field(min_length=1,max_length=4000,repr=False)
    reference_video: str = Field(min_length=1,max_length=5000000,repr=False)
    reference_image: str = Field(min_length=1,max_length=5000000,repr=False)
    duration: int = Field(ge=2,le=8,strict=True)
    width: int = Field(ge=480,le=1920,strict=True)
    height: int = Field(ge=480,le=1920,strict=True)
    seed: int | None = Field(default=None,ge=0,le=2147483647,strict=True)

    @model_validator(mode='after')
    def native_geometry(self) -> OpenRouterVideoEditRequest:
        if min(self.width,self.height)>1080 or self.width*self.height>1920*1080 or self.width%2 or self.height%2:
            raise ValueError('video_parameters_unsupported')
        return self

class OpenRouterSpeechRequest(ProviderContract):
    model: ProviderId
    input: str = Field(min_length=1,max_length=4096,repr=False)
    voice: str | None = Field(default=None,max_length=160)
    reference_audio: str | None = Field(default=None,max_length=21*1024*1024,repr=False)
    reference_transcript: str | None = Field(default=None,max_length=10000,repr=False)

class OpenRouterMusicRequest(ProviderContract):
    model: ProviderId
    prompt: str = Field(min_length=1,max_length=4000,repr=False)
    seed: int | None = Field(default=None,ge=0,le=2147483647)

class OpenRouterReceipt(ProviderContract):
    id: Identifier
    owner_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,160}$')
    kind: MediaKind
    model_id: ProviderId
    model_fingerprint: Digest
    request_fingerprint: Digest
    quote_id: Identifier
    estimated_usd: Usd
    state: Literal['intent','submitting','submission_unknown','submitted','completed','failed','canceled_tracking']
    remote_id: str | None = Field(default=None,max_length=160,pattern=r'^[A-Za-z0-9_-]+$')
    actual_cost_usd: Usd | None = None
    error_code: str | None = Field(default=None,max_length=80,pattern=r'^[a-z_]+$')
    provider_http_status: int | None = Field(default=None,ge=100,le=599,strict=True)
    created_at: str
    updated_at: str

class OpenRouterReceipts(ProviderContract):
    history_incomplete: bool = False
    requests: list[OpenRouterReceipt] = Field(max_length=1000)

class OpenRouterVideoJob(ProviderContract):
    id: str = Field(min_length=1,max_length=160,pattern=r'^[A-Za-z0-9_-]+$')
    status: Literal['pending','queued','processing','completed','failed','cancelled','expired']
    progress: float | None = Field(default=None,ge=0,le=100)
    actual_cost_usd: Usd | None = None

OPENROUTER_CLIENT_MODELS: list[type[BaseModel]] = [OpenRouterStatus,OpenRouterSettingsRequest,OpenRouterKeyRequest,OpenRouterConnection,OpenRouterCatalog,OpenRouterQuoteRequest,OpenRouterQuote,OpenRouterReceipt,OpenRouterReceipts,OpenRouterMusicRequest]
