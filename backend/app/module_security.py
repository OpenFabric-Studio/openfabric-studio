"""Restrict local setup/document mutations to explicitly trusted browser origins."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


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


def require_local_origin(request: Request) -> None:
    """No DNS-rebinding hosts, null origins, wildcard ports or cross-site forms."""
    own = _loopback_origin(f'{request.url.scheme}://{request.headers.get("host", "")}')
    origin = request.headers.get('origin')
    site = request.headers.get('sec-fetch-site', '').lower()
    if own is None or site == 'cross-site':
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
