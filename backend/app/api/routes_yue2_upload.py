"""Reimplements the one non-trivial thing YuE2's own web-ui/server.py did
beyond plain proxying: transcoding a non-WAV upload to WAV before forwarding
it to the native server's /v1/ui/upload (its WAV reader only accepts an
actual WAV container - PCM/float/A-law/mu-law - and rejects mp3/m4a/flac/etc).
We no longer run that server.py process at all, so this lives here instead.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..config import MODELS
from ..yue_upload import UploadConversionError, file_chunks, forward_upload
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus
from .routes_proxy import _HOP_BY_HOP, _client

router = APIRouter(prefix="/api/yue2")

@router.post("/v1/ui/upload")
async def upload_audio(request: Request) -> Response:
    rs = manager.state.models["yue2"]
    if rs.status != ModelStatus.RUNNING:
        return JSONResponse({"error": "model 'yue2' is not active"}, status_code=503)

    filename = request.headers.get("x-audiocpp-filename", "upload.bin")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    convert = ext != "wav"
    if convert:
        filename = "upload.wav"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() not in ("content-type", "x-audiocpp-filename")
    }
    headers["Content-Type"] = "audio/wav"
    headers["X-AudioCPP-Filename"] = filename

    async def send(path: Path) -> httpx.Response:
        # The native reader receives an accurate length, including after
        # conversion. Streaming without one switches HTTPX to chunked transfer,
        # which the legacy native upload contract does not guarantee it accepts.
        headers["Content-Length"] = str(path.stat().st_size)
        chunks = file_chunks(path)
        try:
            return await _client.post(f"{MODELS['yue2'].proxy_target}/v1/ui/upload", headers=headers, content=chunks)
        finally:
            await chunks.aclose()

    try:
        upstream = await forward_upload(request.stream(), convert=convert, send=send)
    except UploadConversionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    except httpx.HTTPError:
        logging.getLogger(__name__).warning("YuE upload upstream request failed", exc_info=True)
        return JSONResponse({"error": "upstream_request_failed"}, status_code=502)
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
