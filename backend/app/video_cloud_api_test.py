"""Cloud routes reject cross-site mutations before quote/payment/tracking logic."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes_videos import router
from app import video_projects as store
from app.video_contracts import VideoProject, OpenRouterVideoProviderConfig


class CloudApiTests(unittest.TestCase):
    def test_cross_site_video_requests_never_reach_cloud_worker(self) -> None:
        app=FastAPI();app.include_router(router)
        with tempfile.TemporaryDirectory() as root, patch.object(store,'DATA_DIR',Path(root)), TestClient(app,base_url='http://127.0.0.1:8000') as client:
            identifier='a'*32
            paths={'quote':{'revision':1,'shot_id':'b'*32,'remote_duration_sec':4},
                'submit':{'revision':1,'shot_id':'b'*32,'remote_duration_sec':4,'quote_id':'c'*32,'transfers_confirmed':True},
                'resume':{'revision':1,'variant_id':'d'*32}}
            with patch('app.video_cloud.quote') as quote, patch('app.video_cloud.submit',new=AsyncMock()) as submit, patch('app.video_cloud.resume',new=AsyncMock()) as resume:
                for action,body in paths.items():
                    response=client.post(f'/api/videos/projects/{identifier}/cloud/{action}',json=body,headers={'origin':'https://untrusted.test','sec-fetch-site':'cross-site'})
                    self.assertEqual(response.status_code,403,response.text)
                quote.assert_not_called();submit.assert_not_called();resume.assert_not_called()

    def test_cloud_stop_tracking_requires_local_origin_before_cancellation(self) -> None:
        app=FastAPI();app.include_router(router)
        project=VideoProject(id='a'*32,name='Cloud',revision=1,track_title='',source_fingerprint='',track_id=None,duration_sec=4,created_at='',updated_at='',provider_config=OpenRouterVideoProviderConfig(model_id='google/veo-3.1-fast',size='1280x720'))
        with patch.object(store,'get',return_value=project), patch('app.video_render.cancel',new=AsyncMock()) as cancel, TestClient(app,base_url='http://127.0.0.1:8000') as client:
            result=client.post(f'/api/videos/projects/{project.id}/cancel',headers={'origin':'https://untrusted.test'})
            self.assertEqual(result.status_code,403,result.text);cancel.assert_not_called()
