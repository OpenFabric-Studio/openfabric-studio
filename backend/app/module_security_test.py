from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.module_security import require_local_origin


class ModuleOriginTests(unittest.TestCase):
    def request(self, origin: str | None, *, host: str = '127.0.0.1:9000', site: str | None = None) -> Request:
        headers = [(b'host', host.encode())]
        if origin is not None:
            headers.append((b'origin', origin.encode()))
        if site is not None:
            headers.append((b'sec-fetch-site', site.encode()))
        return Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/api/modules/jobs', 'headers': headers, 'server': ('127.0.0.1', 9000)})

    def test_same_origin_and_non_browser_cli_are_allowed(self) -> None:
        require_local_origin(self.request('http://127.0.0.1:9000'))
        require_local_origin(self.request(None))

    def test_cross_origin_null_foreign_host_and_cross_site_are_rejected(self) -> None:
        for request in (self.request('https://evil.example'), self.request('null'), self.request('http://localhost:5173'),
                        self.request(None, host='evil.example:9000'), self.request(None, site='cross-site')):
            with self.subTest(request=request), self.assertRaises(HTTPException) as error:
                require_local_origin(request)
            self.assertEqual(error.exception.status_code, 403)

    def test_explicit_development_origins_must_be_loopback_http(self) -> None:
        with patch.dict('os.environ', {'OPENFABRIC_SETUP_ALLOWED_ORIGINS': 'http://localhost:5173,https://evil.example'}):
            require_local_origin(self.request('http://localhost:5173'))
            with self.assertRaises(HTTPException):
                require_local_origin(self.request('https://evil.example'))


if __name__ == '__main__':
    unittest.main()
