from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import db, regional_evaluation as service
from app.music_lora_contracts import MusicAdapter
from app.regional_dataset_contracts import RegionalDatasetError
from app.stable_lora_contracts import StableLoraTrainingJob, StableLoraTrainingRequest
from app.stable_lora_runtime import fingerprint


class RegionalTrainingReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_binds_actual_dataset_runtime_and_registered_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            request = StableLoraTrainingRequest(name='Fixture', samples=[{'regional_manifest': {'manifest_id': 'a' * 32,
                'manifest_sha256': 'b' * 64, 'excerpt_id': 'c' * 32}, 'caption': 'Reviewed phrase'}], max_steps=1)
            adapter = MusicAdapter(id='e' * 32, engine='stable_audio3', target='small-music', name='Fixture',
                sha256='f' * 64, created_at='2026-10-17T00:00:00Z', source_job_id='d' * 32)
            job = StableLoraTrainingJob(id='d' * 32, status='done', created_at='2026-10-17T00:00:00Z', request=request, adapter=adapter)
            folder = root / 'stable-lora-training' / job.id; folder.mkdir(parents=True)
            receipt = {'request_sha256': fingerprint(request), 'samples': [{'sample': '000.wav'}], 'runtime_identity': {
                'models': [{'path': 'models/mlx/base.npz', 'sha256': 'a' * 64, 'bytes': 100}], 'source_sha256': 'b' * 64,
                'source_file_count': 12, 'python_executable_sha256': 'c' * 64, 'python_version': '3.12.13',
                'platform': 'darwin', 'machine': 'arm64', 'packages': {'mlx': 'fixture', 'numpy': 'fixture'}}}
            data = json.dumps(receipt).encode(); (folder / 'dataset.json').write_bytes(data)
            completion = {'job_id': job.id, 'request_sha256': fingerprint(request), 'checkpoint': 'checkpoints/fixture.safetensors',
                'sha256': adapter.sha256, 'dataset_sha256': hashlib.sha256(data).hexdigest(), 'variant': 'small-music', 'max_steps': 1}
            (folder / 'completion.json').write_text(json.dumps(completion))
            with patch.object(db, 'DATA_DIR', root), patch('app.stable_lora.get', return_value=job):
                measured = await service.training_receipt(job.id)
                self.assertEqual(measured.identity_status, 'worker_measured')
                self.assertEqual(measured.runtime.models[0].file_name, 'base.npz')
                self.assertNotIn(str(root), measured.model_dump_json())
                completion['sha256'] = '0' * 64
                (folder / 'completion.json').write_text(json.dumps(completion))
                with self.assertRaises(RegionalDatasetError) as mismatch:
                    await service.training_receipt(job.id)
                self.assertEqual(mismatch.exception.code, 'regional_dataset_source_changed')
