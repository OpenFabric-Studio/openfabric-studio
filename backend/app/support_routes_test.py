"""Support endpoints never accept arbitrary processes or expose debug dumps."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_support
from app.support_contracts import EngineRuntimeResponse
from app.support_controls import EngineControlError


class SupportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(routes_support.router)
        self.client = TestClient(app, base_url='http://127.0.0.1:9000')
        self.stop = self.enterContext(patch.object(routes_support, 'stop_engine', new=AsyncMock()))
        self.enterContext(patch.object(routes_support, 'engine_status', new=AsyncMock(return_value=EngineRuntimeResponse(engines=[]))))

    def test_stop_rejects_external_gpt_process_and_arbitrary_pid(self) -> None:
        for body in ({'engine_id': 'speech', 'instance_id': 'a' * 32},
                     {'engine_id': 'ace_step', 'instance_id': 'a' * 32, 'pid': 42},
                     {'engine_id': 'ace_step', 'instance_id': '../private'}):
            response = self.client.post('/api/support/engines/stop', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {'detail': 'invalid_support_request'})
        self.stop.assert_not_awaited()

    def test_cross_site_stop_is_rejected_before_body_parsing(self) -> None:
        response = self.client.post('/api/support/engines/stop', headers={'Origin': 'https://evil.example', 'Content-Type': 'application/json'}, content='private invalid body')
        self.assertEqual(response.status_code, 403)
        self.stop.assert_not_awaited()

    def test_stop_returns_only_stable_busy_code(self) -> None:
        self.stop.side_effect = EngineControlError('engine_busy')
        response = self.client.post('/api/support/engines/stop', json={'engine_id': 'ace_step', 'instance_id': 'a' * 32})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'engine_busy'})

    def test_unexpected_report_failure_is_not_exposed_to_the_browser(self) -> None:
        with patch.object(routes_support, 'configured_environment', side_effect=RuntimeError('/Users/person/private key')), self.assertLogs('app.api.routes_support', level='ERROR'):
            response = self.client.get('/api/support/report')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'support_unavailable'})
