from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
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


def _venv(root: Path) -> None:
    _touch(root / '.venv' / 'bin' / 'python', '')


class OptionalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_install_commands_do_not_fetch_weights_or_forbidden_variants(self) -> None:
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

    def test_missing_engine_names_the_setup_script(self) -> None:
        with patch.object(config, 'KOKORO_DIR', self.root / 'kokoro'):
            with self.assertRaises(OptionalEngineError) as caught:
                narrate_kokoro('Hello from Kokoro.')
        self.assertEqual(caught.exception.code, 'engine_not_installed')
        self.assertIn('./setup_kokoro.sh', caught.exception.detail)

    def test_turbo_and_non_5b_wan_are_refused_before_launch(self) -> None:
        with patch('app.optional_engines.launch', side_effect=AssertionError('must not launch')):
            with self.assertRaises(OptionalEngineError) as turbo:
                speak_chatterbox('Hello.', model='turbo')
            self.assertEqual(turbo.exception.code, 'chatterbox_turbo_refused')
            with self.assertRaises(OptionalEngineError) as wan:
                render_wan('A cat', variant='t2v-14b')
            self.assertEqual(wan.exception.code, 'wan_variant_refused')
            with self.assertRaises(OptionalEngineError) as animate:
                render_wan('A cat', variant='animate')
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

    def test_kokoro_happy_path_is_pytorch_and_does_not_download(self) -> None:
        engine, weights = self._kokoro_tree()
        seen: list[list[str]] = []

        def fake_launch(argv, *, cwd, env, timeout):
            seen.append(argv)
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            self.assertEqual(env['PYTORCH_ENABLE_MPS_FALLBACK'], '1')
            request = json.loads(Path(argv[-1]).read_text(encoding='utf-8'))
            self.assertEqual(request['task'], 'kokoro')
            self.assertNotIn('reference', request)
            Path(str(request['output_path'])).write_bytes(b'RIFFxxxxWAVEfmt ')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(config, 'KOKORO_DIR', engine), patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_KOKORO_WEIGHTS': str(weights)}), \
                patch('app.optional_engines.launch', fake_launch):
            result = narrate_kokoro('Hello from OpenFabric.', voice='af_heart', lang='a')
        self.assertEqual(result.status, 'completed')
        self.assertIn('PyTorch', result.runtime)
        self.assertIn('PYTORCH_ENABLE_MPS_FALLBACK', result.runtime)
        self.assertNotIn('MLX', result.runtime.split('not the community MLX port')[0])
        self.assertTrue(result.output_path is not None and result.output_path.is_file())
        self.assertTrue(seen[0][1].endswith('optional_engine_worker.py'))
        self.assertNotIn('turbo', ' '.join(seen[0]).lower())

    def test_wan_and_rvc_happy_paths_check_local_files_only(self) -> None:
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

        def fake_launch(argv, *, cwd, env, timeout):
            seen.append(argv)
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            if '--output-path' in argv:
                Path(argv[argv.index('--output-path') + 1]).write_bytes(b'\x00' * 32)
            else:
                Path(argv[argv.index('--output') + 1]).write_bytes(b'RIFFxxxxWAVEfmt ')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(config, 'WAN22_DIR', wan), patch.object(config, 'RVC_DIR', rvc), \
                patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_WAN22_MODEL_DIR': str(weights), 'OPENFABRIC_VIDEO_ENGINE': 'ltx'}), \
                patch('app.optional_engines.launch', fake_launch):
            self.assertEqual(video_engine_preference(), 'ltx')
            video = render_wan('A quiet harbor at dusk.')
            audio = convert_rvc(str(voice), str(source))
        self.assertEqual(video.status, 'completed')
        self.assertIn('LTX', video.runtime)
        self.assertIn('mlx_video.models.wan_2.generate', seen[0])
        self.assertNotIn('14b', ' '.join(seen[0]).lower())
        self.assertEqual(audio.status, 'completed')
        self.assertIn('CPU', audio.runtime)
        self.assertTrue(str(seen[1][1]).endswith('infer/cli.py'))

    def test_missing_weights_name_the_hand_download(self) -> None:
        engine, _weights = self._kokoro_tree()
        with patch.object(config, 'KOKORO_DIR', engine), patch.object(config, 'DATA_DIR', self.root / 'data'), \
                patch.dict('os.environ', {'OPENFABRIC_KOKORO_WEIGHTS': str(self.root / 'empty')}):
            with self.assertRaises(OptionalEngineError) as caught:
                narrate_kokoro('Hello.')
        self.assertEqual(caught.exception.code, 'weights_missing')
        self.assertIn('kokoro-v1_0.pth', caught.exception.detail)
        self.assertIn('does not download', caught.exception.detail)


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

