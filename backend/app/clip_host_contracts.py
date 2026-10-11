"""Public tunnel preferences and private, receipt-bound clip capabilities."""
from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

Identifier=Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]
Digest=Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]


class ClipHostContract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class ClipHostSettingsRequest(ClipHostContract):
    enabled: StrictBool=False


class ClipHostStatus(ClipHostSettingsRequest):
    configured: bool=False
    binary_available: bool=False
    tunnel_active: bool=False
    active_clips: int=Field(default=0,ge=0)


class HostedClip(ClipHostContract):
    receipt_id: Identifier
    source_sha256: Digest
    url: str=Field(min_length=1,max_length=2048,repr=False)
    expires_at: AwareDatetime

    @model_validator(mode='after')
    def owned_url(self) -> HostedClip:
        value=urlsplit(self.url)
        if (value.scheme!='https' or value.username is not None or value.password is not None
                or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com',value.netloc) is None
                or re.fullmatch(r'/clip/[0-9a-f]{64}\.mp4',value.path) is None or value.query or value.fragment):
            raise ValueError('clip_host_url_invalid')
        return self


CLIP_HOST_CLIENT_MODELS: list[type[BaseModel]]=[ClipHostSettingsRequest,ClipHostStatus]
