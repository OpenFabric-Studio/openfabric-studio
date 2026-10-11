"""Local setup only. The public relay is a separate app with no API routes."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message
from .. import clip_host_settings as settings, clip_hosting as hosting
from ..clip_host_contracts import ClipHostSettingsRequest, ClipHostStatus
from ..module_security import require_local_origin


class ClipHostRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request],Coroutine[object,object,Response]]:
        handler=super().get_route_handler()
        async def guarded(request: Request) -> Response:
            if request.method not in {'GET','HEAD','OPTIONS'}: require_local_origin(request)
            count=0
            async def receive() -> Message:
                nonlocal count
                message=await request.receive()
                if message['type']=='http.request':
                    body: object=message.get('body',b'')
                    if isinstance(body,bytes): count+=len(body)
                    if count>4096: raise HTTPException(413,'clip_host_request_too_large')
                return message
            try: return await handler(Request(request.scope,receive))
            except RequestValidationError as error: raise HTTPException(422,'clip_host_request_invalid') from error
            except hosting.ClipHostError as error: raise HTTPException(error.status,error.code) from error
            except (OSError,ValueError) as error: raise HTTPException(503,'clip_host_storage_unavailable') from error
        return guarded


router=APIRouter(prefix='/api/clip-host',tags=['clip-host'],route_class=ClipHostRoute)


@router.get('/settings',response_model=ClipHostStatus)
def status() -> ClipHostStatus: return hosting.status()


@router.put('/settings',response_model=ClipHostStatus)
def save(body: ClipHostSettingsRequest) -> ClipHostStatus: return settings.save(body)


@router.post('/connection',response_model=ClipHostStatus)
async def connection() -> ClipHostStatus: return await hosting.connection()


@router.post('/cleanup',response_model=ClipHostStatus)
async def cleanup() -> ClipHostStatus: return await hosting.cleanup()
