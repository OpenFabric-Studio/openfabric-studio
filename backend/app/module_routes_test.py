from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import module_catalog as catalog
from app.module_jobs import ModuleJobService
from app.api.routes_modules import router


class ModuleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = catalog.ModuleEnvironment.for_root(Path(self.temporary.name), platform='darwin', architecture='arm64')
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:9000')
        self.enterContext(patch('app.api.routes_modules.service', return_value=ModuleJobService(self.environment)))
        self.enterContext(patch('app.api.routes_modules.configured_environment', return_value=self.environment))
        self.enterContext(patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))))

    def test_untrusted_origin_is_rejected_before_request_body_parsing(self) -> None:
        response = self.client.post('/api/modules/jobs', headers={'Origin': 'https://evil.example', 'Content-Type': 'application/json'}, content='not JSON')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'detail': 'setup_origin_forbidden'})

    def test_plan_rejects_arbitrary_targets_and_unknown_module_ids(self) -> None:
        for body in ({'features': ['shell']}, {'features': ['speech'], 'url': 'https://evil.example'}, {'features': ['speech'], 'destination': '/private'}):
            response = self.client.post('/api/modules/plan', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {'detail': 'invalid_setup_request'})

    def test_read_only_inventory_and_plan_do_not_create_setup_storage(self) -> None:
        response = self.client.get('/api/modules')
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/modules/plan', json={'features': ['ebooks']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
