"""Private launch identity for desktop readiness; source launches stay unchanged."""
from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/desktop")


class DesktopReady(BaseModel):
    service: Literal["openfabric-studio"] = "openfabric-studio"
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    pid: int = Field(ge=1, le=9007199254740991)


@router.get("/ready", response_model=DesktopReady)
async def ready(response: Response) -> DesktopReady:
    nonce = os.environ.get("OPENFABRIC_DESKTOP_STARTUP_NONCE", "")
    if re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        raise HTTPException(status_code=404, detail="not_found")
    response.headers["Cache-Control"] = "no-store"
    return DesktopReady(nonce=nonce, pid=os.getpid())
