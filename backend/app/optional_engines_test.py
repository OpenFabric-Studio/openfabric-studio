from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import asyncio
import os
import sys
import wave
import io
from contextlib import redirect_stdout
from types import ModuleType
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.api.routes_optional_engines import router
from app.optional_engines import (
    OptionalEngineError, convert_rvc, install_command, narrate_kokoro, render_wan,
    speak_chatterbox, video_engine_preference,
)


def _touch(path: Path, text: str = 'x') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _wav(path: Path) -> None:
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\0\0' * 1600)


def _venv(root: Path) -> None:
    _touch(root / '.venv' / 'bin' / 'python', '')


class OptionalEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.enterContext(patch.object(config, 'DATA_DIR', self.root / 'data'))

    async def asyncSetUp(self) -> None:
        from app import optional_engines
        await optional_engines.recover()

    async def asyncTearDown(self) -> None:
        from app import optional_engines
        await optional_engines.shutdown()

    async def test_install_commands_do_not_fetch_weights_or_forbidden_variants(self) -> None:
        for identifier in ('kokoro', 'chatterbox', 'wan22', 'rvc'):
            command = ' '.join(install_command('uv', identifier, Path('/venv/bin/python'), Path('/engine')))
            lowered = command.lower()
            self.assertNotIn('huggingface', lowered)
            self.assertNotIn('from_pretrained', lowered)
            self.assertNotIn('wan2.2-ti2v', lowered)
            self.assertNotIn('turbo', lowered)
            self.assertNotIn('14b', lowered)
        self.assertIn('soundfile', install_command('uv', 'kokoro', Path('/venv/bin/python'), Path('/engine')))
        self.assertNotIn('-e', install_command('uv', 'rvc', Path('/venv/bin/python'), Path('/engine')))

    async def test_missing_engine_names_the_setup_script(self) -> None:
        with patch.object(config, 'KOKORO_DIR', self.root / 'kokoro'):
            with self.assertRaises(OptionalEngineError) as caught:
                await narrate_kokoro('Hello from Kokoro.')
        self.assertEqual(caught.exception.code, 'engine_not_installed')
        self.assertIn('./setup_kokoro.sh', caught.exception.detail)

    async def test_turbo_and_non_5b_wan_are_refused_before_launch(self) -> None:
        with patch('app.optional_engines.launch', side_effect=AssertionError('must not launch')):
            with self.assertRaises(OptionalEngineError) as turbo:
                await speak_chatterbox('Hello.', model='turbo')
            self.assertEqual(turbo.exception.code, 'chatterbox_turbo_refused')
            with self.assertRaises(OptionalEngineError) as wan:
                await render_wan('A cat', variant='t2v-14b')
            self.assertEqual(wan.exception.code, 'wan_variant_refused')
            with self.assertRaises(OptionalEngineError) as animate:
                await render_wan('A cat', variant='animate')
            self.assertEqual(animate.exception.code, 'wan_variant_refused')

    def _kokoro_tree(self) -> tuple[Path, Path]:
        engine = self.root / 'kokoro'
        _touch(engine / 'pyproject.toml')
        _touch(engine / 'kokoro' / 'pipeline.py', 'class KPipeline: pass\n')
        _venv(engine)
        weights = self.root / 'kokoro-weights'
        _touch(weights / 'config.json', '{}')
        _touch(weights / 'kokoro-v1_0.pth')
        _touch(weights / 'voices' / 'af_heart.pt')
        return engine, weights

    async def test_kokoro_happy_path_is_pytorch_and_does_not_download(self) -> None:
        engine, weights = self._kokoro_tree()
        seen: list[list[str]] = []

        async def fake_launch(argv, *, cwd, env, timeout, **options):
            seen.append(argv)
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            self.assertEqual(env['PYTORCH_ENABLE_MPS_FALLBACK'], '1')
            request = json.loads(Path(argv[-1]).read_text(encoding='utf-8'))
            self.assertEqual(request['task'], 'kokoro')
            self.assertNotIn('reference', request)
            _wav(Path(str(request['output_path'])))
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(config, 'KOKORO_DIR', engine), patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_KOKORO_WEIGHTS': str(weights)}), \
                patch('app.optional_engines.launch', fake_launch):
            result = await narrate_kokoro('Hello from OpenFabric.', voice='af_heart', lang='a')
        self.assertEqual(result.status, 'completed')
        self.assertIn('PyTorch', result.runtime)
        self.assertIn('PYTORCH_ENABLE_MPS_FALLBACK', result.runtime)
        self.assertNotIn('MLX', result.runtime.split('not the community MLX port')[0])
        self.assertTrue(result.output_path is not None and result.output_path.is_file())
        self.assertTrue(seen[0][1].endswith('optional_engine_worker.py'))
        self.assertNotIn('turbo', ' '.join(seen[0]).lower())

    async def test_wan_and_rvc_happy_paths_check_local_files_only(self) -> None:
        wan = self.root / 'mlx-video'
        _touch(wan / 'pyproject.toml')
        _touch(wan / 'mlx_video' / 'models' / 'wan_2' / 'generate.py', 'def main(): pass\n')
        _venv(wan)
        weights = self.root / 'wan22-ti2v-5b'
        for name in ('config.json', 'model.safetensors', 't5_encoder.safetensors', 'vae.safetensors'):
            _touch(weights / name)
        rvc = self.root / 'rvc'
        _touch(rvc / 'webui.py')
        _touch(rvc / 'infer' / 'cli.py', 'print("cli")\n')
        _venv(rvc)
        _touch(rvc / 'assets' / 'hubert_base' / 'config.json', '{}')
        _touch(rvc / 'assets' / 'rmvpe' / 'rmvpe.pt')
        voice = self.root / 'singer.pth'
        _touch(voice)
        source = self.root / 'in.wav'
        _touch(source, 'RIFF')
        seen: list[list[str]] = []

        async def fake_launch(argv, *, cwd, env, timeout, **options):
            seen.append(argv)
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            if '--output-path' in argv:
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=0.25', '-r', '24', '-an', argv[argv.index('--output-path') + 1]], check=True)
            else:
                _wav(Path(argv[argv.index('--output') + 1]))
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(config, 'WAN22_DIR', wan), patch.object(config, 'RVC_DIR', rvc), \
                patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_WAN22_MODEL_DIR': str(weights), 'OPENFABRIC_VIDEO_ENGINE': 'ltx'}), \
                patch('app.optional_engines.launch', fake_launch):
            self.assertEqual(video_engine_preference(), 'ltx')
            video = await render_wan('A quiet harbor at dusk.')
            audio = await convert_rvc(str(voice), str(source))
        self.assertEqual(video.status, 'completed')
        self.assertIn('LTX', video.runtime)
        self.assertIn('mlx_video.models.wan_2.generate', seen[0])
        self.assertNotIn('14b', ' '.join(seen[0]).lower())
        self.assertEqual(audio.status, 'completed')
        self.assertIn('CPU', audio.runtime)
        self.assertTrue(str(seen[1][1]).endswith('infer/cli.py'))

    async def test_missing_weights_name_the_hand_download(self) -> None:
        engine, _weights = self._kokoro_tree()
        with patch.object(config, 'KOKORO_DIR', engine), patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_KOKORO_WEIGHTS': str(self.root / 'empty')}):
            with self.assertRaises(OptionalEngineError) as caught:
                await narrate_kokoro('Hello.')
        self.assertEqual(caught.exception.code, 'weights_missing')
        self.assertIn('kokoro-v1_0.pth', caught.exception.detail)
        self.assertIn('does not download', caught.exception.detail)

    async def test_invalid_audio_is_not_reported_completed(self) -> None:
        from app.optional_engines import _run
        output = self.root / 'data' / 'outputs' / 'local-engines' / 'kokoro' / ('ab' * 16 + '.partial.wav')
        output.parent.mkdir(parents=True)
        output.write_bytes(b'not a wave file' * 4)
        with patch('app.optional_engines.launch', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(OptionalEngineError):
                await _run('kokoro', [], cwd=self.root, output=output, timeout=1, runtime='test')

    async def test_worker_internal_errors_are_not_public(self) -> None:
        from app.optional_engines import _run
        output = self.root / 'data' / 'outputs' / 'local-engines' / 'kokoro' / ('ab' * 16 + '.partial.wav')
        output.parent.mkdir(parents=True)
        with patch('app.optional_engines.launch', return_value=subprocess.CompletedProcess([], 1, '', 'SECRET internal path /private/home')):
            with self.assertRaises(OptionalEngineError) as failed:
                await _run('kokoro', [], cwd=self.root, output=output, timeout=1, runtime='test')
        self.assertNotIn('SECRET', failed.exception.detail)


class OptionalEngineRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:9000')

    def test_route_names_setup_script_and_keeps_ltx_default(self) -> None:
        with patch.object(config, 'KOKORO_DIR', Path(self.temporary.name) / 'missing'):
            status = self.client.get('/api/local-engines')
            refused = self.client.post('/api/local-engines/kokoro', json={'text': 'Hello.'})
            turbo = self.client.post('/api/local-engines/chatterbox', json={'text': 'Hello.', 'model': 'turbo'})
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body['video_engine'], 'ltx')
        self.assertEqual(body['video_preference'], 'ltx')
        self.assertIn('Song videos', body['note'])
        self.assertEqual(refused.status_code, 409)
        self.assertIn('setup_kokoro.sh', refused.json()['detail']['detail'])
        self.assertEqual(turbo.status_code, 422)

    def test_success_exposes_a_media_url_and_serves_only_that_file(self) -> None:
        from app.optional_engines import LocalRun
        data = Path(self.temporary.name) / 'data'
        output = data / 'outputs' / 'local-engines' / 'kokoro' / ('ab' * 16 + '.wav')
        output.parent.mkdir(parents=True)
        output.write_bytes(b'RIFF' + b'\0' * 40)
        run = LocalRun('completed', 'kokoro wrote audio.', output, 'runtime')
        with patch('app.api.routes_optional_engines.narrate_kokoro', return_value=run), patch.object(config, 'DATA_DIR', data):
            created = self.client.post('/api/local-engines/kokoro', json={'text': 'Hello.', 'voice': 'af_heart'})
            played = self.client.get(created.json()['media_url'])
            missing = self.client.get('/api/local-engines/kokoro/media/' + 'cd' * 16)
            escaped = self.client.get('/api/local-engines/kokoro/media/' + '..')
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()['media_url'], f'/api/local-engines/kokoro/media/{output.stem}')
        self.assertEqual(played.status_code, 200)
        self.assertTrue(played.headers['content-type'].startswith('audio/wav'))
        self.assertEqual(missing.status_code, 404)
        self.assertNotEqual(escaped.status_code, 200)
        self.assertNotIn(b'RIFF', escaped.content)

    def test_browser_pick_is_staged_and_does_not_download_weights(self) -> None:
        data = Path(self.temporary.name) / 'data'
        with patch.object(config, 'DATA_DIR', data):
            saved = self.client.post('/api/local-engines/inputs', files={'file': ('clip.wav', b'RIFFclip-bytes!!', 'audio/wav')})
            refused = self.client.post('/api/local-engines/inputs', files={'file': ('weights.bin', b'model-weights-here', 'application/octet-stream')})
        self.assertEqual(saved.status_code, 200)
        path = Path(saved.json()['path'])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, '.wav')
        self.assertEqual(path.parent.name, 'inputs')
        self.assertNotIn('huggingface', saved.text.lower())
        self.assertEqual(refused.status_code, 400)
        self.assertIn('wav', refused.json()['detail']['detail'])

    def test_media_and_staging_refuse_external_directory_symlinks(self) -> None:
        data = Path(self.temporary.name) / 'data'
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (outside / ('ab' * 16 + '.wav')).write_bytes(b'secret file')
        root = data / 'outputs' / 'local-engines'
        root.mkdir(parents=True)
        (root / 'kokoro').symlink_to(outside, target_is_directory=True)
        (root / 'inputs').symlink_to(outside, target_is_directory=True)
        with patch.object(config, 'DATA_DIR', data):
            media = self.client.get('/api/local-engines/kokoro/media/' + 'ab' * 16)
            staged = self.client.post('/api/local-engines/inputs', files={'file': ('clip.wav', b'RIFF' + b'x' * 32, 'audio/wav')})
        self.assertEqual(media.status_code, 404)
        self.assertGreaterEqual(staged.status_code, 400)
        self.assertEqual(len(list(outside.iterdir())), 1)

    def test_upload_limit_rejects_before_multipart_is_spooled(self) -> None:
        from app.api import routes_optional_engines as routes
        from starlette.formparsers import MultiPartParser
        with patch.object(routes, '_MAX_INPUT_BYTES', 16), patch.object(MultiPartParser, 'parse', side_effect=AssertionError('must reject before parsing')):
            response = self.client.post('/api/local-engines/inputs', files={'file': ('clip.wav', b'x' * (2 * 1024 * 1024), 'audio/wav')})
        self.assertEqual(response.status_code, 413)


class OptionalWorkerBoundaryTests(unittest.TestCase):
    def test_worker_request_rejects_wrong_field_types_before_engine_imports(self) -> None:
        from scripts import optional_engine_worker as worker
        self.assertTrue(hasattr(worker, '_parse_request'))
        with self.assertRaises(ValueError):
            worker._parse_request({'task': 'kokoro', 'text': ['untrusted']})

    def test_worker_request_rejects_unsupported_tasks(self) -> None:
        from scripts import optional_engine_worker as worker
        self.assertTrue(hasattr(worker, '_parse_request'))
        with self.assertRaises(ValueError):
            worker._parse_request({'task': 'turbo'})


class OptionalEngineLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import optional_engines
        self.engines = optional_engines
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, 'DATA_DIR', self.root / 'data'))
        if hasattr(self.engines, 'recover'):
            await self.engines.recover()

    async def asyncTearDown(self) -> None:
        if hasattr(self.engines, 'shutdown'):
            await self.engines.shutdown()

    async def test_both_chatterbox_workers_write_portable_pcm16_that_validates(self) -> None:
        import numpy as np
        import soundfile as sf
        from numpy.typing import NDArray
        from scripts import optional_engine_worker as worker

        class FakeChatterbox:
            sr: int = 24000

            @staticmethod
            def from_local(weights: str, device: str, *, t3_model: str = '') -> FakeChatterbox:
                return FakeChatterbox()

            def generate(self, text: str, *, audio_prompt_path: str | None = None,
                         language_id: str = 'en') -> NDArray[np.float32]:
                return np.full((1, 2400), .125, dtype=np.float32)

        settings: list[tuple[str | None, int | None]] = []

        def save(path: str, audio: NDArray[np.float32], sample_rate: int, *,
                 encoding: str | None = None, bits_per_sample: int | None = None) -> None:
            settings.append((encoding, bits_per_sample))
            # Match the verified TorchAudio SoundFile default for float audio:
            # unspecified encoding writes IEEE FLOAT, which is not PCM WAV.
            subtype = 'PCM_16' if encoding == 'PCM_S' and bits_per_sample == 16 else 'FLOAT'
            sf.write(path, audio.T, sample_rate, subtype=subtype)

        package = ModuleType('chatterbox')
        original = ModuleType('chatterbox.tts')
        multilingual = ModuleType('chatterbox.mtl_tts')
        torchaudio = ModuleType('torchaudio')
        setattr(original, 'ChatterboxTTS', FakeChatterbox)
        setattr(multilingual, 'ChatterboxMultilingualTTS', FakeChatterbox)
        setattr(torchaudio, 'save', save)
        modules = {'chatterbox': package, 'chatterbox.tts': original,
                   'chatterbox.mtl_tts': multilingual, 'torchaudio': torchaudio}
        with patch.dict(sys.modules, modules), patch.object(worker, '_device', return_value='cpu'), redirect_stdout(io.StringIO()):
            for model in ('original', 'multilingual'):
                with self.subTest(model=model):
                    output = self.root / f'{model}.wav'
                    worker._chatterbox(worker.ChatterboxJob(text='Hello.', model=model, weights=str(self.root),
                        t3_model='', language_id='en', audio_prompt_path='', output_path=str(output)))
                    self.assertEqual(settings[-1], ('PCM_S', 16))
                    self.assertEqual(sf.info(output).subtype, 'PCM_16')
                    await self.engines._validate_output(output)
                    with wave.open(str(output), 'rb') as pcm:
                        self.assertEqual(pcm.getsampwidth(), 2)
                        self.assertEqual(pcm.getnframes(), 2400)

    async def test_launch_timeout_drains_descendants(self) -> None:
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(self.engines.launch))
        marker = self.root / 'pid'
        code = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)"
        with self.assertRaises((TimeoutError, subprocess.TimeoutExpired)):
            await self.engines.launch([sys.executable, '-c', code, str(marker)], cwd=self.root, env=dict(os.environ), timeout=.5)
        pid = int(marker.read_text())
        for _ in range(100):
            status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
            if not status or status.startswith('Z'):
                break
            await asyncio.sleep(.01)
        self.assertTrue(not status or status.startswith('Z'))

    async def start_sleeping_run(self) -> tuple[asyncio.Task[object], Path]:
        output = self.engines._output('kokoro', '.wav')
        marker = self.root / 'started'
        code = "import sys,time;from pathlib import Path;Path(sys.argv[1]).write_text('started');time.sleep(30)"
        task = asyncio.create_task(self.engines._run('kokoro', [sys.executable, '-c', code, str(marker)],
            cwd=self.root, output=output, timeout=60, runtime='test'))
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(.01)
        return task, output

    async def test_shutdown_drains_registered_run_and_persists_cancellation(self) -> None:
        task, output = await self.start_sleeping_run()
        self.assertTrue(self.engines.work_busy())
        await self.engines.shutdown()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.engines.work_busy())
        record = self.engines.StoredRun.model_validate_json(self.engines._record_path(output.name[:32]).read_bytes())
        self.assertEqual(record.status, 'cancelled')
        self.assertIsNone(record.worker)

    async def test_second_run_is_refused_while_first_is_owned(self) -> None:
        task, _output = await self.start_sleeping_run()
        try:
            with self.assertRaises(OptionalEngineError) as busy:
                await self.engines._run('kokoro', [], cwd=self.root, output=self.engines._output('kokoro', '.wav'), timeout=1, runtime='test')
            self.assertEqual(busy.exception.code, 'engine_busy')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.engines.work_busy())

    async def test_recovery_preserves_completed_file_after_interrupted_metadata_write(self) -> None:
        partial = self.engines._output('kokoro', '.wav')
        final = partial.with_name(partial.name.replace('.partial', ''))
        _wav(final)
        self.engines._save_run(self.engines.StoredRun(id=final.stem, engine='kokoro', status='running', output_path=str(final)))
        await self.engines.recover()
        record = self.engines.StoredRun.model_validate_json(self.engines._record_path(final.stem).read_bytes())
        self.assertEqual(record.status, 'completed')
        self.assertTrue(final.is_file())
