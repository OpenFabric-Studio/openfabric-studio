"""Clip-only tunnel ownership with synthetic HTTP and isolated local files."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import sys
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from app import clip_host_settings as settings, clip_hosting as hosting
from app.api.routes_clip_host import router
from app.clip_host_contracts import ClipHostSettingsRequest, HostedClip


class ClipHostingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ,{'OPENFABRIC_CONFIG':str(self.library/'config.json'),
            'OPENFABRIC_DATA_DIR':str(self.library),'SEED_VC_DIR':str(self.library/'seed'),
            'OPENFABRIC_MODULE_ROOT':str(self.library/'modules')}))
        for module in (settings,hosting): self.enterContext(patch.object(module,'DATA_DIR',self.library))
        self.enterContext(patch.object(hosting,'_closing',False))
        self.enterContext(patch.object(hosting,'_unresolved',False))
        self.real_binary=hosting._binary
        self.enterContext(patch.object(hosting,'_binary',return_value='/synthetic/cloudflared'))
        self.addAsyncCleanup(hosting.shutdown)
        self.receipt='a'*32; self.media=b'prepared silent clip'
        self.source=self.library/'input.mp4'; self.source.write_bytes(self.media)
        self.digest=hashlib.sha256(self.media).hexdigest()
        self.calls: list[httpx.Request]=[]
        self.real_start=hosting._start_tunnel
        settings.save(ClipHostSettingsRequest(enabled=True))
        async def start() -> str:
            hosting._origin='https://synthetic-test.trycloudflare.com'; return hosting._origin
        self.enterContext(patch.object(hosting,'_start_tunnel',side_effect=start))
        self.enterContext(patch.object(hosting.client,'transport',httpx.MockTransport(self.serve)))

    def serve(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        self.assertNotIn('authorization',request.headers); self.assertNotIn('apikey',request.headers)
        self.assertEqual(request.url.host,'synthetic-test.trycloudflare.com')
        return httpx.Response(200,headers={'content-type':'video/mp4','content-length':str(len(self.media)),'cache-control':'private, no-store, max-age=0'},
            content=b'' if request.method=='HEAD' else self.media)

    async def stage(self, identifier: str | None=None) -> HostedClip:
        return await hosting.stage_clip(identifier or self.receipt,self.source,self.digest)

    async def relay(self, url: str, *, method: str='GET', headers: dict[str,str] | None=None) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hosting.relay_app),base_url='http://127.0.0.1') as client:
            return await client.request(method,httpx.URL(url).path,headers=headers)

    async def test_owned_copy_verified_head_get_and_no_saved_hostname(self) -> None:
        hosted=await self.stage(); folder=self.library/'providers'/'clip-assets'/self.receipt
        self.assertEqual(hosted.source_sha256,self.digest)
        self.assertGreater(hosted.expires_at,datetime.now(timezone.utc)+timedelta(hours=23))
        self.assertEqual([call.method for call in self.calls],['HEAD','GET'])
        self.assertEqual((folder/'clip.mp4').read_bytes(),self.media)
        self.assertNotIn('trycloudflare',(folder/'grant.json').read_text())
        self.source.unlink(); self.assertEqual((await self.relay(hosted.url)).content,self.media)
        self.assertEqual(hosting.status().active_clips,1)

    async def test_only_get_head_token_route_and_ranges(self) -> None:
        hosted=await self.stage()
        for route in ('/','/docs','/openapi.json','/api/clip-host/settings','/api/videos','/clip/'+'0'*64+'.mp4','/clip/../input.mp4'):
            self.assertEqual((await self.relay(route)).status_code,404)
        for method in ('POST','PUT','DELETE','PATCH','OPTIONS'):
            self.assertIn((await self.relay(hosted.url,method=method)).status_code,{404,405})
        head=await self.relay(hosted.url,method='HEAD')
        self.assertEqual((head.status_code,head.content,head.headers['content-length']),(200,b'',str(len(self.media))))
        partial=await self.relay(hosted.url,headers={'Range':'bytes=2-5'})
        self.assertEqual((partial.status_code,partial.content),(206,self.media[2:6]))
        self.assertEqual(partial.headers['cache-control'],'private, no-store, max-age=0')
        multipart=await self.relay(hosted.url,headers={'Range':'bytes=0-1,5-6'})
        self.assertEqual(multipart.status_code,206)
        self.assertEqual(int(multipart.headers['content-length']),len(multipart.content))

    async def test_external_redirect_or_wrong_media_never_published_or_retried(self) -> None:
        for index,mode in enumerate(('redirect','wrong_bytes','wrong_type','wrong_length')):
            calls: list[httpx.Request]=[]
            def invalid(request: httpx.Request) -> httpx.Response:
                calls.append(request)
                if mode=='redirect': return httpx.Response(302,headers={'location':'https://outside.test/file'})
                headers={'content-type':'text/html' if mode=='wrong_type' else 'video/mp4',
                    'content-length':'99999' if mode=='wrong_length' else str(len(self.media))}
                return httpx.Response(200,headers=headers,content=b'x'*len(self.media) if mode=='wrong_bytes' else self.media)
            with patch.object(hosting.client,'transport',httpx.MockTransport(invalid)):
                with self.assertRaises(hosting.ClipHostError): await self.stage(f'{index:032x}')
            self.assertLessEqual(len(calls),2)
            self.assertTrue(all(call.url.host=='synthetic-test.trycloudflare.com' for call in calls))
            self.assertEqual(hosting.status().active_clips,0)

    async def test_source_hash_symlink_and_size_checked_before_publication(self) -> None:
        link=self.library/'link.mp4'; link.symlink_to(self.source)
        for source,digest in ((self.source,'0'*64),(self.library/'missing',self.digest),(link,self.digest)):
            with self.assertRaises(hosting.ClipHostError): await hosting.stage_clip(self.receipt,source,digest)
        with patch.object(hosting,'MAX_CLIP_BYTES',2):
            with self.assertRaises(hosting.ClipHostError): await self.stage()
        self.assertEqual(self.calls,[]); self.assertFalse(hosting._served)

    async def test_clip_symlink_does_not_serve_or_delete_its_target(self) -> None:
        hosted=await self.stage(); path=self.library/'providers'/'clip-assets'/self.receipt/'clip.mp4'
        path.unlink(); path.symlink_to(self.source)
        self.assertEqual((await self.relay(hosted.url)).status_code,404)
        await hosting.release(self.receipt); self.assertEqual(self.source.read_bytes(),self.media)

    async def test_file_switch_after_hash_cannot_serve_unapproved_bytes(self) -> None:
        hosted=await self.stage(); real_hash=hosting.hash_file
        secret=self.library/'private-other-file'; secret.write_bytes(b'private unrelated content')
        async def switch(path: Path) -> str:
            digest=await real_hash(path)
            path.unlink(); path.symlink_to(secret)
            return digest
        with patch.object(hosting,'hash_file',side_effect=switch):
            response=await self.relay(hosted.url)
        self.assertEqual(response.status_code,404)
        self.assertNotIn(b'private unrelated content',response.content)

    async def test_release_revokes_one_token_preserves_original_and_siblings(self) -> None:
        first=await self.stage(); second=await self.stage('b'*32)
        await hosting.release(self.receipt)
        self.assertEqual((await self.relay(first.url)).status_code,404)
        self.assertEqual((await self.relay(second.url)).content,self.media)
        self.assertEqual(self.source.read_bytes(),self.media)
        await hosting.release('b'*32); self.assertFalse(hosting.status().tunnel_active)

    async def test_disabled_settings_keep_existing_accepted_input(self) -> None:
        hosted=await self.stage(); settings.save(ClipHostSettingsRequest(enabled=False))
        with self.assertRaises(hosting.ClipHostError): await self.stage('b'*32)
        self.assertFalse(hosting.status().configured)
        self.assertEqual((await self.relay(hosted.url)).content,self.media)

    async def test_restart_never_reexposes_old_tokens_or_reuploads(self) -> None:
        hosted=await self.stage(); await hosting.shutdown(); await hosting.recover()
        self.assertEqual(hosting.status().active_clips,1)
        self.assertFalse(hosting.status().tunnel_active)
        self.assertEqual((await self.relay(hosted.url)).status_code,404)
        with self.assertRaises(hosting.ClipHostError): await self.stage()

    async def test_expiry_denies_access_before_cleanup(self) -> None:
        hosted=await self.stage(); path=self.library/'providers'/'clip-assets'/self.receipt/'grant.json'
        document=json.loads(path.read_text())
        timestamp=datetime.now(timezone.utc)
        document['created_at']=(timestamp-timedelta(days=2)).isoformat()
        document['expires_at']=(timestamp-timedelta(days=1)).isoformat()
        path.write_text(json.dumps(document))
        self.assertEqual((await self.relay(hosted.url)).status_code,404)
        await hosting.cleanup(); self.assertEqual(hosting.status().active_clips,0)

    async def test_cancelled_verification_drains_and_revokes_unsubmitted_copy(self) -> None:
        started=asyncio.Event()
        async def waiting(request: httpx.Request) -> httpx.Response:
            started.set(); await asyncio.Event().wait(); return httpx.Response(500)
        with patch.object(hosting.client,'transport',httpx.MockTransport(waiting)):
            task=asyncio.create_task(self.stage()); await asyncio.wait_for(started.wait(),2); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(hosting.work_busy()); self.assertEqual(hosting.status().active_clips,0)

    async def test_corrupt_grant_and_redirected_folder_preserved(self) -> None:
        folder=self.library/'providers'/'clip-assets'/self.receipt; folder.mkdir(parents=True)
        (folder/'grant.json').write_bytes(b'private corrupt evidence')
        with self.assertRaises(hosting.ClipHostError): await hosting.cleanup()
        self.assertEqual((folder/'grant.json').read_bytes(),b'private corrupt evidence')
        folder.rename(self.library/'outside'); folder.symlink_to(self.library/'outside',target_is_directory=True)
        await hosting.recover(); self.assertTrue(hosting.work_busy())
        self.assertEqual((self.library/'outside'/'grant.json').read_bytes(),b'private corrupt evidence')

    async def test_connection_is_local_only_and_requires_installed_binary(self) -> None:
        self.assertTrue((await hosting.connection()).configured); self.assertEqual(self.calls,[])
        self.assertFalse((self.library/'providers'/'clip-assets').exists())
        with patch.object(hosting,'_binary',return_value=None):
            with self.assertRaises(hosting.ClipHostError) as failure: await hosting.ensure_ready()
        self.assertEqual(failure.exception.code,'clip_host_binary_missing')

    async def test_api_has_origin_guard_and_sanitized_validation(self) -> None:
        app=FastAPI(); app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:9000') as client:
            response=await client.put('/api/clip-host/settings',json={'enabled':'private rejected value'})
            self.assertEqual(response.status_code,422); self.assertNotIn('private rejected value',response.text)
            response=await client.put('/api/clip-host/settings',json={'enabled':False},headers={'Origin':'https://outside.test'})
            self.assertEqual(response.status_code,403)
            self.assertEqual((await client.post('/api/clip-host/connection')).status_code,200)
            self.assertEqual((await client.post('/api/clip-host/cleanup')).json()['active_clips'],0)

    async def test_hosted_contract_rejects_foreign_host_query_or_app_route(self) -> None:
        value={'receipt_id':self.receipt,'source_sha256':self.digest,'expires_at':datetime.now(timezone.utc)+timedelta(hours=24)}
        valid='https://synthetic-test.trycloudflare.com/clip/'+'b'*64+'.mp4'
        HostedClip(**value,url=valid)
        for url in ('https://outside.test/clip.mp4',valid+'?token=secret',valid.replace('/clip/','/api/'),valid.replace('https://','http://')):
            with self.assertRaises(ValidationError): HostedClip(**value,url=url)

    async def test_interrupted_cleanup_retains_marker_and_retries(self) -> None:
        await self.stage(); unlink=Path.unlink
        def fail(path: Path, missing_ok: bool=False) -> None:
            if path.name=='clip.mp4': raise OSError('synthetic failure')
            unlink(path,missing_ok=missing_ok)
        with patch.object(Path,'unlink',new=fail):
            with self.assertRaises(hosting.ClipHostError): await hosting.release(self.receipt)
        self.assertTrue((self.library/'providers'/'clip-assets'/self.receipt/'grant.json').exists())
        await hosting.cleanup(); self.assertEqual(hosting.status().active_clips,0)

    async def test_foreign_worker_receipt_cannot_be_followed(self) -> None:
        root=self.library/'providers'/'clip-assets'; root.mkdir(parents=True)
        (root/'worker.json').write_text(json.dumps({'pid':123,'token':'a'*32,'receipt':'/outside/receipt.json'}))
        with patch.object(hosting,'terminate_verified',return_value=True) as terminate: await hosting.recover()
        terminate.assert_not_called(); self.assertTrue(hosting.work_busy())

    async def test_all_relay_errors_have_private_no_cache_headers(self) -> None:
        hosted=await self.stage()
        for route,method in (('/docs','GET'),('/clip/'+'0'*64+'.mp4','HEAD'),(hosted.url,'POST')):
            response=await self.relay(route,method=method)
            self.assertEqual(response.headers.get('cache-control'),'private, no-store, max-age=0')

    async def test_external_delivery_requires_no_store_and_rejects_positive_cache(self) -> None:
        for index,cache in enumerate(('', 'public, max-age=60','no-store, max-age=60')):
            def cached(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200,headers={'content-type':'video/mp4','content-length':str(len(self.media)),
                    'cache-control':cache},content=self.media)
            with patch.object(hosting.client,'transport',httpx.MockTransport(cached)):
                with self.assertRaises(hosting.ClipHostError): await self.stage(f'{index:032x}')

    async def test_issued_url_can_retry_only_read_only_edge_readiness(self) -> None:
        requests: list[str]=[]
        def propagation(request: httpx.Request) -> httpx.Response:
            requests.append(request.method)
            if len(requests)==1: return httpx.Response(502)
            return self.serve(request)
        with patch.object(hosting.client,'transport',httpx.MockTransport(propagation)), patch.object(hosting,'_VERIFY_BACKOFF',0.0,create=True):
            hosted=await self.stage()
        self.assertEqual(requests,['HEAD','HEAD','GET'])
        self.assertIn('trycloudflare',hosted.url)

    async def test_transient_dns_can_outlast_four_read_attempts_without_restart(self) -> None:
        requests: list[str]=[]
        def dns_propagation(request: httpx.Request) -> httpx.Response:
            requests.append(request.method)
            if len(requests)<=5: raise httpx.ConnectError('synthetic negative DNS cache',request=request)
            return self.serve(request)
        with patch.object(hosting.client,'transport',httpx.MockTransport(dns_propagation)), patch.object(hosting,'_VERIFY_BACKOFF',0.0,create=True), patch.object(hosting,'_VERIFY_TIMEOUT',0.5,create=True):
            hosted=await self.stage()
        self.assertEqual(requests,['HEAD']*6+['GET'])
        self.assertIn('trycloudflare',hosted.url)

    async def test_unavailable_edge_hits_overall_deadline_and_cleans_unsubmitted_copy(self) -> None:
        calls: list[str]=[]
        def broken(request: httpx.Request) -> httpx.Response:
            calls.append(request.method); raise httpx.ConnectError('synthetic unavailable DNS',request=request)
        with patch.object(hosting.client,'transport',httpx.MockTransport(broken)), patch.object(hosting,'_VERIFY_BACKOFF',0.0,create=True), patch.object(hosting,'_VERIFY_TIMEOUT',0.03,create=True):
            with self.assertRaises(hosting.ClipHostError) as failure: await self.stage()
        self.assertEqual(failure.exception.code,'clip_host_unavailable')
        self.assertGreater(len(calls),4)
        self.assertFalse(hosting.work_busy()); self.assertEqual(hosting.status().active_clips,0)

    async def test_empty_folder_after_cleanup_crash_is_recoverable(self) -> None:
        folder=self.library/'providers'/'clip-assets'/self.receipt; folder.mkdir(parents=True)
        await hosting.recover()
        self.assertFalse(folder.exists()); self.assertFalse(hosting.work_busy())

    async def test_httpx_logs_do_not_record_secret_clip_url(self) -> None:
        with self.assertLogs('httpx',level='INFO') as logged: hosted=await self.stage()
        token=httpx.URL(hosted.url).path.split('/')[-1].removesuffix('.mp4')
        self.assertNotIn(token,str(logged.output))

    async def test_missing_process_handle_never_erases_unresolved_worker_evidence(self) -> None:
        root=self.library/'providers'/'clip-assets'; root.mkdir(parents=True)
        ownership=root/'worker.json'
        ownership.write_text(json.dumps({'pid':123,'token':'a'*32,'receipt':'worker-receipt.json'}))
        listener=socket.socket()
        server=hosting._RelayServer(uvicorn.Config(hosting.relay_app,log_config=None))
        task=asyncio.create_task(asyncio.sleep(0))
        hosting._relay=hosting._Relay(server,task,listener)
        try:
            with patch.object(hosting,'terminate_verified',return_value=False):
                with self.assertRaises(hosting.ClipHostError): await hosting._stop_tunnel()
            self.assertTrue(ownership.exists()); self.assertTrue(hosting.work_busy())
        finally:
            hosting._relay=None; listener.close(); await task

    async def test_real_embedded_server_and_owned_worker_drain_without_signal_changes(self) -> None:
        real_spawn=hosting.spawn_owned; children: list[asyncio.subprocess.Process]=[]; commands: list[list[str]]=[]
        before=(signal.getsignal(signal.SIGINT),signal.getsignal(signal.SIGTERM))
        async def fake_cloudflared(argv: list[str], *, receipt_path: Path, env: dict[str,str], stdout: int,
                on_identity: Callable[[hosting.WorkerIdentity],None]) -> asyncio.subprocess.Process:
            commands.append(argv)
            self.assertFalse(any(name.startswith('TUNNEL_') for name in env))
            self.assertNotIn('OPENROUTER_API_KEY',env)
            self.assertEqual((receipt_path.parent/'relay.yaml').read_bytes(),b'{}\n')
            script='import time; print("https://unapproved.trycloudflare.com.evil.test",flush=True); print("x"*20000,flush=True); print("https://synthetic-test.trycloudflare.com",flush=True); time.sleep(3600)'
            proc=await real_spawn([sys.executable,'-c',script],receipt_path=receipt_path,env=env,stdout=stdout,on_identity=on_identity)
            children.append(proc); return proc
        with patch.object(hosting,'_start_tunnel',side_effect=self.real_start), patch.object(hosting,'spawn_owned',side_effect=fake_cloudflared), patch.dict(os.environ,{'TUNNEL_NAME':'unsafe','NO_TLS_VERIFY':'1','OPENROUTER_API_KEY':'private'}):
            hosted=await self.stage()
            self.assertEqual(before,(signal.getsignal(signal.SIGINT),signal.getsignal(signal.SIGTERM)))
            relay=hosting._relay; self.assertIsNotNone(relay)
            if relay is None: self.fail('Missing relay owner')
            port=relay.listener.getsockname()[1]
            self.assertNotEqual(port,9000)
            async with httpx.AsyncClient(trust_env=False) as client:
                response=await client.get(f'http://127.0.0.1:{port}'+httpx.URL(hosted.url).path)
                self.assertEqual(response.content,self.media)
                self.assertEqual((await client.get(f'http://127.0.0.1:{port}/api/videos')).status_code,404)
            self.assertIn('--management-diagnostics=false',commands[0])
            await hosting.release(self.receipt)
            self.assertIsNotNone(children[0].returncode)
            self.assertTrue(relay.task.done())
            self.assertFalse((self.library/'providers'/'clip-assets'/'worker.json').exists())

    async def test_mac_finder_path_discovers_known_brew_binary_without_execution(self) -> None:
        binary=self.library/'bin'/'cloudflared'; binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o700)
        with patch.object(hosting.shutil,'which',return_value=None), patch.object(sys,'platform','darwin'), patch.object(hosting,'_MAC_BINARY_PATHS',(binary,),create=True), patch.dict(os.environ,{'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'}):
            self.assertEqual(self.real_binary(),str(binary.resolve()))

    async def test_cancelled_real_tunnel_start_drains_worker_and_server(self) -> None:
        real_spawn=hosting.spawn_owned; started=asyncio.Event(); children: list[asyncio.subprocess.Process]=[]
        async def waiting_cloudflared(argv: list[str], *, receipt_path: Path, env: dict[str,str], stdout: int,
                on_identity: Callable[[hosting.WorkerIdentity],None]) -> asyncio.subprocess.Process:
            proc=await real_spawn([sys.executable,'-c','import time; time.sleep(3600)'],receipt_path=receipt_path,env=env,stdout=stdout,on_identity=on_identity)
            children.append(proc); started.set(); return proc
        with patch.object(hosting,'_start_tunnel',side_effect=self.real_start), patch.object(hosting,'spawn_owned',side_effect=waiting_cloudflared):
            task=asyncio.create_task(self.stage()); await asyncio.wait_for(started.wait(),2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNotNone(children[0].returncode)
        self.assertFalse(hosting.work_busy()); self.assertIsNone(hosting._relay)
        self.assertFalse((self.library/'providers'/'clip-assets'/'worker.json').exists())
