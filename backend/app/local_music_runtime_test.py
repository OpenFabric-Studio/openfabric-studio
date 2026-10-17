from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from app import local_music_runtime as runtime
from app.local_music_contracts import LocalMusicError, LocalMusicRequest
from app.module_catalog import ModuleEnvironment
from scripts.local_music_worker import WorkerCommand, read_command, run as run_worker


class LocalMusicRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')

    def weights(self, engine: str, variant: str = 'small-music') -> None:
        directory = self.env.paths['stable_audio3']
        names = ('optimized/mlx/models/mlx/t5gemma_f16.npz',
            'optimized/mlx/models/mlx/dit_sm-music_f16.npz' if variant == 'small-music' else 'optimized/mlx/models/mlx/dit_medium_f16.npz',
            'optimized/mlx/models/mlx/same_s_decoder_f32.npz' if variant == 'small-music' else 'optimized/mlx/models/mlx/same_l_decoder_f32.npz')
        for name in names:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'local weights')

    def test_status_never_installs_or_downloads_and_requires_verified_environment(self) -> None:
        result = runtime.engine_status(self.env)
        self.assertEqual([item.id for item in result.engines], ['stable_audio3'])
        self.assertFalse(any(item.installed or item.weights_ready for item in result.engines))
        self.assertEqual(list(self.root.iterdir()), [])
        self.weights('stable_audio3')
        with patch.object(runtime, 'environment_verified', return_value=False):
            stable = runtime.engine_status(self.env).engines[0]
        self.assertTrue(stable.weights_ready)
        self.assertFalse(stable.installed)

    def test_selected_stable_variant_must_have_all_its_weights(self) -> None:
        self.weights('stable_audio3')
        request = LocalMusicRequest(engine='stable_audio3', prompt='piano', variant='medium', duration_seconds=30)
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True):
            with self.assertRaises(LocalMusicError) as caught:
                runtime.prepare_worker(request, self.root / 'job/output.wav')
        self.assertEqual(caught.exception.code, 'local_music_weights_missing')

    def test_stable_status_reports_each_generation_variant_separately(self) -> None:
        self.weights('stable_audio3')
        with patch.object(runtime, 'environment_verified', return_value=True):
            status = runtime.engine_status(self.env).engines[0]
        self.assertTrue(status.weights_ready)
        self.assertTrue(status.model_dump().get('small_weights_ready'))
        self.assertFalse(status.model_dump().get('medium_weights_ready'))
        from app.stable_lora_runtime import training_weights
        for path in training_weights(self.env, 'medium'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'training weights')
        with patch.object(runtime, 'environment_verified', return_value=True):
            self.assertFalse(runtime.engine_status(self.env).engines[0].model_dump().get('medium_weights_ready'))
        self.weights('stable_audio3', 'medium')
        with patch.object(runtime, 'environment_verified', return_value=True):
            self.assertTrue(runtime.engine_status(self.env).engines[0].model_dump().get('medium_weights_ready'))


    def test_command_uses_offline_env_real_cli_flags_and_keeps_text_out_of_process_arguments(self) -> None:
        self.weights('stable_audio3')
        output = self.root / 'job/output.wav'
        output.parent.mkdir()
        request = LocalMusicRequest(engine='stable_audio3', prompt='private song prompt', duration_seconds=30, seed=42)
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True):
            argv, cwd, env = runtime.prepare_worker(request, output)
        self.assertNotIn(request.prompt, argv)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['TRANSFORMERS_OFFLINE'], '1')
        self.assertEqual(env.get('PYTHONUNBUFFERED'), '1')
        payload = json.loads((output.parent / 'request.json').read_text())
        self.assertIn('--dit', payload['argv'])
        self.assertIn('sm-music', payload['argv'])
        self.assertIn('--seed', payload['argv'])
        self.assertEqual(cwd, self.env.paths['stable_audio3'] / 'optimized/mlx')

    def test_stable_lora_resolves_only_managed_matching_variant(self) -> None:
        self.weights('stable_audio3')
        output = self.root / 'job/output.wav'; output.parent.mkdir()
        request = LocalMusicRequest(engine='stable_audio3', prompt='Piano', duration_seconds=30,
            adapter={'id': 'a' * 32, 'strength': 0.7})
        adapter = self.root / 'managed.safetensors'
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True), patch('app.music_lora.resolve', return_value=adapter) as resolve:
            runtime.prepare_worker(request, output)
        resolve.assert_called_once_with(request.adapter, engine='stable_audio3', target='small-music')
        command = json.loads((output.parent / 'request.json').read_text())['argv']
        self.assertEqual(command[command.index('--lora') + 1:][-2:], [str(adapter), 'strength=0.7'])


    def test_worker_runs_native_cli_then_normalizes_pcm_without_model_packages(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        request = self.root / 'request.json'
        request.write_text(json.dumps({'argv': [str(script), str(raw)], 'cwd': str(root), 'raw_output': str(raw), 'output': str(output), 'ffmpeg': runtime.tool('ffmpeg'), 'seed': None}))
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)
        with wave.open(str(output)) as audio:
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getnframes(), 800)
        self.assertFalse(raw.exists())

    def test_worker_does_not_follow_a_normalization_symlink(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        original = self.root / 'original.wav'
        original.write_bytes(b'keep this file')
        (self.root / 'output.partial.wav').symlink_to(original)
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        request = self.root / 'request.json'
        request.write_text(json.dumps({'argv': [str(script), str(raw)], 'cwd': str(root), 'raw_output': str(raw), 'output': str(output), 'ffmpeg': runtime.tool('ffmpeg'), 'seed': None}))
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(original.read_bytes(), b'keep this file')

    def test_completed_audio_survives_a_raw_cleanup_error(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        command = WorkerCommand([str(script), str(raw)], root, raw, output, runtime.tool('ffmpeg'))
        original_cwd, original_argv, original_path = Path.cwd(), sys.argv.copy(), sys.path.copy()
        try:
            with patch.object(Path, 'unlink', side_effect=PermissionError('raw file locked')):
                run_worker(command)
            self.assertTrue(output.is_file())
        finally:
            os.chdir(original_cwd)
            sys.argv = original_argv
            sys.path[:] = original_path

    def test_worker_rejects_malformed_or_unbounded_launch_spec(self) -> None:
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        request = self.root / 'request.json'
        request.write_text('{"argv": [123], "seed": true}')
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'output.wav').exists())

    def test_conditioning_cannot_silently_use_the_text_only_adapter(self) -> None:
        self.weights('stable_audio3')
        output = self.root / 'job/output.wav'; output.parent.mkdir()
        request = LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Piano',
            'duration_seconds': 5, 'conditioning': {'mode': 'audio_to_audio', 'source': {
                'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32}, 'source_sha256': 'b' * 64, 'noise_level': .6}})
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True):
            with self.assertRaises(LocalMusicError) as error:
                runtime.prepare_worker(request, output)
        self.assertEqual(error.exception.code, 'conditioning_snapshot_invalid')

    def test_conditioned_worker_rejects_missing_source_flag_or_changed_copy(self) -> None:
        source = self.root / 'conditioning.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(44100)
            audio.writeframes(b'\0\0\0\0' * 220500)
        script = self.root / 'generate.py'; script.write_text('pass\n')
        request = self.root / 'request.json'
        descriptor = {'mode': 'audio_to_audio', 'source_wav': str(source),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'source_frames': 220500,
            'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(), 'noise_level': .6,
            'start_sec': None, 'end_sec': None}
        argv = [str(script), '--init-audio', str(source), '--init-noise-level', '.6', '--seconds', '5', '--out', str(self.root / 'raw.wav')]
        payload = {'argv': argv, 'cwd': str(self.root), 'raw_output': str(self.root / 'raw.wav'),
            'output': str(self.root / 'output.wav'), 'ffmpeg': runtime.tool('ffmpeg'), 'conditioning': descriptor}
        request.write_text(json.dumps(payload))
        self.assertIsNotNone(read_command(request).conditioning)
        for altered in ({**payload, 'argv': [str(script), '--seconds', '5']},
                {**payload, 'conditioning': {**descriptor, 'source_sha256': 'f' * 64}},
                {**payload, 'argv': [*argv, '--init-noise-level', '1.0']}):
            request.write_text(json.dumps(altered))
            with self.assertRaises(ValueError):
                read_command(request)

    def test_worker_refuses_existing_audio_before_invoking_native_code(self) -> None:
        script = self.root / 'generate.py'; script.write_text('raise RuntimeError("native must not run")\n')
        for existing in ('raw.wav', 'output.wav', 'output.partial.wav'):
            path = self.root / existing
            path.write_bytes(b'previous audio')
            command = WorkerCommand([str(script)], self.root, self.root / 'raw.wav', self.root / 'output.wav', runtime.tool('ffmpeg'))
            with self.subTest(existing=existing):
                with patch('scripts.local_music_worker.runpy.run_path') as native, self.assertRaises(ValueError):
                    run_worker(command)
                native.assert_not_called()
                self.assertEqual(path.read_bytes(), b'previous audio')
            path.unlink()

    def test_conditioned_adapter_emits_native_input_and_mask_or_noise_flags(self) -> None:
        from app.music_conditioning import RetainedConditioning, SOURCE_COMMIT
        from app.music_conditioning_contracts import MusicConditioningRecipe
        self.weights('stable_audio3')
        directory = self.root / 'job'; directory.mkdir()
        prepared = directory / 'conditioning.wav'
        with wave.open(str(prepared), 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(44100)
            audio.writeframes(b'\0\0\0\0' * 220500)
        script = self.env.paths['stable_audio3'] / 'optimized/mlx/scripts/sa3_mlx.py'
        script.parent.mkdir(); script.write_text('pass\n')
        for mode in ('audio_to_audio', 'inpaint'):
            with self.subTest(mode=mode):
                options = {'mode': mode, 'source': {'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32},
                    'source_sha256': 'b' * 64, **({'noise_level': .6} if mode == 'audio_to_audio' else {'start_sec': 1.0, 'end_sec': 2.0})}
                request = LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Piano',
                    'duration_seconds': 5, 'conditioning': options})
                recipe = MusicConditioningRecipe(source_sha256='b' * 64, prepared_sha256=hashlib.sha256(prepared.read_bytes()).hexdigest(),
                    source_rate_hz=44100, source_channels=2, source_frames=220500, prepared_frames=220500,
                    mode=mode, noise_level=.6 if mode == 'audio_to_audio' else None,
                    region_start_frame=44100 if mode == 'inpaint' else None, region_end_frame=88200 if mode == 'inpaint' else None,
                    source_commit=SOURCE_COMMIT, source_script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                    encoder_sha256='c' * 64, output_semantics='generated_full_mix' if mode == 'audio_to_audio' else 'original_pcm_outside_region')
                snapshot = RetainedConditioning(original_name='conditioning-original.wav', recipe=recipe)
                with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True), patch.object(runtime, 'conditioning_weights_present', return_value=True):
                    runtime.prepare_worker(request, directory / 'output.wav', conditioning=snapshot)
                command = read_command(directory / 'request.json')
                self.assertEqual(command.argv[command.argv.index('--init-audio') + 1], str(prepared))
                self.assertIsNotNone(command.conditioning)
                flag = '--inpaint-range' if mode == 'inpaint' else '--init-noise-level'
                self.assertEqual(command.argv[command.argv.index(flag) + 1], '1,2' if mode == 'inpaint' else '0.6')
