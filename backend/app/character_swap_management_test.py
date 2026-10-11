"""Saved replacement controls use isolated libraries and no provider calls."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import character_swaps as swaps, openrouter_requests as ledger
from app.character_swap_contracts import CharacterSwapJob, CharacterSwapStatus


class CharacterSwapManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.main import app
        self.library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(swaps, 'DATA_DIR', self.library))
        self.enterContext(patch.object(ledger, 'REQUESTS_ROOT', self.library / 'receipts'))
        self.enterContext(patch.object(swaps, '_closing', False))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.identifier = 'a' * 32
        self.url = '/api/videos/character-swaps/' + self.identifier
        self.job = self.fixture()

    def fixture(self, *, status: CharacterSwapStatus = 'prepared', legacy: bool = False) -> CharacterSwapJob:
        path = swaps.directory(self.identifier)
        path.mkdir(parents=True, exist_ok=True)
        job = CharacterSwapJob(id=self.identifier, name='Replacement', status=status,
            created_at=swaps.now(), updated_at=swaps.now(), start_sec=0, seconds=2,
            source_name='original.mp4', image_name='character.png', source_duration_sec=2,
            width=96, height=64, frames=48, has_audio=True, seed=42,
            source_url=self.url + '/source', clip_url=self.url + '/clip', image_url=self.url + '/image',
            provider='fal' if legacy else 'openrouter',
            model_id='fal-ai/wan/v2.2-14b/animate/replace' if legacy else 'bytedance-seed/seedream-4.5')
        swaps.save(swaps.StoredSwap(job=job, source_sha256='b' * 64, image_sha256='c' * 64,
            selected_sha256='d' * 64, input_sha256='e' * 64))
        (path / 'source.video').write_bytes(b'owned source')
        (path / 'selected.mp4').write_bytes(b'owned selection')
        return job

    async def test_rename_trims_title_updates_revision_and_preserves_media(self) -> None:
        response = await self.client.patch(self.url, json={'revision': 1, 'name': '  New name  '})
        self.assertEqual(response.status_code, 200)
        renamed = CharacterSwapJob.model_validate(response.json())
        self.assertEqual((renamed.name, renamed.revision, renamed.status), ('New name', 2, 'prepared'))
        self.assertEqual(swaps.get(self.identifier).name, 'New name')
        self.assertEqual(swaps.artifact(self.identifier, 'source.video').read_bytes(), b'owned source')

    async def test_stale_rename_and_delete_leave_job_intact(self) -> None:
        for method, body in [('PATCH', {'revision': 2, 'name': 'Stale'}), ('DELETE', {'revision': 2})]:
            response = await self.client.request(method, self.url, json=body)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'detail': 'revision_conflict'})
        self.assertEqual(swaps.get(self.identifier).name, 'Replacement')

    async def test_invalid_rename_and_noninteger_revision_are_rejected(self) -> None:
        for body in ({'revision': 1, 'name': '   '}, {'revision': True, 'name': 'Name'},
                     {'revision': 1, 'name': 'x' * 121}, {'revision': 1, 'name': 42}):
            response = await self.client.patch(self.url, json=body)
            self.assertEqual(response.status_code, 422)
        response = await self.client.request('DELETE', self.url, json={'revision': '1'})
        self.assertEqual(response.status_code, 422)

    async def test_active_states_cannot_be_renamed_or_deleted(self) -> None:
        for status in ('submitting', 'queued', 'running'):
            job = self.fixture(status=CharacterSwapJob.model_validate({**self.job.model_dump(), 'status': status}).status)
            for method, body in [('PATCH', {'revision': job.revision, 'name': 'Active'}), ('DELETE', {'revision': job.revision})]:
                response = await self.client.request(method, self.url, json=body)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail': 'busy'})
            self.assertTrue(swaps.directory(self.identifier).is_dir())

    async def test_registered_owner_blocks_even_inactive_job(self) -> None:
        task = asyncio.create_task(asyncio.sleep(30))
        try:
            with patch.dict(swaps._tasks, {self.identifier: task}):
                response = await self.client.request('DELETE', self.url, json={'revision': 1})
                self.assertEqual(response.status_code, 409)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(swaps.directory(self.identifier).is_dir())

    async def test_unresolved_worker_evidence_is_retained(self) -> None:
        ownership = swaps.artifact(self.identifier, 'worker.json')
        ownership.write_text('unverified worker')
        for method, body in [('PATCH', {'revision': 1, 'name': 'Unsafe'}), ('DELETE', {'revision': 1})]:
            response = await self.client.request(method, self.url, json=body)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'detail': 'worker_unresolved'})
        self.assertEqual(ownership.read_text(), 'unverified worker')

    async def test_unknown_job_can_be_deleted_without_touching_billing_receipts(self) -> None:
        self.fixture(status='submission_unknown')
        ledger.REQUESTS_ROOT.mkdir()
        receipt = ledger.REQUESTS_ROOT / ('b' * 32 + '.json')
        receipt.write_bytes(b'paid evidence remains')
        with patch.object(swaps, 'OpenRouterClient') as provider:
            response = await self.client.request('DELETE', self.url, json={'revision': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'deleted': self.identifier})
        self.assertEqual(receipt.read_bytes(), b'paid evidence remains')
        provider.assert_not_called()
        self.assertEqual(swaps.list_jobs().jobs, [])
        self.assertEqual((await self.client.get(self.url + '/source')).status_code, 404)

    async def test_legacy_jobs_allow_local_management_only(self) -> None:
        self.fixture(legacy=True)
        response = await self.client.patch(self.url, json={'revision': 1, 'name': 'Legacy name'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['provider'], 'fal')
        response = await self.client.request('DELETE', self.url, json={'revision': 2})
        self.assertEqual(response.status_code, 200)

    async def test_cross_origin_mutations_are_rejected(self) -> None:
        for method, body in [('PATCH', {'revision': 1, 'name': 'Outside'}), ('DELETE', {'revision': 1})]:
            response = await self.client.request(method, self.url, headers={'Origin': 'https://outside.example'}, json=body)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(swaps.get(self.identifier).revision, 1)

    async def test_atomic_hide_failure_preserves_original_job(self) -> None:
        with patch.object(Path, 'rename', side_effect=OSError('synthetic disk failure')):
            response = await self.client.request('DELETE', self.url, json={'revision': 1})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(swaps.get(self.identifier).name, 'Replacement')
        self.assertTrue(swaps.artifact(self.identifier, 'source.video').is_file())

    async def test_interrupted_cleanup_remains_hidden_and_recovers_on_startup(self) -> None:
        with patch.object(swaps, '_cleanup_deleted', side_effect=OSError('synthetic unlink failure'), create=True):
            response = await self.client.request('DELETE', self.url, json={'revision': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(swaps.list_jobs().jobs, [])
        tombstone = swaps.root() / ('.' + self.identifier + '.deleting')
        self.assertTrue(tombstone.is_dir())
        await swaps.recover()
        self.assertFalse(tombstone.exists())

    async def test_escaped_job_directory_is_rejected_without_touching_target(self) -> None:
        original = swaps.directory(self.identifier)
        target = self.library / 'external'
        original.rename(target)
        original.symlink_to(target, target_is_directory=True)
        response = await self.client.request('DELETE', self.url, json={'revision': 1})
        self.assertEqual(response.status_code, 503)
        self.assertEqual((target / 'source.video').read_bytes(), b'owned source')

    async def test_cleanup_unlinks_media_symlinks_without_following_them(self) -> None:
        target = self.library / 'external-secret'
        target.write_bytes(b'keep')
        swaps.artifact(self.identifier, 'input.mp4').symlink_to(target)
        response = await self.client.request('DELETE', self.url, json={'revision': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(target.read_bytes(), b'keep')

    async def test_unmarked_nonempty_tombstone_is_never_erased(self) -> None:
        tombstone = swaps.root() / ('.' + 'f' * 32 + '.deleting')
        tombstone.mkdir()
        evidence = tombstone / 'unknown'
        evidence.write_bytes(b'keep unknown evidence')
        await swaps.recover()
        self.assertEqual(evidence.read_bytes(), b'keep unknown evidence')
