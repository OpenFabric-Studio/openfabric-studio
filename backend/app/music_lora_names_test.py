"""Names identify a model's LoRA family, not an arbitrary training attempt."""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import db, music_lora, music_training, stable_lora
from app.music_lora_contracts import MusicLoraError
from app.music_lora_test import adapter_bytes
from app.music_training_contracts import MusicLoraTrainingJob, MusicLoraTrainingRequest
from app.stable_lora_contracts import StableLoraTrainingJob


class MusicLoraNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for item in (patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', self.root),
                     patch.object(db, 'DB_PATH', self.root / 'catalog.db')):
            item.start(); self.addCleanup(item.stop)
        self.connection = db.get_db()
        self.addCleanup(self.connection.close)
        stable_lora.migrate(self.connection); music_training.migrate(self.connection)

    def stable_job(self, *, name: str = 'My style', status: str = 'done') -> None:
        job = StableLoraTrainingJob.model_validate({'id': 'a' * 32, 'created_at': '2026-10-16',
            'status': status, 'request': {'name': name, 'samples': [{'track_id': 1, 'caption': 'piano'}]}})
        self.connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (job.id, job.model_dump_json(), job.created_at)); self.connection.commit()

    def music_job(self, identifier: str, *, source: str | None = None) -> None:
        request = MusicLoraTrainingRequest(engine='yue2', name='Family', samples=[{'track_id': 1, 'caption': 'piano'}],
            yue_ar_source_job_id=source)
        job = MusicLoraTrainingJob(id=identifier, status='done', created_at='2026-10-16', request=request)
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (job.id, job.model_dump_json(), job.created_at)); self.connection.commit()

    def test_saved_training_names_block_new_imports_even_after_failure(self) -> None:
        for status in ('queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'):
            self.connection.execute('DELETE FROM stable_lora_jobs'); self.connection.commit()
            self.stable_job(status=status)
            with self.subTest(status=status), self.assertRaises(MusicLoraError) as caught:
                music_lora.import_stream(io.BytesIO(adapter_bytes()), filename='adapter.safetensors',
                    engine='stable_audio3', target='small-music', name=' my  STYLE ')
            self.assertEqual(caught.exception.code, 'music_lora_name_exists')
        self.assertEqual(music_lora.list_adapters().adapters, [])

    def test_family_exception_is_limited_to_the_linked_yue_family(self) -> None:
        from app.music_lora_names import require_available
        parent = 'b' * 32
        self.music_job(parent); self.music_job('c' * 32, source=parent)
        require_available('yue2', 'family', family_job_id=parent)
        with self.assertRaises(MusicLoraError):
            require_available('yue2', 'family')
        self.music_job('d' * 32)
        with self.assertRaises(MusicLoraError):
            require_available('yue2', 'family', family_job_id=parent)
        require_available('stable_audio3', 'family')

    def test_retired_training_records_do_not_block_supported_model_names(self) -> None:
        from app.music_lora_names import require_available
        self.music_job('b' * 32)
        encoded = self.connection.execute('SELECT payload_json FROM music_training_jobs').fetchone()[0]
        retired = encoded.replace('yue2', 'heartmula')
        self.connection.execute('UPDATE music_training_jobs SET payload_json=?', (retired,))
        self.connection.commit()
        require_available('yue2', 'Family')
        self.assertEqual(self.connection.execute('SELECT payload_json FROM music_training_jobs').fetchone()[0], retired)

    def test_incomplete_registry_and_invalid_history_fail_closed(self) -> None:
        from app.music_lora_names import require_available
        self.connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            ('a' * 32, '{broken', '2026-10-16')); self.connection.commit()
        with self.assertRaises(MusicLoraError) as caught:
            require_available('stable_audio3', 'New name')
        self.assertEqual(caught.exception.code, 'music_lora_name_unavailable')
        self.connection.execute('DELETE FROM stable_lora_jobs'); self.connection.commit()
        directory = self.root / 'music-lora' / ('b' * 32); directory.mkdir(parents=True)
        (directory / 'manifest.json').write_text('{broken')
        with self.assertRaises(MusicLoraError) as caught:
            require_available('stable_audio3', 'New name')
        self.assertEqual(caught.exception.code, 'music_lora_name_unavailable')

    def test_large_valid_history_does_not_block_unrelated_names(self) -> None:
        from app.music_lora_names import require_available
        request = MusicLoraTrainingRequest(engine='yue2', name='Large dataset', yue_pair={'planner_steps': 1}, samples=[
            {'track_id': index + 1, 'caption': 'x' * 4000, 'lyrics': 'y' * 20000} for index in range(100)])
        job = MusicLoraTrainingJob(id='a' * 32, status='done', created_at='2026-10-16', request=request)
        encoded = job.model_dump_json()
        self.assertGreater(len(encoded), 2 * 1024**2)
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (job.id, encoded, job.created_at)); self.connection.commit()
        require_available('yue2', 'Another name')
        with self.assertRaises(MusicLoraError) as caught:
            require_available('yue2', 'large dataset')
        self.assertEqual(caught.exception.code, 'music_lora_name_exists')

    def test_name_transaction_excludes_another_process_and_rejects_a_symlink(self) -> None:
        from app.music_lora_names import name_transaction
        code = ('from app.music_lora_names import name_transaction;'
            '\nwith name_transaction(): print("entered",flush=True)')
        environment = {**os.environ, 'OPENFABRIC_CONFIG': str(self.root / 'config.json'),
            'OPENFABRIC_DATA_DIR': str(self.root), 'OPENFABRIC_MODULE_ROOT': str(self.root / 'modules'),
            'SEED_VC_DIR': str(self.root / 'seed-vc')}
        with name_transaction():
            child = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('music_lora_name_unavailable', child.stderr)
            self.assertNotIn('entered', child.stdout)
        child = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        lease = self.root / 'music-lora/names.lock'
        lease.unlink(); outside = self.root / 'outside'; outside.write_bytes(b'preserved'); lease.symlink_to(outside)
        with self.assertRaises(MusicLoraError):
            with name_transaction():
                self.fail('unsafe lease acquired')
        self.assertEqual(outside.read_bytes(), b'preserved')
