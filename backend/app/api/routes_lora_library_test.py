"""Native status failures fail closed; preprocessing reuse requires a receipt."""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
import httpx

from app import lora_datasets as library
from app.atomic_files import read_object, write_object
from app.lora_dataset_contracts import CreateLoraDatasetRequest, LoraDatasetRevision, RemoveLoraDatasetRecordsResponse
from app.orchestrator.state import ModelStatus
from app.api import routes_lora_library as routes


class DatasetLibraryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine = self.root / 'engine'
        self.data = self.root / 'data'
        self.engine.mkdir()
        self.data.mkdir()
        self.enterContext(patch.object(library, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(library, 'DATA_DIR', self.data))
        self.enterContext(patch.object(library.manager.state.models['ace_step'], 'status', ModelStatus.RUNNING))
        app = FastAPI()
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.addAsyncCleanup(self.client.aclose)

    def entry(self) -> library.LoraDatasetEntry:
        entry = library.create_dataset(CreateLoraDatasetRequest(name='Saved work'))
        audio = library.confined_path(entry.audio_dir) / 'clip.wav'
        audio.write_bytes(b'audio')
        write_object(library.confined_path(entry.dataset_path), {'metadata': {'name': entry.name, 'custom_tag': 'voice'},
            'samples': [{'id': 'keep-id', 'audio_path': str(audio), 'filename': audio.name, 'caption': 'keep caption', 'labeled': True}]})
        return library.get_dataset(entry.id)

    async def native(self, method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
        if endpoint in ('v1/dataset/auto_label_status', 'v1/dataset/preprocess_status'):
            return {'status': 'idle'}
        if endpoint == 'v1/training/status':
            return {'is_training': False}
        if endpoint == 'health':
            return {'models_initialized': True, 'loaded_model': 'test-base'}
        if endpoint == 'v1/dataset/preprocess_async':
            return {'task_id': 'pre-1', 'message': 'started', 'total': 1}
        return {}

    async def test_remove_records_preserves_dataset_files_and_does_not_rediscover_them(self) -> None:
        entry = self.entry()
        entry = library.trash_dataset(entry.id, LoraDatasetRevision(revision=entry.revision))
        dataset = library.confined_path(entry.dataset_path)
        original = dataset.read_bytes()
        with patch.object(library, '_native', side_effect=self.native):
            response = await self.client.post('/api/lora-datasets/remove-records', json={'records': [{'id': entry.id, 'revision': entry.revision}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'removed_ids': [entry.id]})
        self.assertEqual(dataset.read_bytes(), original)
        self.assertEqual((dataset.parent / 'clip.wav').read_bytes(), b'audio')
        self.assertEqual((await self.client.get('/api/lora-datasets')).json()['datasets'], [])

    async def test_remove_records_rejects_busy_engine_origin_and_stale_snapshot(self) -> None:
        entry = self.entry()
        entry = library.trash_dataset(entry.id, LoraDatasetRevision(revision=entry.revision))
        body = {'records': [{'id': entry.id, 'revision': entry.revision}]}
        catalog = library._catalog_path()
        original = catalog.read_bytes()
        for failure in ({'status': 'running'}, library.LoraDatasetError('dataset_native_unavailable', 503)):
            fake = AsyncMock(return_value=failure) if isinstance(failure, dict) else AsyncMock(side_effect=failure)
            with patch.object(library, '_native', fake):
                response = await self.client.post('/api/lora-datasets/remove-records', json=body)
            self.assertIn(response.status_code, (409, 503))
            self.assertEqual(catalog.read_bytes(), original)
        with patch.object(library, '_native', side_effect=self.native):
            response = await self.client.post('/api/lora-datasets/remove-records', json=body, headers={'origin': 'https://foreign.example'})
            self.assertEqual(response.status_code, 403)
            response = await self.client.post('/api/lora-datasets/remove-records', json={'records': [{'id': entry.id, 'revision': entry.revision + 1}]})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['detail'], 'dataset_revision_conflict')
        self.assertEqual(catalog.read_bytes(), original)

    async def test_remove_records_validates_identifiers_revisions_and_duplicate_batch(self) -> None:
        record = {'id': 'a' * 32, 'revision': 0}
        with patch.object(library, 'remove_dataset_records') as remove:
            for records in ([], [record, record], [{'id': '../escape', 'revision': 0}], [{'id': 'a' * 32}], [{'id': 'a' * 32, 'revision': True}]):
                response = await self.client.post('/api/lora-datasets/remove-records', json={'records': records})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['detail'], 'dataset_request_invalid')
            remove.assert_not_called()

    async def test_remove_records_accepts_bounded_full_catalog_and_rejects_oversized_body(self) -> None:
        records = [{'id': f'{index:032x}', 'revision': 0} for index in range(10000)]
        encoded = json.dumps({'records': records}).encode()
        self.assertGreater(len(encoded), 64 * 1024)
        self.assertLess(len(encoded), 1024 * 1024)
        result = RemoveLoraDatasetRecordsResponse(removed_ids=[record['id'] for record in records])
        with patch.object(library, '_native', side_effect=self.native), patch.object(library, 'remove_dataset_records', return_value=result) as remove:
            response = await self.client.post('/api/lora-datasets/remove-records', content=encoded, headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()['removed_ids']), 10000)
            remove.assert_called_once()
            response = await self.client.post('/api/lora-datasets/remove-records', content=b'x' * (1024 * 1024 + 1))
            self.assertEqual(response.status_code, 413)
            remove.assert_called_once()

    async def test_busy_and_unqueryable_engine_block_rename_without_losing_data(self) -> None:
        entry = self.entry()
        original = library.confined_path(entry.dataset_path).read_bytes()
        for failure in ({'status': 'running'}, library.LoraDatasetError('dataset_native_unavailable', 503)):
            with self.subTest(failure=failure):
                fake = AsyncMock(return_value=failure) if isinstance(failure, dict) else AsyncMock(side_effect=failure)
                with patch.object(library, '_native', fake):
                    response = await self.client.post(f'/api/lora-datasets/{entry.id}/rename', json={'name': 'Unsafe', 'revision': entry.revision})
                self.assertIn(response.status_code, (409, 503))
                self.assertEqual(library.get_dataset(entry.id).name, 'Saved work')
                self.assertEqual(library.confined_path(entry.dataset_path).read_bytes(), original)

    async def test_selection_loads_saved_metadata_without_scanning(self) -> None:
        entry = self.entry()
        with patch.object(library, '_native', side_effect=self.native) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/load', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['samples'][0]['caption'], 'keep caption')
        self.assertEqual(response.json()['metadata']['custom_tag'], 'voice')
        self.assertNotIn('v1/dataset/scan', [call.args[1] for call in native.call_args_list])

    async def test_preprocess_receipt_precedes_submission_and_rejects_changed_dataset(self) -> None:
        entry = self.entry()
        output = f'{entry.audio_dir}/tensors'
        with patch.object(library, '_native', side_effect=self.native):
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output})
            self.assertEqual(response.status_code, 200)
            receipt = library.get_workspace().preprocess_receipt
            self.assertIsNotNone(receipt)
            again = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
            self.assertEqual(again.status_code, 200)
            raw = read_object(library.confined_path(entry.dataset_path))
            raw['metadata'] = {'name': 'Saved work', 'custom_tag': 'changed settings'}
            write_object(library.confined_path(entry.dataset_path), raw)
            entry = library.get_dataset(entry.id)
            changed = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()['detail'], 'preprocess_reuse_mismatch')

    async def test_lost_preprocess_response_retains_receipt_without_resubmission(self) -> None:
        entry = self.entry()
        async def lost(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint.endswith('preprocess_async'):
                self.assertIsNotNone(library.get_workspace().preprocess_receipt)
                raise library.LoraDatasetError('dataset_native_unavailable', 503)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=lost) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': f'{entry.audio_dir}/tensors'})
        self.assertEqual(response.status_code, 503)
        self.assertIsNotNone(library.get_workspace().preprocess_receipt)
        self.assertEqual(sum(call.args[1].endswith('preprocess_async') for call in native.call_args_list), 1)

    async def test_scan_keeps_ids_edits_and_missing_sources_and_reloads_merged_document(self) -> None:
        entry = self.entry()
        folder = library.confined_path(entry.audio_dir)
        (folder / 'new.wav').write_bytes(b'new audio')
        original = read_object(library.confined_path(entry.dataset_path))
        scanned: library.JsonObject = {'metadata': {'name': 'Saved work'}, 'samples': [
            {'id': 'changed-id', 'audio_path': str(folder / 'clip.wav'), 'filename': 'clip.wav', 'caption': '', 'labeled': False},
            {'id': 'new-id', 'audio_path': str(folder / 'new.wav'), 'filename': 'new.wav', 'caption': '', 'labeled': False},
        ]}
        async def scan_native(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/save' and body is not None:
                destination = body['save_path']
                self.assertIsInstance(destination, str)
                if isinstance(destination, str):
                    write_object(library.confined_path(destination), scanned)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=scan_native) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/scan', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['samples'][0]['id'], 'keep-id')
        self.assertEqual(response.json()['samples'][0]['caption'], 'keep caption')
        self.assertEqual(response.json()['dataset']['revision'], entry.revision + 1)
        persisted = read_object(library.confined_path(entry.dataset_path))
        self.assertEqual(persisted['samples'][0], original['samples'][0])
        self.assertEqual(len(persisted['samples']), 2)
        self.assertEqual(native.call_args_list[-1].args[1], 'v1/dataset/load')
        self.assertEqual(list(folder.glob('.snapshot*')), [])

    async def test_failed_native_snapshot_preserves_saved_document_and_removes_partial(self) -> None:
        entry = self.entry()
        original = library.confined_path(entry.dataset_path).read_bytes()
        async def interrupted(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/save' and body is not None:
                destination = body['save_path']
                if isinstance(destination, str):
                    library.confined_path(destination).write_text('{partial', encoding='utf-8')
                raise library.LoraDatasetError('dataset_native_unavailable', 503)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=interrupted):
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/save', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(library.confined_path(entry.dataset_path).read_bytes(), original)
        self.assertEqual(list(library.confined_path(entry.audio_dir).glob('.snapshot*')), [])

    async def test_preprocess_rejects_changed_live_model_source_and_unverified_output(self) -> None:
        entry = self.entry()
        output = f'{entry.audio_dir}/tensors'
        with patch.object(library, '_native', side_effect=self.native):
            initial = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output})
            self.assertEqual(initial.status_code, 200)
        async def changed_model(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'health':
                return {'models_initialized': True, 'loaded_model': 'other-base'}
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=changed_model):
            refused = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
        self.assertEqual(refused.status_code, 409)
        (library.confined_path(entry.audio_dir) / 'clip.wav').write_bytes(b'changed source')
        with patch.object(library, '_native', side_effect=self.native):
            refused = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
            self.assertEqual(refused.status_code, 409)
            outside = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': 'datasets/unrelated/tensors'})
            self.assertEqual(outside.status_code, 422)

    async def test_auto_label_loads_exact_saved_dataset_and_persists_submission_context(self) -> None:
        entry = self.entry()
        async def labeling(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/auto_label_async':
                context = library.get_workspace().auto_label_task
                self.assertIsNotNone(context)
                self.assertEqual(context.dataset_id, entry.id)
                self.assertEqual(body['save_path'], entry.dataset_path)
                return {'task_id': 'label-1', 'message': 'started', 'total': 1}
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=labeling) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/auto-label', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 200)
        context = library.get_workspace().auto_label_task
        self.assertEqual(context.task_id, 'label-1')
        calls = [call.args[1] for call in native.call_args_list]
        self.assertLess(calls.index('v1/dataset/load'), calls.index('v1/dataset/auto_label_async'))

    async def test_sample_edit_loads_requested_dataset_and_atomically_persists_revision(self) -> None:
        entry = self.entry()
        raw = read_object(library.confined_path(entry.dataset_path))
        raw['samples'][0]['genre'] = 'original genre'
        raw['samples'][0]['prompt_override'] = 'genre'
        write_object(library.confined_path(entry.dataset_path), raw)
        entry = library.get_dataset(entry.id)
        async def editing(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/sample/0' and body is not None:
                self.assertEqual(body['genre'], 'original genre')
                self.assertEqual(body['prompt_override'], 'genre')
            if endpoint == 'v1/dataset/save' and body is not None:
                destination = body['save_path']
                if isinstance(destination, str):
                    raw = read_object(library.confined_path(entry.dataset_path))
                    raw['samples'][0]['caption'] = 'new manual edit'
                    write_object(library.confined_path(destination), raw)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=editing) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/samples/0', json={'revision': entry.revision, 'caption': 'new manual edit'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['dataset']['revision'], entry.revision + 1)
        self.assertEqual(response.json()['samples'][0]['caption'], 'new manual edit')
        self.assertEqual(native.call_args_list[3].args[1], 'v1/dataset/load')
        stale = await self.client.post(f'/api/lora-datasets/{entry.id}/samples/0', json={'revision': entry.revision})
        self.assertEqual(stale.status_code, 409)

    async def test_save_reloads_own_snapshot_before_saving_shared_source_builder(self) -> None:
        entry = self.entry()
        async def own_snapshot(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/save' and body is not None:
                self.assertIn('v1/dataset/load', [call.args[1] for call in native.call_args_list[:-1]])
                destination = body['save_path']
                if isinstance(destination, str):
                    write_object(library.confined_path(destination), read_object(library.confined_path(entry.dataset_path)))
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=own_snapshot) as native:
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/save', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['samples'][0]['caption'], 'keep caption')

    async def test_tensor_reuse_preserves_valid_zip_and_quarantines_partial_without_unpickling(self) -> None:
        entry = self.entry()
        output = f'{entry.audio_dir}/tensors'
        with patch.object(library, '_native', side_effect=self.native):
            first = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output})
            self.assertEqual(first.status_code, 200)
            folder = library.confined_path(output)
            folder.mkdir()
            tensor = folder / 'keep-id.pt'
            tensor.write_bytes(b'partial archive')
            resumed = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
            self.assertEqual(resumed.status_code, 200)
            self.assertFalse(tensor.exists())
            quarantined = list(folder.glob('.openfabric-partial-*'))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b'partial archive')
            self.assertEqual(list(folder.glob('*.pt')), [])
            with zipfile.ZipFile(tensor, 'w') as archive:
                archive.writestr('keep-id/data.pkl', b'not unpickled during validation')
                archive.writestr('keep-id/data/0', b'complete tensor fixture')
                archive.writestr('keep-id/version', b'3\n')
                archive.writestr('keep-id/byteorder', b'little')
            original = tensor.read_bytes()
            valid = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
            self.assertEqual(valid.status_code, 200)
            self.assertEqual(tensor.read_bytes(), original)
            tensor.unlink()
            tensor.symlink_to(self.root / 'private.pt')
            unsafe = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': output, 'skip_existing': True})
            self.assertEqual(unsafe.status_code, 422)

    async def test_replacing_same_source_path_invalidates_label_and_preserves_annotation_provenance(self) -> None:
        folder = self.engine / 'datasets' / 'replacement'
        folder.mkdir(parents=True)
        source = folder / 'clip.wav'
        source.write_bytes(b'original source')
        dataset = folder / 'dataset.json'
        original: library.JsonObject = {'metadata': {'name': 'Replacement'}, 'samples': [{
            'id': 'same-id', 'audio_path': str(source), 'filename': 'clip.wav', 'caption': 'original manual caption',
            'lyrics': 'original lyrics', 'raw_lyrics': 'source lyrics', 'labeled': True, 'duration': 12,
        }]}
        write_object(dataset, original)
        entry = library.list_datasets().datasets[0]
        source.write_bytes(b'a distinctly different replacement source')
        # Listing must not erase the stored historical signature.
        library.list_datasets()
        scanned: library.JsonObject = {'metadata': {'name': 'Replacement'}, 'samples': [{
            'id': 'scanner-id', 'audio_path': str(source), 'filename': 'clip.wav', 'caption': '', 'labeled': False, 'duration': 23,
        }]}
        async def native_scan(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/save' and body is not None:
                destination = body['save_path']
                if isinstance(destination, str):
                    write_object(library.confined_path(destination), scanned)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=native_scan):
            refused = await self.client.post(f'/api/lora-datasets/{entry.id}/preprocess', json={'revision': entry.revision, 'output_dir': f'{entry.audio_dir}/tensors'})
            self.assertEqual(refused.status_code, 409)
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/scan', json={'revision': entry.revision})
            self.assertEqual(response.status_code, 200, response.text)
            sample = response.json()['samples'][0]
            self.assertFalse(sample['labeled'])
            self.assertEqual(sample['id'], 'same-id')
            self.assertEqual(sample['caption'], 'original manual caption')
            self.assertEqual(sample['lyrics'], 'original lyrics')
            self.assertEqual(sample['raw_lyrics'], 'source lyrics')
            self.assertEqual(sample['duration'], 23)
            self.assertEqual(response.json()['message'], 'dataset_sources_changed')
            again = await self.client.post(f'/api/lora-datasets/{entry.id}/scan', json={'revision': response.json()['dataset']['revision']})
            self.assertEqual(again.status_code, 200)
            self.assertEqual(again.json()['message'], '')
            self.assertFalse(again.json()['samples'][0]['labeled'])

    async def test_old_catalog_scan_establishes_source_baseline_with_explicit_unknown_history(self) -> None:
        entry = self.entry()
        path = library._catalog_path()
        catalog = read_object(path)
        catalog.pop('source_signatures', None)
        write_object(path, catalog)
        original = read_object(library.confined_path(entry.dataset_path))
        async def baseline(method: str, endpoint: str, body: library.JsonObject | None = None) -> library.JsonObject:
            if endpoint == 'v1/dataset/save' and body is not None:
                destination = body['save_path']
                if isinstance(destination, str):
                    write_object(library.confined_path(destination), original)
            return await self.native(method, endpoint, body)
        with patch.object(library, '_native', side_effect=baseline):
            response = await self.client.post(f'/api/lora-datasets/{entry.id}/scan', json={'revision': entry.revision})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'dataset_source_history_unavailable')

    async def test_cross_origin_and_oversized_workspace_are_rejected(self) -> None:
        foreign = await self.client.put('/api/lora-datasets/workspace', json={}, headers={'origin': 'https://foreign.invalid'})
        self.assertEqual(foreign.status_code, 403)
        oversized = await self.client.put('/api/lora-datasets/workspace', content=b'x' * (128 * 1024))
        self.assertEqual(oversized.status_code, 413)
