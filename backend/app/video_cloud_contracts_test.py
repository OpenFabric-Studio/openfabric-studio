"""Cloud provider selection is separate from retained local engine settings."""
from __future__ import annotations
import unittest
from pydantic import ValidationError
from app.video_contracts import VideoProject, UpdateVideoProjectRequest


class CloudVideoContractTests(unittest.TestCase):
    def project(self) -> VideoProject:
        return VideoProject(id='a'*32,revision=1,name='Existing local',track_title='',duration_sec=12,source_fingerprint='',created_at='',updated_at='')

    def test_old_local_projects_load_without_a_cloud_configuration(self) -> None:
        project=self.project()
        self.assertEqual(project.provider_config.provider,'local')
        self.assertEqual(project.settings.engine_pack,'ltx23')

    def test_remote_parameters_do_not_masquerade_as_an_ltx_model_pack(self) -> None:
        body=UpdateVideoProjectRequest.model_validate({'revision':1,'provider_config':{'provider':'openrouter','model_id':'google/veo-3.1-fast','size':'1280x720'}})
        self.assertEqual(body.provider_config.provider,'openrouter')
        self.assertIsNone(body.settings)
        with self.assertRaises(ValidationError):
            UpdateVideoProjectRequest.model_validate({'revision':1,'provider_config':{'provider':'openrouter','model_id':'../secret','size':'1280x720'}})
        with self.assertRaises(ValidationError):
            UpdateVideoProjectRequest.model_validate({'revision':1,'provider_config':{'provider':'openrouter','model_id':'google/veo-3.1-fast','size':'99999x99999','engine_pack':'ltx23'}})
