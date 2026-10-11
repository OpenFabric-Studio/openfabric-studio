"""Readiness belongs to the supervised listener, not whoever holds its port."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import ProcessSpec
from app.orchestrator.process import ManagedProcess

CPP_HEALTH = {'status': 'ok', 'backend': 'cpu', 'models': 0, 'ui': False, 'ui_management': False}
ACE_HEALTH = {'data': {'status': 'ok', 'service': 'ACE-Step API', 'version': '1.0'}, 'code': 200, 'error': None}


class NativeHealthTests(unittest.IsolatedAsyncioTestCase):
    async def foreign(self, status: int, body: bytes) -> None:
        async def response(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(4096)
            writer.write(f'HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(response, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process = ManagedProcess(ProcessSpec(name='foreign-health', cwd=root,
                    cmd=[sys.executable, '-c', 'import time; time.sleep(120)'],
                    health_url=f'http://127.0.0.1:{port}/health', startup_timeout=.3, shutdown_timeout=.2))
                with patch('app.orchestrator.process.LOG_DIR', root):
                    process.start()
                    try:
                        async with asyncio.timeout(2):
                            while True:
                                try:
                                    process.require_owned()
                                    break
                                except RuntimeError:
                                    await asyncio.sleep(.01)
                        with self.assertRaises((TimeoutError, RuntimeError, OSError)):
                            await process.wait_healthy()
                    finally:
                        await process.stop()
        finally:
            server.close()
            await server.wait_closed()

    async def test_foreign_404_cannot_mark_owned_sleeper_ready(self) -> None:
        await self.foreign(404, b'')

    async def test_foreign_valid_200_cannot_mark_owned_sleeper_ready(self) -> None:
        await self.foreign(200, json.dumps(CPP_HEALTH).encode())

    async def owned(self, body: bytes, status: int = 200, *, valid: bool) -> None:
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'server.py'
            script.write_text('from http.server import BaseHTTPRequestHandler, HTTPServer\n'
                'class Handler(BaseHTTPRequestHandler):\n'
                ' def do_GET(self):\n'
                f'  self.send_response({status}); self.send_header("Content-Length", "{len(body)}"); self.end_headers(); self.wfile.write({body!r})\n'
                f'HTTPServer(("127.0.0.1", {port}), Handler).serve_forever()\n')
            process = ManagedProcess(ProcessSpec(name='owned-health', cwd=root, cmd=[sys.executable, str(script)],
                health_url=f'http://127.0.0.1:{port}/health', startup_timeout=2, shutdown_timeout=.2))
            with patch('app.orchestrator.process.LOG_DIR', root):
                process.start()
                try:
                    if valid:
                        await process.wait_healthy()
                        process.require_owned()
                    else:
                        with self.assertRaises((TimeoutError, RuntimeError, OSError)):
                            await process.wait_healthy()
                finally:
                    await process.stop()

    async def test_actual_owned_cpp_listener_is_ready(self) -> None:
        await self.owned(json.dumps(CPP_HEALTH).encode(), valid=True)

    async def test_actual_owned_ace_listener_is_ready(self) -> None:
        await self.owned(json.dumps(ACE_HEALTH).encode(), valid=True)

    async def test_owned_404_is_not_ready(self) -> None:
        await self.owned(json.dumps(CPP_HEALTH).encode(), status=404, valid=False)

    async def test_owned_malformed_health_is_not_ready(self) -> None:
        await self.owned(b'{"status":"broken"}', valid=False)

    async def test_listener_handoff_after_valid_response_is_rejected(self) -> None:
        from app.orchestrator.process import listener_pids
        checked = 0
        async def identities(port: int) -> frozenset[int]:
            nonlocal checked
            pids = await listener_pids(port)
            if pids:
                checked += 1
                if checked == 2:
                    return frozenset({os.getpid()})
            return pids
        with patch('app.orchestrator.process.listener_pids', side_effect=identities):
            await self.owned(json.dumps(CPP_HEALTH).encode(), valid=False)
        self.assertEqual(checked, 2, 'Readiness needs ownership proof after the response')

    async def test_unavailable_listener_ownership_fails_closed(self) -> None:
        with patch('app.orchestrator.process.listener_pids', new=AsyncMock(side_effect=OSError('No ownership evidence'))):
            await self.owned(json.dumps(CPP_HEALTH).encode(), valid=False)
