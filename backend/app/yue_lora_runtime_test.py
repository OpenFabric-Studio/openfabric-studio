"""Headless YuE2 commands stay fixed, local and separate from user settings."""
from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import yue_lora_runtime as runtime
from app.music_training_contracts import MusicLoraTrainingRequest


class YueLoraRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run_dir = self.root / ('a' * 32)
        self.run_dir.mkdir()
        trainer, comfy = self.root / 'trainer', self.root / 'comfy'
        trainer.mkdir(); (comfy / 'comfy/ldm/yue2').mkdir(parents=True)
        (trainer / 'train_cli.py').write_text('')
        (comfy / 'comfy/ldm/yue2/model.py').write_text('')
        base, python, ffmpeg = self.root / 'base.safetensors', self.root / 'python', self.root / 'ffmpeg'
        for file in (base, python, ffmpeg):
            file.write_bytes(b'installed')
        self.environment = runtime.YueLoraEnvironment(python, trainer, comfy, base, ffmpeg, 'mps')
        self.source = self.root / 'source.wav'; self.source.write_bytes(b'original')
        self.request = MusicLoraTrainingRequest(engine='yue2', name='Style', samples=[{'track_id': 1, 'caption': 'Warm piano'}], max_steps=2)

    def test_backend_manifest_uses_typed_settings_and_refuses_overwrite(self) -> None:
        argv, cwd, env = runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)
        self.assertEqual(cwd, self.run_dir)
        self.assertEqual(argv[0], str(self.environment.python))
        self.assertTrue(argv[1].endswith('scripts/yue_lora_worker.py'))
        self.assertEqual(argv[-2:], ['--request', str(self.run_dir / 'request.json')])
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        manifest = json.loads((self.run_dir / 'request.json').read_bytes())
        self.assertEqual(manifest['request']['engine'], 'yue2')
        self.assertEqual(manifest['sources'], [str(self.source)])
        self.assertNotIn('argv', manifest)
        with self.assertRaises(FileExistsError):
            runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)

    def test_fixed_cli_disables_unsupported_auxiliary_and_head_training(self) -> None:
        argv = runtime.training_argv(self.request, self.run_dir, self.environment)
        self.assertEqual(argv[argv.index('--conditioning') + 1], 'compact')
        self.assertEqual(argv[argv.index('--prefix-mode') + 1], 'off')
        self.assertEqual(argv[argv.index('--devices') + 1], 'mps')
        self.assertEqual(argv[argv.index('--alpha') + 1], str(self.request.rank))
        for unsafe in ('--use-semantic', '--train-acoustic-head', '--semantic-head', '--existing-lora', '--transcribe'):
            self.assertNotIn(unsafe, argv)
        self.assertEqual(argv[argv.index('--out') + 1], str(self.run_dir / 'trainer.safetensors'))

    def test_rejects_missing_runtime_wrong_engine_and_mismatched_sources(self) -> None:
        (self.environment.trainer_root / 'train_cli.py').unlink()
        self.assertFalse(runtime.runtime_ready(self.environment))
        with self.assertRaises(ValueError):
            runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)
        (self.environment.trainer_root / 'train_cli.py').write_text('')
        with self.assertRaises(ValueError):
            runtime.prepare_worker(self.request, self.run_dir, [], self.environment)
        other = self.request.model_copy(update={'engine': 'unsupported'})
        with self.assertRaises(ValueError):
            runtime.training_argv(other, self.run_dir, self.environment)

    def test_default_paths_and_readiness_use_configured_managed_root(self) -> None:
        from app.module_catalog import ModuleEnvironment
        managed = ModuleEnvironment.for_root(self.root / 'configured', platform='darwin', architecture='arm64', data_dir=self.root / 'library')
        actual = runtime.default_environment(managed)
        self.assertEqual(actual.trainer_root, managed.root / 'training/yue2/trainer')
        self.assertTrue(actual.base_checkpoint.is_relative_to(managed.root / 'training-assets/yue2'))
        with patch('app.music_training_setup.environment_ready', return_value=True), patch('app.music_training_setup.weights_ready', return_value=False):
            status = runtime.status(managed)
        self.assertTrue(status.supported)
        self.assertTrue(status.installed)
        self.assertFalse(status.weights_ready)
        self.assertEqual(status.validation, 'experimental')

    def test_planner_command_uses_private_scores_and_separate_output(self) -> None:
        paired = MusicLoraTrainingRequest.model_validate({**self.request.model_dump(), 'yue_pair': {'planner_steps': 3}})
        self.assertTrue(hasattr(runtime, 'score_argv'), 'Paired transcription command must be available')
        argv = runtime.training_argv(paired, self.run_dir, self.environment, target='ar')
        self.assertEqual(argv[3], 'planner')
        self.assertEqual(argv[argv.index('--steps') + 1], '3')
        self.assertEqual(argv[argv.index('--lr') + 1], '5e-05')
        self.assertEqual(argv[argv.index('--out') + 1], str(self.run_dir / 'trainer-ar.safetensors'))
        for flag in ('--semantic', '--no-abc', '--transcribe', '--conditioning', '--prefix-mode', '--segment-seconds'):
            self.assertNotIn(flag, argv)
        nar = runtime.training_argv(paired, self.run_dir, self.environment)
        self.assertEqual(nar[nar.index('--out') + 1], str(self.run_dir / 'trainer-nar.safetensors'))

    def test_score_command_requires_installed_executable_model_and_spec(self) -> None:
        self.assertTrue(hasattr(runtime, 'score_argv'), 'Paired transcription command must be available')
        cli, model, spec = self.root / 'audiocpp_cli', self.root / 'score.gguf', self.root / 'sheetsage2.json'
        cli.write_bytes(b'installed'); cli.chmod(0o755)
        model.write_bytes(b'GGUF' + bytes(32)); spec.write_text('{"family":"sheetsage2"}')
        env = replace(self.environment, score_cli=cli, score_model=model, score_model_spec=spec)
        self.assertTrue(runtime.score_ready(env))
        argv = runtime.score_argv(self.source, self.run_dir / 'scores', env)
        self.assertEqual(argv, [str(cli), '--task', 'midi', '--family', 'sheetsage2', '--model', str(model),
            '--backend', 'metal', '--mode', 'offline', '--model-spec-override', str(spec), '--audio', str(self.source),
            '--max-tokens', '5120', '--out-dir', str(self.run_dir / 'scores')])
        cli.chmod(0o644)
        self.assertFalse(runtime.score_ready(env))
        with self.assertRaisesRegex(ValueError, 'score_runtime_unprepared'):
            runtime.score_argv(self.source, self.run_dir / 'scores', env)

    def test_usable_score_validation_rejects_native_rest_fallback_and_unbounded_files(self) -> None:
        self.assertTrue(hasattr(runtime, 'validate_score'), 'ABC validation must be available')
        score = self.root / 'score.abc'
        valid = 'X:1\nT:Excerpt\nM:4/4\nL:1/8\nK:C\nV:1\nCDEF GABc|\n'
        score.write_text(valid)
        self.assertEqual(runtime.validate_score(score), valid.strip())
        for invalid in ('', 'model failed', 'X:1\nM:4/4\nL:1/8\nK:C\nz8|z8|',
            'X:1\nM:4/4\nL:1/8\nK:C\nfailed to generate score',
            'X:1\nT:ABCDEFG\nK:C\n"Cmaj" !accent! z8|', valid + '\x00', valid + ' ' * (256 * 1024)):
            score.write_text(invalid)
            with self.assertRaisesRegex(ValueError, 'score_invalid'):
                runtime.validate_score(score)

    def test_paired_readiness_fails_closed_when_ffmpeg_is_missing(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.video_media import VideoMediaError
        managed = ModuleEnvironment.for_root(self.root / 'configured', platform='darwin', architecture='arm64')
        with patch('app.music_training_setup.environment_ready', return_value=True), patch('app.music_training_setup.weights_ready', return_value=True), patch('app.video_media.tool', side_effect=VideoMediaError('ffmpeg_missing')):
            try:
                status = runtime.status(managed)
            except VideoMediaError:
                self.fail('A readiness read must report unavailable instead of throwing')
        self.assertFalse(status.paired_ready)

    def test_score_runtime_rejects_symlinked_ancestor(self) -> None:
        actual = self.root / 'actual'; actual.mkdir()
        cli, model, spec = actual / 'cli', actual / 'score.gguf', actual / 'spec.json'
        cli.write_bytes(b'installed'); cli.chmod(0o755)
        model.write_bytes(b'GGUF' + bytes(32)); spec.write_text('{"family":"sheetsage2"}')
        alias = self.root / 'alias'; alias.symlink_to(actual, target_is_directory=True)
        env = replace(self.environment, score_cli=alias / 'cli', score_model=model, score_model_spec=spec)
        self.assertFalse(runtime.score_ready(env), 'Symlinked runtime ancestors must not escape checks')

    def test_status_names_missing_score_prerequisite_without_disabling_legacy_runtime(self) -> None:
        from app.module_catalog import ModuleEnvironment
        managed = ModuleEnvironment.for_root(self.root / 'configured', platform='darwin', architecture='arm64')
        cli, model, spec = self.root / 'cli', self.root / 'score.gguf', self.root / 'spec.json'
        env = replace(self.environment, score_cli=cli, score_model=model, score_model_spec=spec)
        for missing in (None, cli, model, spec):
            with self.subTest(missing=missing):
                cli.write_bytes(b'installed'); cli.chmod(0o755)
                model.write_bytes(b'GGUF' + bytes(32)); spec.write_text('{"family":"sheetsage2"}')
                if missing is not None:
                    missing.unlink()
                with patch('app.music_training_setup.environment_ready', return_value=True), patch('app.music_training_setup.weights_ready', return_value=True), patch.object(runtime, 'default_environment', return_value=env):
                    current = runtime.status(managed)
                self.assertTrue(current.installed)
                self.assertTrue(current.weights_ready)
                self.assertEqual(current.paired_ready, missing is None)
                self.assertEqual(current.error_code, None if missing is None else 'yue_lora_score_runtime_unprepared')
                from app.music_training import _require_ready
                with patch('app.music_training.status', return_value=current):
                    _require_ready(self.request)


    def ar_request(self) -> MusicLoraTrainingRequest:
        return MusicLoraTrainingRequest.model_validate({**self.request.model_dump(),
            'yue_ar_source_job_id': 'c' * 32, 'max_steps': 3, 'learning_rate': 0.00005})

    def test_missing_planner_command_uses_ar_controls_without_acoustic_training(self) -> None:
        request = self.ar_request()
        argv = runtime.training_argv(request, self.run_dir, self.environment, target='ar')
        self.assertEqual(argv[3], 'planner')
        self.assertEqual(argv[argv.index('--steps') + 1], '3')
        self.assertEqual(argv[argv.index('--lr') + 1], '5e-05')
        self.assertEqual(argv[argv.index('--out') + 1], str(self.run_dir / 'trainer-ar.safetensors'))
        for flag in ('--conditioning', '--segment-seconds', '--existing-lora'):
            self.assertNotIn(flag, argv)
        with self.assertRaisesRegex(ValueError, 'ar_only'):
            runtime.training_argv(request, self.run_dir, self.environment, target='nar')

    def test_missing_planner_requires_checked_cached_audio_and_score_runtime(self) -> None:
        request = self.ar_request()
        with wave.open(str(self.source), 'wb') as audio:
            audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(48000 * 4))
        checksum = hashlib.sha256(self.source.read_bytes()).hexdigest()
        cli, model, spec = self.root / 'cli', self.root / 'score.gguf', self.root / 'spec.json'
        cli.write_bytes(b'installed'); cli.chmod(0o755)
        model.write_bytes(b'GGUF' + bytes(32)); spec.write_text('{"family":"sheetsage2"}')
        env = replace(self.environment, score_cli=cli, score_model=model, score_model_spec=spec)
        with self.assertRaisesRegex(ValueError, 'prepared_audio'):
            runtime.prepare_worker(request, self.run_dir, [self.source], env)
        prepared = (runtime.PreparedAudio(self.source, checksum),)
        with self.assertRaisesRegex(ValueError, 'score_runtime_unprepared'):
            runtime.prepare_worker(request, self.run_dir, [self.source], self.environment, prepared_audio=prepared)
        runtime.prepare_worker(request, self.run_dir, [self.source], env, prepared_audio=prepared)
        payload = json.loads((self.run_dir / 'request.json').read_bytes())
        self.assertEqual(payload['prepared_audio'], [{'path': str(self.source), 'sha256': checksum}])
        self.assertEqual(payload['runtime']['score_cli'], str(cli))

    def test_cached_audio_rejects_hash_mismatch_missing_and_symlinked_ancestors(self) -> None:
        request = self.ar_request()
        with wave.open(str(self.source), 'wb') as audio:
            audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(48000 * 4))
        checksum = hashlib.sha256(self.source.read_bytes()).hexdigest()
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        for audio, expected in ((self.source, '0' * 64), (self.root / 'missing.wav', checksum),
            (alias / self.source.name, checksum)):
            with self.subTest(audio=audio, expected=expected):
                with self.assertRaisesRegex(ValueError, 'prepared_audio'):
                    runtime.validate_prepared_audio(runtime.PreparedAudio(audio, expected), segment_seconds=request.segment_seconds)
        self.assertFalse((self.run_dir / 'request.json').exists())
