from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_mashups import router
from app.mashup_contracts import MashupError
from app.mashup_plan_test import state_fixture


class MashupRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:9000')

    def test_saved_state_reads_do_not_start_work_and_plan_errors_preserve_conflicting_ids(self) -> None:
        state = state_fixture()
        with patch('app.api.routes_mashups.mashup_store.get', return_value=state), \
                patch('app.api.routes_mashups.mashups.create', new=AsyncMock()) as start:
            response = self.client.get('/api/mashups/' + state.id)
            self.assertEqual(response.status_code, 200)
            start.assert_not_awaited()
        with patch('app.api.routes_mashups.mashups.generate_plan', side_effect=MashupError('mashup_lock_conflict', ['locked'])):
            response = self.client.post('/api/mashups/' + state.id + '/plan', json={
                'expected_revision': 0, 'orientation': 'a_vocals_b_backing', 'target_bpm': 120,
                'meter': {'numerator': 4, 'denominator': 4}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'mashup_lock_conflict',
            'error': {'code': 'mashup_lock_conflict', 'conflicting_ids': ['locked']}})

    def test_invalid_traversal_body_and_foreign_origin_refuse_before_work(self) -> None:
        with patch('app.api.routes_mashups.mashups.create', new=AsyncMock()) as start:
            response = self.client.post('/api/mashups', json={'source_a': {'path': '../private.wav'}})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail'], 'mashup_invalid')
            response = self.client.post('/api/mashups', content='x' * (2 * 1024**2 + 1), headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            response = self.client.post('/api/mashups', json={}, headers={'origin': 'https://elsewhere.example'})
            self.assertEqual(response.status_code, 403)
            start.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
