"""Offline reference boundaries and durable ownership use a private catalog."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app import db
from app import reference_imports as refs
from app.reference_contracts import ReferenceImportRequest, ReferenceSource, ReferenceTrackPreparationRequest


class ReferenceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(refs, '_accepting_work', True))

    async def test_failed_probe_drain_retains_receipt_and_blocks_work_until_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.enterContext(patch.object(db, 'FILES_DIR', root / 'files'))
            self.enterContext(patch.object(refs, '_recovered_root', None))
            self.enterContext(patch.dict(refs._jobs, {}, clear=True))
            self.enterContext(patch.object(refs, '_probe_tasks', set()))
            self.enterContext(patch.object(refs, '_unverified_workers', set()))
            process = AsyncMock(spec=asyncio.subprocess.Process)
            process.returncode = 0

            async def spawn(argv: list[str], *, receipt_path: Path, cwd: Path | None = None,
                            env: Mapping[str, str] | None = None, stdout: int | None = None,
                            on_identity: Callable[[refs.WorkerIdentity], None] | None = None) -> asyncio.subprocess.Process:
                identity = refs.WorkerIdentity(pid=123, token='a' * 32, receipt=str(receipt_path))
                receipt_path.write_text(json.dumps({'pid': 123, 'token': 'a' * 32, 'returncode': 0,
                                                   'job_name': 'Local\\OpenFabricNative_' + 'b' * 32}))
                if on_identity is not None:
                    on_identity(identity)
                return process

            self.enterContext(patch.object(refs, 'spawn_owned', side_effect=spawn))
            self.enterContext(patch.object(refs, 'read_owned_output', AsyncMock(return_value=b'local fixture')))

            async def local_probe(url: str, workspace: Path, *, download: bool,
                                  identifier: str | None = None, language: str | None = None,
                                  probe_id: str | None = None) -> refs._YtdlpInfo:
                command = [sys.executable, '-c', "print('local probe fixture')"]
                if probe_id is None:
                    await refs._run_tool(command, workspace, timeout=5)
                else:
                    await refs._run_tool(command, workspace, timeout=5, probe_id=probe_id)
                return refs._YtdlpInfo(id='abcdefghijk', title='Fixture', duration=10)

            try:
                with patch.object(refs, '_ytdlp', side_effect=local_probe), patch.object(refs, 'kill_process_tree', AsyncMock(side_effect=OSError('descendants not verified'))):
                    with self.assertRaises((refs.ReferenceImportError, OSError)) as failure:
                        await refs.probe('https://youtu.be/abcdefghijk')
                self.assertFalse(refs._probe_tasks, 'Public request has finished')
                self.assertTrue(refs.work_busy(), 'An exited wrapper cannot discard descendant ownership')
                self.assertIsInstance(failure.exception, refs.ReferenceImportError)
                if isinstance(failure.exception, refs.ReferenceImportError):
                    self.assertEqual(failure.exception.code, 'busy')
                staged = list(refs.references_root().glob('.probe-*'))
                self.assertEqual(len(staged), 1)
                receipts = list(staged[0].glob('receipt-*.json'))
                self.assertEqual(len(receipts), 1)
                receipt_bytes = receipts[0].read_bytes()
                self.assertEqual(json.loads(receipt_bytes)['returncode'], 0)
                with patch.object(refs, 'terminate_verified', AsyncMock(return_value=False)):
                    await refs.shutdown()
                    await refs.start()
                    with self.assertRaises(refs.ReferenceImportError) as blocked:
                        await refs.create(ReferenceImportRequest(url='https://youtu.be/abcdefghijk'))
                    self.assertEqual(blocked.exception.code, 'busy')
                    self.assertTrue(refs.work_busy())
                    self.assertEqual(receipts[0].read_bytes(), receipt_bytes)
                    self.assertFalse(refs.list_imports().imports, 'Blocked admission cannot create an orphan record')
                with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)), patch.object(refs, 'kill_process_tree', AsyncMock()):
                    await refs.recover()
                self.assertFalse(refs.work_busy())
                self.assertFalse(staged[0].exists())
            finally:
                # This fixture never starts a model, network tool, or real child process.
                with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)), patch.object(refs, 'kill_process_tree', AsyncMock()):
                    await refs.shutdown()

    async def test_restart_recovers_probe_receipt_written_before_spawn_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.enterContext(patch.object(db, 'FILES_DIR', Path(temporary).resolve() / 'files'))
            self.enterContext(patch.object(refs, '_recovered_root', None))
            self.enterContext(patch.object(refs, '_probe_owners', {}))
            self.enterContext(patch.object(refs, '_probe_tasks', set()))
            self.enterContext(patch.dict(refs._jobs, {}, clear=True))
            identifier = 'c' * 32
            workspace = refs.references_root() / ('.probe-' + identifier)
            workspace.mkdir(parents=True)
            (workspace / 'probe.json').write_text('{"worker":null}')
            receipt = workspace / ('receipt-' + 'd' * 32 + '.json')
            proof = {'pid': 456, 'token': 'e' * 32, 'returncode': 0,
                     'job_name': 'Local\\OpenFabricNative_' + 'f' * 32}
            receipt.write_text(json.dumps(proof))
            with patch.object(refs, 'terminate_verified', AsyncMock(return_value=False)) as terminate:
                await refs.recover()
                self.assertTrue(refs.work_busy())
                identity = terminate.call_args.args[0]
                self.assertEqual((identity.pid, identity.token, identity.receipt), (456, 'e' * 32, str(receipt)))
                self.assertEqual(json.loads(receipt.read_text()), proof, 'Job accounting proof must stay intact')
            with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)):
                await refs.recover()
            self.assertFalse(refs.work_busy())
            self.assertFalse(workspace.exists())

    async def test_late_probe_cancellation_always_unregisters_the_completed_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.enterContext(patch.object(db, 'FILES_DIR', Path(temporary).resolve() / 'files'))
            self.enterContext(patch.object(refs, '_recovered_root', None))
            self.enterContext(patch.object(refs, '_probe_tasks', set()))
            source = ReferenceSource(canonical_url='https://www.youtube.com/watch?v=abcdefghijk', video_id='abcdefghijk',
                                     title='Fixture', duration_seconds=10, subtitle_languages=[])
            # await_cleanup propagates caller cancellation after the owned task has drained.
            with patch.object(refs, '_probe_inner', AsyncMock(return_value=source)), patch.object(refs, 'await_cleanup', AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await refs.probe(source.canonical_url)
            self.assertFalse(refs._probe_tasks)

    async def test_canonical_urls_reject_other_providers_and_ambiguous_input(self) -> None:
        self.assertEqual(refs.canonical_youtube_url('https://youtu.be/abcdefghijk?t=30'), ('https://www.youtube.com/watch?v=abcdefghijk', 'abcdefghijk'))
        for url in ['http://www.youtube.com/watch?v=abcdefghijk', 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://user@youtube.com/watch?v=abcdefghijk', 'https://youtube.com:8443/watch?v=abcdefghijk', 'file:///tmp/source', 'https://youtube.com/playlist?list=abc', 'https://youtube.com/watch?v=abcdefghijk&v=12345678901']:
            with self.subTest(url=url), self.assertRaises(refs.ReferenceImportError):
                refs.canonical_youtube_url(url)

    async def test_every_resolved_address_must_be_public(self) -> None:
        self.assertEqual(refs.public_addresses('www.youtube.com', ['8.8.8.8', '2001:4860:4860::8888']), ['8.8.8.8', '2001:4860:4860::8888'])
        for addresses in [['127.0.0.1'], ['8.8.8.8', '10.0.0.1'], ['::1'], ['169.254.169.254'], ['224.0.0.1'], []]:
            with self.subTest(addresses=addresses), self.assertRaises(refs.ReferenceImportError):
                refs.public_addresses('www.youtube.com', addresses)

    async def test_proxy_rejects_redirect_targets_and_non_https_tunnels(self) -> None:
        async with refs.ProviderProxy(max_bytes=1024) as proxy:
            for target in ['127.0.0.1:443', 'evil.test:443', 'www.youtube.com:80']:
                reader, writer = await asyncio.open_connection('127.0.0.1', proxy.port)
                writer.write(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode())
                await writer.drain()
                self.assertTrue((await reader.readline()).startswith(b'HTTP/1.1 403'))
                writer.close()
                await writer.wait_closed()

    async def test_command_ignores_user_configs_and_keeps_source_codec(self) -> None:
        command = refs.ytdlp_command('https://www.youtube.com/watch?v=abcdefghijk', Path('/temporary/source'), 'http://127.0.0.1:9999', download=True)
        self.assertIn('--ignore-config', command)
        self.assertIn('--no-plugin-dirs', command)
        self.assertIn('--proxy', command)
        self.assertEqual(command[command.index('--js-runtimes') + 1], 'deno')
        self.assertNotIn('--extract-audio', command)
        self.assertNotIn('--audio-format', command)
        self.assertNotIn('--exec', command)
        self.assertNotIn('mp3', command)

    async def test_unreviewed_downloader_version_and_missing_solver_fail_closed(self) -> None:
        for release, solver in [('2027.1.1', '0.8.0'), ('2026.8.19', None)]:
            def version(name: str) -> str:
                if name == 'yt-dlp':
                    return release
                if solver is None:
                    raise refs.importlib.metadata.PackageNotFoundError(name)
                return solver
            with patch.object(refs.importlib.util, 'find_spec', return_value=object()), patch.object(refs.shutil, 'which', return_value='/fake/node'), patch.object(refs.importlib.metadata, 'version', side_effect=version):
                self.assertFalse(refs.capabilities().source_import.available)

    async def test_probe_requires_actual_audio_stream_and_decoder_restrictions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with patch.object(refs, '_tool_path', return_value='ffprobe'), patch.object(refs, '_run_tool', AsyncMock(return_value=b'{"format":{"duration":"10"},"streams":[]}')) as run:
                with self.assertRaises(refs.ReferenceImportError) as failure:
                    await refs._inspect_audio(workspace / 'source.wav', workspace, 'a' * 32)
                self.assertEqual(failure.exception.code, 'invalid_audio')
                command = run.call_args.args[0]
                self.assertIn('-protocol_whitelist', command)
                self.assertIn('-format_whitelist', command)


    async def test_proxy_pins_numeric_public_ip_and_enforces_tunnel_byte_limit(self) -> None:
        import socket
        async def serve(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b'x' * 256)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        port = int(server.sockets[0].getsockname()[1])
        original = asyncio.open_connection
        connections: list[str] = []
        async def numeric(host: str, target_port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            connections.append(host)
            self.assertEqual(host, '8.8.8.8')
            self.assertEqual(target_port, 443)
            return await original('127.0.0.1', port)
        try:
            async with refs.ProviderProxy(max_bytes=64) as proxy:
                resolved = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 443))]
                loop = asyncio.get_running_loop()
                with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=resolved)), patch.object(refs.asyncio, 'open_connection', side_effect=numeric):
                    reader, writer = await original('127.0.0.1', proxy.port)
                    writer.write(b'CONNECT www.youtube.com:443 HTTP/1.1\r\nHost: www.youtube.com:443\r\n\r\n')
                    await writer.drain()
                    self.assertIn(b'200 Connection established', await asyncio.wait_for(reader.read(), 3))
                    writer.close()
                    await writer.wait_closed()
                self.assertEqual(proxy.error, 'network_limit')
                self.assertEqual(connections, ['8.8.8.8'])
        finally:
            server.close()
            await server.wait_closed()



class ReferenceImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'), ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.dict(refs._jobs, {}, clear=True))
        self.enterContext(patch.object(refs, '_recovered_root', None))
        self.enterContext(patch.object(refs, '_unverified_workers', set()))
        self.enterContext(patch.object(refs, '_accepting_work', True))
        self.source = ReferenceSource(canonical_url='https://www.youtube.com/watch?v=abcdefghijk', video_id='abcdefghijk', title='Reference', duration_seconds=10, subtitle_languages=['de'])
        self.real_probe = refs.probe
        self.enterContext(patch.object(refs, 'probe', AsyncMock(return_value=self.source)))
        self.master_bytes = b'original encoded audio data, deliberately not MP3'
        async def downloaded(_job: object, workspace: Path) -> Path:
            result = workspace / 'source.m4a'
            result.write_bytes(self.master_bytes)
            return result
        self.enterContext(patch.object(refs, '_download_source', side_effect=downloaded))
        self.enterContext(patch.object(refs, '_inspect_audio', AsyncMock(return_value=10.0)))

    async def asyncTearDown(self) -> None:
        await refs.shutdown()
        if db._db is not None:
            db._db.close()

    async def finished(self, identifier: str) -> None:
        task = refs._jobs[identifier].task
        self.assertIsNotNone(task)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 3)

    async def test_source_import_uses_same_catalog_upload_origin_and_preserves_master_bytes(self) -> None:
        job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url))
        await self.finished(job.id)
        done = refs.get(job.id)
        self.assertEqual(done.status, 'done')
        row = db.get_track(done.track_id or 0)
        self.assertIsNotNone(row)
        if row is not None:
            self.assertEqual(row['model'], 'upload')
            self.assertEqual(Path(row['audio_path']).read_bytes(), self.master_bytes)
            self.assertEqual(json.loads(row['params_json'])['reference_import']['video_id'], 'abcdefghijk')
        refs._jobs.clear()
        self.assertEqual(refs.get(job.id).track_id, done.track_id, 'Reload reads durable state')

    async def test_missing_optional_tool_leaves_imported_source_usable(self) -> None:
        with patch.object(refs, '_transcribe_whisper', side_effect=refs.ReferenceImportError('whisper_missing')):
            job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url, lyrics_source='whisper'))
            await self.finished(job.id)
        done = refs.get(job.id)
        self.assertEqual(done.status, 'partial')
        self.assertIsNotNone(done.track_id)
        self.assertEqual(next(stage for stage in done.stages if stage.name == 'lyrics').error_code, 'whisper_missing')

    async def test_cancel_owns_task_and_removes_partial_workspace_then_retry_succeeds(self) -> None:
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        async def pending(_job: object, workspace: Path) -> Path:
            (workspace / 'partial').write_bytes(b'partial')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
            return workspace / 'source.m4a'
        with patch.object(refs, '_download_source', side_effect=pending):
            job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url))
            await entered.wait()
            cancelled = await refs.cancel(job.id)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertFalse(any(p.name.startswith('staging-') for p in refs.job_dir(job.id).iterdir()))
        await refs.retry(job.id)
        await self.finished(job.id)
        self.assertEqual(refs.get(job.id).status, 'done')
        self.assertEqual(len(db.list_tracks('upload')), 1)

    async def test_delete_import_keeps_saved_source_and_other_jobs(self) -> None:
        first = await refs.create(ReferenceImportRequest(url=self.source.canonical_url))
        second = await refs.create(ReferenceImportRequest(url=self.source.canonical_url))
        await self.finished(first.id)
        await self.finished(second.id)
        retained = refs.get(first.id).track_id
        result = await refs.delete(first.id)
        self.assertTrue(result.track_retained)
        self.assertIsNotNone(db.get_track(retained or 0))
        self.assertEqual(refs.get(second.id).status, 'done')

    async def test_recovery_marks_interrupted_job_and_never_creates_duplicate_source(self) -> None:
        job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url))
        await self.finished(job.id)
        stored = refs._read(job.id)
        stored.public.status = 'running'
        stored.public.stages[1].status = 'running'
        refs._write(job.id, stored)
        refs._jobs.clear()
        refs._recovered_root = None
        await refs.recover()
        self.assertEqual(refs.get(job.id).status, 'partial')
        self.assertEqual(refs.get(job.id).stages[1].error_code, 'import_interrupted')
        await refs.retry(job.id)
        await self.finished(job.id)
        self.assertEqual(len(db.list_tracks('upload')), 1)

    async def test_track_preparation_uses_catalog_source_and_validates_melody(self) -> None:
        source = self.root / 'files/upload/local.wav'
        source.parent.mkdir(parents=True)
        source.write_bytes(self.master_bytes)
        track_id = db.insert_track(model='upload', title='Local', lyrics='', seed=None, duration_ms=10_000, wall_ms=None, params={'source': 'user_upload'}, audio_path=source, abc_path=None)
        with patch.object(refs, '_prepare_melody', AsyncMock(return_value='X:1\nK:G\nF =F | F\n')):
            job = await refs.prepare(ReferenceTrackPreparationRequest(track_id=track_id))
            await self.finished(job.id)
        self.assertEqual(refs.get(job.id).track_id, track_id)
        self.assertIsNotNone(refs.get(job.id).abc)
        self.assertEqual(source.read_bytes(), self.master_bytes)
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_import_directory_symlinks_cannot_escape_library(self) -> None:
        identifier = 'f' * 32
        root = refs.references_root()
        root.mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (root / identifier).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(refs.ReferenceImportError):
            refs.job_dir(identifier)

    async def test_promotion_interruption_adopts_only_verified_original_bytes(self) -> None:
        class SimulatedCrash(BaseException):
            pass
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        job.source = self.source
        refs._save(job)
        stage = refs.job_dir(job.id) / 'staging-crash'
        stage.mkdir()
        source = stage / 'source.m4a'
        source.write_bytes(self.master_bytes)
        with patch.object(db, 'insert_track', side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            refs._persist_source(job, source, 10)
        refs._recovered_root = None
        await refs.recover()
        with patch.object(refs, '_download_source', side_effect=AssertionError('Recovery must retain promoted master')):
            await refs.retry(job.id)
            await self.finished(job.id)
        self.assertEqual(refs.get(job.id).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_retry_admission_failure_leaves_previous_status_unchanged(self) -> None:
        target = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        target.status = 'cancelled'
        refs._save(target)
        pending = asyncio.create_task(asyncio.Event().wait())
        with patch.dict(refs._jobs, {'a': refs._OwnedJob(task=pending), 'b': refs._OwnedJob(task=pending)}):
            try:
                with self.assertRaises(refs.ReferenceImportError):
                    await refs.retry(target.id)
                self.assertEqual(refs.get(target.id).status, 'cancelled')
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_unverified_recovered_worker_blocks_library_mutation(self) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        stored = refs._read(job.id)
        stored.worker = refs._Worker(pid=123, token='e' * 32, receipt_name='receipt-' + 'd' * 32 + '.json')
        refs._write(job.id, stored)
        with patch.object(refs, 'terminate_verified', AsyncMock(return_value=False)):
            await refs.recover()
        self.assertTrue(refs.work_busy())
        with self.assertRaises(refs.ReferenceImportError):
            await refs.delete(job.id)

    async def test_separated_vocal_is_durable_and_served_without_touching_master(self) -> None:
        async def separate(_job: object, _source: Path, workspace: Path) -> Path:
            vocal = workspace / 'vocal.wav'
            vocal.write_bytes(b'dry vocal')
            return vocal
        with patch.object(refs, '_separate', side_effect=separate):
            job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url, separation='fast'))
            await self.finished(job.id)
        done = refs.get(job.id)
        self.assertEqual(done.status, 'done')
        self.assertEqual(refs.vocal_path(job.id).read_bytes(), b'dry vocal')
        row = db.get_track(done.track_id or 0)
        self.assertIsNotNone(row)
        if row is not None:
            self.assertEqual(Path(row['audio_path']).read_bytes(), self.master_bytes)

    async def test_original_version_wins_over_current_lossy_export(self) -> None:
        from app.audio_versions import retain_original
        source = db.model_dir('upload') / 'master.wav'
        source.write_bytes(self.master_bytes)
        track = db.insert_track(model='upload', title='Original', lyrics='', seed=None, duration_ms=10000, wall_ms=None, params={}, audio_path=source, abc_path=None)
        retain_original(track, source)
        lossy = source.with_suffix('.mp3')
        lossy.write_bytes(b'lossy current audio')
        db.get_db().execute('UPDATE tracks SET audio_path=? WHERE id=?', (str(lossy), track))
        db.get_db().commit()
        async def melody(_job: object, selected: Path, _workspace: Path) -> str:
            self.assertEqual(selected.read_bytes(), self.master_bytes)
            return 'X:1\nK:C\nC D E'
        with patch.object(refs, '_prepare_melody', side_effect=melody):
            job = await refs.prepare(ReferenceTrackPreparationRequest(track_id=track))
            await self.finished(job.id)
        self.assertEqual(refs.get(job.id).status, 'done')

    async def test_tool_environment_cannot_bypass_proxy_using_no_proxy(self) -> None:
        workspace = self.root / 'worker'
        workspace.mkdir()
        original = refs.spawn_owned
        async def checked(command: list[str], **kwargs: object) -> asyncio.subprocess.Process:
            environment = kwargs.get('env')
            self.assertIsInstance(environment, dict)
            if isinstance(environment, dict):
                self.assertNotIn('NO_PROXY', environment)
                self.assertNotIn('no_proxy', environment)
                self.assertNotIn('NODE_OPTIONS', environment)
            return await original(command, receipt_path=workspace / 'receipt.json', cwd=workspace, stdout=asyncio.subprocess.PIPE)
        with patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*', 'NODE_OPTIONS': '--inspect'}), patch.object(refs, 'spawn_owned', side_effect=checked):
            result = await refs._run_tool([sys.executable, '-c', 'print("safe")'], workspace, timeout=3)
        self.assertEqual(result.strip(), b'safe')

    async def test_preparation_requires_review_before_replacing_catalog_lyrics_or_score(self) -> None:
        source = db.model_dir('upload') / 'local.wav'
        source.write_bytes(self.master_bytes)
        existing_score = source.with_suffix('.abc')
        existing_score.write_text('X:1\nK:C\nC')
        track = db.insert_track(model='upload', title='Reviewed', lyrics='[chorus]\nKeep me', seed=None, duration_ms=10000, wall_ms=None, params={}, audio_path=source, abc_path=existing_score)
        with patch.object(refs, '_prepare_melody', AsyncMock(return_value='X:1\nK:D\nD E F')):
            job = await refs.prepare(ReferenceTrackPreparationRequest(track_id=track))
            await self.finished(job.id)
        row = db.get_track(track)
        self.assertIsNotNone(row)
        if row is not None:
            self.assertEqual(row['lyrics'], '[chorus]\nKeep me')
            self.assertEqual(row['abc_path'], str(existing_score))
        self.assertEqual(refs.get(job.id).abc, 'X:1\nK:D\nD E F')

    async def test_promoted_source_content_conflict_is_preserved_and_never_adopted(self) -> None:
        class SimulatedCrash(BaseException):
            pass
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        job.source = self.source
        refs._save(job)
        workspace = refs.job_dir(job.id) / 'staging-crash'
        workspace.mkdir()
        source = workspace / 'source.m4a'
        source.write_bytes(self.master_bytes)
        with patch.object(db, 'insert_track', side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            refs._persist_source(job, source, 10)
        target = db.model_dir('upload') / f'reference_{job.id}.m4a'
        target.write_bytes(b'conflicting data')
        refs._recovered_root = None
        await refs.recover()
        await refs.retry(job.id)
        await self.finished(job.id)
        self.assertEqual(refs.get(job.id).status, 'failed')
        self.assertEqual(target.read_bytes(), b'conflicting data')
        self.assertEqual(db.list_tracks(), [])

    async def test_symlinked_reference_metadata_root_cannot_escape_files(self) -> None:
        outside = self.root / 'outside'
        outside.mkdir()
        db.FILES_DIR.mkdir()
        refs.references_root().symlink_to(outside, target_is_directory=True)
        with self.assertRaises(refs.ReferenceImportError):
            refs.job_dir('a' * 32)

    async def test_reference_separation_cancellation_drains_supervisor_and_receipt(self) -> None:
        from app import voice_separation
        from app.stems import SpawnProcess
        entered = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []
        caps = refs.capabilities()
        caps.separation[0].available = True
        async def separate(source: Path, output: Path, *, quality: str, log_name: str, on_proc: object, spawn: SpawnProcess) -> Path:
            proc = await spawn([sys.executable, '-c', 'import time;time.sleep(60)'], output.parent, os.environ.copy(), asyncio.subprocess.DEVNULL)
            children.append(proc)
            entered.set()
            await proc.wait()
            return output / 'vocals.wav'
        with patch.object(refs, 'capabilities', return_value=caps), patch.object(voice_separation, 'separate_vocal', side_effect=separate):
            job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url, separation='fast'))
            await asyncio.wait_for(entered.wait(), 3)
            stored = refs._read(job.id)
            self.assertIsNotNone(stored.worker)
            await refs.cancel(job.id)
        self.assertIsNotNone(children[0].returncode)
        self.assertIsNone(refs._read(job.id).worker)
        self.assertFalse(list(refs.job_dir(job.id).glob('receipt-*')))
        self.assertEqual(refs.get(job.id).status, 'cancelled')

    async def test_failed_worker_termination_keeps_ownership_and_blocks_mutation(self) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        refs._jobs[job.id] = refs._OwnedJob()
        workspace = refs.job_dir(job.id) / 'staging-failed-stop'
        workspace.mkdir()
        with patch.object(refs, 'read_owned_output', AsyncMock(side_effect=refs.ReferenceImportError('tool_timeout'))), patch.object(refs, 'kill_process_tree', AsyncMock(side_effect=OSError('stop failed'))):
            with self.assertRaises(OSError):
                await refs._run_tool([sys.executable, '-c', 'import time;time.sleep(60)'], workspace, timeout=1, identifier=job.id)
        worker = refs._jobs[job.id].proc
        try:
            self.assertTrue(refs.work_busy())
            self.assertIsNotNone(refs._read(job.id).worker)
            with self.assertRaises(refs.ReferenceImportError):
                await refs.delete(job.id)
        finally:
            await refs.kill_process_tree(worker)
            refs._unverified_workers.discard(job.id)
            refs._jobs[job.id].proc = None

    async def test_unvalidated_native_scores_are_never_published_and_failure_is_durable(self) -> None:
        for candidate, code in [('X:1\nK:C\n(3CDE', 'unsupported_abc'), ('X:1\nK:C\nC' + ' ' * 100001, 'size_limit')]:
            with self.subTest(code=code):
                with patch.object(refs, '_prepare_melody', AsyncMock(return_value=candidate)):
                    job = await refs.create(ReferenceImportRequest(url=self.source.canonical_url, melody=True))
                    await self.finished(job.id)
                done = refs.get(job.id)
                self.assertEqual(done.status, 'partial')
                self.assertIsNone(done.abc)
                stage = next(stage for stage in done.stages if stage.name == 'melody')
                self.assertEqual(stage.status, 'failed')
                self.assertEqual(stage.error_code, code)
                self.assertFalse((refs.job_dir(job.id) / 'melody.abc').exists())

    async def test_probe_rejects_symlinked_root_before_temporary_creation_or_network(self) -> None:
        outside = self.root / 'outside'
        outside.mkdir()
        db.FILES_DIR.mkdir()
        refs.references_root().symlink_to(outside, target_is_directory=True)
        with patch.object(refs, '_ytdlp', AsyncMock(return_value=refs._YtdlpInfo(id='abcdefghijk', title='Reference', duration=10))) as downloader:
            with self.assertRaises(refs.ReferenceImportError) as error:
                await refs._probe_inner(self.source.canonical_url)
            self.assertEqual(error.exception.code, 'source_missing')
            downloader.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    async def test_video_admission_blocks_create_prepare_and_retry_without_orphan_records(self) -> None:
        from app import video_jobs
        source = db.model_dir('upload') / 'song.wav'
        source.write_bytes(self.master_bytes)
        track = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=10000, wall_ms=None, params={}, audio_path=source, abc_path=None)
        existing = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        existing.status = 'cancelled'
        refs._save(existing)
        with patch.object(video_jobs, 'work_busy', return_value=True):
            for operation in [lambda: refs.create(ReferenceImportRequest(url=self.source.canonical_url)), lambda: refs.prepare(ReferenceTrackPreparationRequest(track_id=track)), lambda: refs.retry(existing.id)]:
                with self.assertRaises(refs.ReferenceImportError) as error:
                    await operation()
                self.assertEqual(error.exception.code, 'busy')
                self.assertEqual([job.id for job in refs.list_imports().imports], [existing.id])
                self.assertEqual(refs._jobs, {})
                self.assertEqual(refs.get(existing.id).status, 'cancelled')

    async def _cancel_retry_race(self, *, deleting: bool) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        job.status = 'running'
        refs._save(job)
        async def old_run() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                job.status = 'cancelled'
                refs._save(job)
                raise
        original = asyncio.create_task(old_run())
        await asyncio.sleep(0)
        refs._jobs[job.id] = refs._OwnedJob(task=original)
        spawned: list[asyncio.Task[None]] = []
        retries: list[asyncio.Task[refs.ReferenceImport]] = []
        async def replacement(identifier: str) -> None:
            task = asyncio.current_task()
            if task is not None:
                spawned.append(task)
            public = refs.get(identifier)
            public.status = 'running'
            refs._save(public)
            await asyncio.Event().wait()
        original.add_done_callback(lambda _done: retries.append(asyncio.create_task(refs.retry(job.id))))
        try:
            with patch.object(refs, '_run', side_effect=replacement):
                if deleting:
                    result = await refs.delete(job.id)
                    self.assertTrue(result.deleted)
                else:
                    await refs.cancel(job.id)
                await asyncio.gather(*retries, return_exceptions=True)
                await asyncio.sleep(0)
            if deleting:
                self.assertFalse(any(not task.done() for task in spawned))
                self.assertNotIn(job.id, refs._jobs)
            else:
                self.assertEqual(len(spawned), 1)
                self.assertIs(refs._jobs[job.id].task, spawned[0])
                self.assertEqual(refs.get(job.id).status, 'running')
        finally:
            for task in spawned:
                task.cancel()
            await asyncio.gather(*spawned, return_exceptions=True)

    async def test_delete_cannot_lose_replacement_task_started_by_retry(self) -> None:
        await self._cancel_retry_race(deleting=True)

    async def test_delete_retains_proof_when_cancellation_introduces_a_drain_failure(self) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        process = AsyncMock(spec=asyncio.subprocess.Process)
        process.returncode = 0
        receipt = refs.job_dir(job.id) / ('receipt-' + 'b' * 32 + '.json')
        receipt.write_text('retained process proof')
        stored = refs._read(job.id)
        stored.worker = refs._Worker(pid=123, token='a' * 32, receipt_name=receipt.name)
        refs._write(job.id, stored)

        async def running() -> None:
            try:
                await asyncio.Future()
            finally:
                await refs._drain_worker(job.id, process)

        task = asyncio.create_task(running())
        refs._jobs[job.id] = refs._OwnedJob(task=task, proc=process)
        await asyncio.sleep(0)
        try:
            with patch.object(refs, 'kill_process_tree', AsyncMock(side_effect=OSError('descendants not drained'))):
                with self.assertRaises(refs.ReferenceImportError) as error:
                    await refs.delete(job.id)
                self.assertEqual(error.exception.code, 'busy')
            self.assertTrue(refs.work_busy())
            self.assertEqual(receipt.read_text(), 'retained process proof')
            self.assertIs(refs._jobs[job.id].proc, process)
            self.assertIsNotNone(refs._read(job.id).worker)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)), patch.object(refs, 'kill_process_tree', AsyncMock()):
                await refs.recover()

    async def test_cancel_cannot_overwrite_replacement_generation_status(self) -> None:
        await self._cancel_retry_race(deleting=False)

    async def test_shutdown_rejects_retry_callbacks_and_new_work_until_explicit_start(self) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        job.status = 'running'
        refs._save(job)

        async def original_run() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                job.status = 'cancelled'
                refs._save(job)
                raise

        original = asyncio.create_task(original_run())
        await asyncio.sleep(0)
        refs._jobs[job.id] = refs._OwnedJob(task=original)
        spawned: list[asyncio.Task[None]] = []
        retries: list[asyncio.Task[object]] = []

        async def replacement(identifier: str) -> None:
            current = asyncio.current_task()
            if current is not None:
                spawned.append(current)
            await asyncio.Future()

        original.add_done_callback(lambda _done: retries.append(asyncio.create_task(refs.retry(job.id))))
        try:
            with patch.object(refs, '_run', side_effect=replacement):
                await refs.shutdown()
                await asyncio.gather(*retries, return_exceptions=True)
                await asyncio.sleep(0)
            self.assertFalse(any(not task.done() for task in spawned), 'Shutdown must not lose a replacement task')
            await refs.recover()
            for operation in [lambda: refs.create(ReferenceImportRequest(url=self.source.canonical_url)),
                              lambda: refs.prepare(ReferenceTrackPreparationRequest(track_id=1)),
                              lambda: refs.retry(job.id), lambda: self.real_probe(self.source.canonical_url)]:
                with self.assertRaises(refs.ReferenceImportError) as error:
                    await operation()
                self.assertEqual(error.exception.code, 'busy')
            self.assertEqual(refs.get(job.id).status, 'cancelled', 'Result reads remain available')
            refs.capabilities()
            await refs.start()
            with patch.object(refs, '_run', AsyncMock()):
                await refs.retry(job.id)
                await self.finished(job.id)
        finally:
            for task in spawned:
                task.cancel()
            await asyncio.gather(*spawned, return_exceptions=True)

    async def test_descendant_drain_failure_keeps_ownership_after_supervisor_exit(self) -> None:
        job = refs._new(ReferenceImportRequest(url=self.source.canonical_url))
        process = AsyncMock(spec=asyncio.subprocess.Process)
        process.returncode = 0
        refs._jobs[job.id] = refs._OwnedJob(proc=process)
        stored = refs._read(job.id)
        stored.worker = refs._Worker(pid=123, token='a' * 32, receipt_name='receipt-' + 'b' * 32 + '.json')
        refs._write(job.id, stored)
        with patch.object(refs, 'kill_process_tree', AsyncMock(side_effect=OSError('descendants not drained'))):
            with self.assertRaises(OSError):
                await refs._drain_worker(job.id, process)
        self.assertTrue(refs.work_busy())
        self.assertIs(refs._jobs[job.id].proc, process)
        self.assertIsNotNone(refs._read(job.id).worker)
        with self.assertRaises(refs.ReferenceImportError):
            await refs.delete(job.id)
        with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)), patch.object(refs, 'kill_process_tree', AsyncMock(side_effect=OSError('retained job accounting failed'))):
            await refs.recover()
        self.assertTrue(refs.work_busy(), 'Recovery must also drain a retained Process job handle')
        self.assertIs(refs._jobs[job.id].proc, process)
        self.assertIsNotNone(refs._read(job.id).worker)
        with patch.object(refs, 'terminate_verified', AsyncMock(return_value=True)), patch.object(refs, 'kill_process_tree', AsyncMock()):
            await refs.recover()
        self.assertFalse(refs.work_busy())
        self.assertIsNone(refs._jobs[job.id].proc)
        self.assertIsNone(refs._read(job.id).worker)
