"""Write-only provider credentials and safe free setup/estimate endpoints."""
from __future__ import annotations
from collections.abc import Callable, Coroutine
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message
from ..module_security import require_local_origin as require_openrouter_origin
from .. import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings
from ..openrouter_client import OpenRouterClient
from ..openrouter_errors import OpenRouterError
from ..openrouter_contracts import OpenRouterStatus, OpenRouterSettingsRequest, OpenRouterKeyRequest, OpenRouterConnection, OpenRouterCatalog, OpenRouterQuote, OpenRouterQuoteRequest, OpenRouterReceipt, OpenRouterReceipts

class ProviderRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object,object,Response]]:
        handler=super().get_route_handler()
        async def bounded(request: Request) -> Response:
            if request.method not in {'GET','HEAD','OPTIONS'}: require_openrouter_origin(request)
            count=0
            async def receive() -> Message:
                nonlocal count
                message=await request.receive()
                if message['type']=='http.request':
                    body: object=message.get('body',b'')
                    if isinstance(body,bytes): count+=len(body)
                    if count>65536: raise HTTPException(413,'provider_request_too_large')
                return message
            try:
                return await handler(Request(request.scope,receive))
            except RequestValidationError as error:
                # FastAPI's standard validation payload includes the rejected
                # input. It must never echo a key, transcript or reference data.
                raise HTTPException(422,'provider_request_invalid') from error
            except OpenRouterError as error: raise HTTPException(error.status,error.code) from error
            except (OSError,ValueError) as error: raise HTTPException(503,'provider_storage_unavailable') from error
        return bounded

router=APIRouter(prefix='/api/openrouter',tags=['openrouter'],route_class=ProviderRoute)
client=OpenRouterClient()

@router.get('/settings',response_model=OpenRouterStatus)
def provider_status() -> OpenRouterStatus: return settings.status()

@router.put('/settings',response_model=OpenRouterStatus)
def provider_settings(body: OpenRouterSettingsRequest) -> OpenRouterStatus: return settings.save(body)

@router.post('/credential',response_model=OpenRouterStatus)
def provider_credential(body: OpenRouterKeyRequest) -> OpenRouterStatus: return settings.set_credential(body)

@router.delete('/credential',response_model=OpenRouterStatus)
def forget_credential() -> OpenRouterStatus: return settings.delete_credential()

@router.post('/connection',response_model=OpenRouterConnection)
async def connection() -> OpenRouterConnection: return await client.connection()

@router.get('/catalog',response_model=OpenRouterCatalog)
def model_catalog() -> OpenRouterCatalog: return catalog.load()

@router.post('/catalog/refresh',response_model=OpenRouterCatalog)
async def refresh_catalog() -> OpenRouterCatalog: return await client.refresh_catalog()

@router.post('/quote',response_model=OpenRouterQuote)
def cost_quote(body: OpenRouterQuoteRequest) -> OpenRouterQuote: return catalog.quote(body)

@router.get('/requests',response_model=OpenRouterReceipts)
def provider_requests() -> OpenRouterReceipts: return OpenRouterReceipts(requests=ledger.list_receipts(),history_incomplete=ledger.history_unavailable())

@router.get('/requests/{identifier}',response_model=OpenRouterReceipt)
def provider_request(identifier: str) -> OpenRouterReceipt: return ledger.get(identifier)
