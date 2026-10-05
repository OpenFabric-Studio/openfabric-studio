"""Browser GETs must load history while cloud mutations reject foreign origins."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes_cloud_music import router
from app.cloud_music_contracts import CloudMusicJobs

class CloudMusicApiTests(unittest.TestCase):
    def test_same_origin_browser_get_does_not_require_an_origin_header(self) -> None:
        app=FastAPI();app.include_router(router)
        with patch('app.cloud_music.list_jobs',return_value=CloudMusicJobs(jobs=[])),TestClient(app,base_url='http://localhost') as client:
            response=client.get('/api/cloud-music/jobs',headers={'sec-fetch-site':'same-origin','sec-fetch-mode':'cors'})
        self.assertEqual(response.status_code,200,response.text)
    def test_cross_site_paid_mutation_is_rejected_before_processing(self) -> None:
        app=FastAPI();app.include_router(router)
        with TestClient(app,base_url='http://localhost') as client:
            response=client.post('/api/cloud-music/quote',json={'model':'google/lyria-3-clip-preview','prompt':'hello'},headers={'origin':'https://foreign.example','sec-fetch-site':'cross-site'})
        self.assertEqual(response.status_code,403)
