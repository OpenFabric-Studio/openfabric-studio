"""Shared typed translation for exact-source download endpoints."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import HTTPException, Query, Request

from ..job_lifecycle import await_cleanup
from ..tagging import TaggedAudioDownload, TaggedDownloadOptions, TaggedFileResponse, TaggingError, prepare_download


def download_options(album: Annotated[str | None, Query(max_length=120, pattern=r"^[^\x00-\x1f\x7f]*$")] = None,
                     track_no: Annotated[int | None, Query(ge=1, le=999)] = None) -> TaggedDownloadOptions:
    # Query strings are parsed by FastAPI before the strict domain contract.
    return TaggedDownloadOptions(album=album, track_no=track_no)


async def _disconnected(request: Request, stop: asyncio.Event) -> None:
    while not stop.is_set():
        if await request.is_disconnected() or stop.is_set():
            return
        await asyncio.sleep(0.1)


async def _connected_download(track_id: int, version_id: str | None, export_id: str | None,
                              options: TaggedDownloadOptions, request: Request) -> TaggedAudioDownload:
    preparation = asyncio.create_task(prepare_download(track_id, version_id, export_id, options=options))
    stop = asyncio.Event()
    disconnected = asyncio.create_task(_disconnected(request, stop))
    try:
        try:
            finished, _ = await asyncio.wait({preparation, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in finished:
                raise TaggingError("download_cancelled")
            result = preparation.result()
        finally:
            # Request.is_disconnected() uses an AnyIO cancel scope which can
            # absorb a racing Task.cancel(). The explicit signal still lets
            # the watcher settle before owned preparation cleanup completes.
            stop.set()
            disconnected.cancel()
            if not preparation.done():
                preparation.cancel()
            await await_cleanup(asyncio.gather(preparation, disconnected, return_exceptions=True))
        return result
    except BaseException:
        if preparation.done() and not preparation.cancelled() and preparation.exception() is None:
            preparation.result().cleanup()
        raise


async def tagged_download_response(track_id: int, version_id: str | None,
                                   export_id: str | None, options: TaggedDownloadOptions,
                                   request: Request) -> TaggedFileResponse:
    try:
        return TaggedFileResponse(await _connected_download(track_id, version_id, export_id, options, request))
    except TaggingError as exc:
        status = 409
        if exc.code == "download_cancelled":
            status = 499
        elif exc.code in {"track_not_found", "audio_version_not_found", "export_not_found"}:
            status = 404
        elif exc.code in {"ffmpeg_missing", "tagging_failed", "tagging_timeout", "tagging_output_invalid",
                          "artist_settings_invalid", "artist_settings_write_failed"}:
            status = 503
        raise HTTPException(status, detail=exc.code) from exc
