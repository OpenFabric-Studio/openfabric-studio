"""Restrict local setup/document mutations to explicitly trusted browser origins."""
from __future__ import annotations

import os
import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def _loopback_origin(value: str) -> str | None:
    try:
        url = urlsplit(value)
        if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1') or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            return None
        # Access validates malformed/non-numeric ports.
        port = url.port or 80
        host = '[' + url.hostname + ']' if ':' in url.hostname else url.hostname
        return f'http://{host}:{port}'
    except ValueError:
        return None


def require_local_host(request: Request) -> str:
    """A redirected DNS name must never become the local app's trusted host."""
    if len(request.headers.getlist('host')) != 1:
        raise HTTPException(403, detail='setup_origin_forbidden')
    own = _loopback_origin(f'{request.url.scheme}://{request.headers.get("host", "")}')
    if own is None:
        raise HTTPException(403, detail='setup_origin_forbidden')
    return own


def require_local_origin(request: Request) -> None:
    """No DNS-rebinding hosts, null origins, wildcard ports or cross-site forms."""
    own = require_local_host(request)
    origin = request.headers.get('origin')
    site = request.headers.get('sec-fetch-site', '').lower()
    if len(request.headers.getlist('origin')) > 1 or site == 'cross-site':
        raise HTTPException(403, detail='setup_origin_forbidden')
    if origin is None:
        if site or request.headers.get('sec-fetch-mode'):
            raise HTTPException(403, detail='setup_origin_forbidden')
        return
    normalized = _loopback_origin(origin)
    allowed = {own}
    for configured in os.environ.get('OPENFABRIC_SETUP_ALLOWED_ORIGINS', '').split(','):
        candidate = _loopback_origin(configured.strip())
        if candidate is not None:
            allowed.add(candidate)
    if normalized is None or normalized not in allowed:
        raise HTTPException(403, detail='setup_origin_forbidden')


class LocalAccessMiddleware:
    """Check every route before uploads are read, without creating child tasks."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http':
            request = Request(scope)
            try:
                peer = request.client
                if peer is None or not ipaddress.ip_address(peer.host).is_loopback:
                    raise HTTPException(403, detail='setup_origin_forbidden')
                require_local_host(request)
                if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                    require_local_origin(request)
            except (HTTPException, ValueError):
                response = JSONResponse({'detail': 'setup_origin_forbidden'}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
