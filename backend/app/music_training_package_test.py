from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import struct
import tempfile
import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave
import zipfile

from app.music_training_contracts import MusicLoraTrainingRequest
from app import music_training_package
from app.music_training_package import prepare_package
from app.yue_lora_runtime import YueLoraEnvironment
from scripts import music_training_package_bootstrap as bootstrap


class MusicTrainingPackageTests(unittest.TestCase):
    def audio(self, root: Path, seconds: int = 1) -> Path:
        source = root / 'original.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(8000)
            output.writeframes(b'\0\0' * 8000 * seconds)
        return source

    def request(self, engine: str = 'yue2', lyrics: str = '') -> MusicLoraTrainingRequest:
        return MusicLoraTrainingRequest.model_validate({'engine': engine, 'name': 'test',
            'samples': [{'track_id': 1, 'caption': 'synth', 'lyrics': lyrics}], 'max_steps': 1, 'segment_seconds': 6})

    def pair_request(self, lyrics: str = '') -> MusicLoraTrainingRequest:
        return MusicLoraTrainingRequest.model_validate({
            **self.request('yue2').model_dump(), 'yue_pair': {'planner_steps': 2},
            'samples': [{'track_id': 1, 'caption': 'synth', 'lyrics': lyrics}]})

    def test_pair_package_scores_the_exact_bounded_excerpt_and_checks_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root, 7);original = source.read_bytes()
            ffmpeg = shutil.which('ffmpeg')
            self.assertIsNotNone(ffmpeg)
            environment = YueLoraEnvironment(root / 'python', root / 'trainer', root / 'comfy', root / 'base', Path(str(ffmpeg)), 'mps')
            scored: list[tuple[bytes, float]] = []
            def native_argv(audio: Path, output: Path, env: YueLoraEnvironment) -> list[str]:
                with wave.open(str(audio)) as recording:
                    scored.append((audio.read_bytes(), recording.getnframes() / recording.getframerate()))
                self.assertEqual(env, environment)
                return [sys.executable, '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]).joinpath("score.abc").write_text("X:1\\nM:4/4\\nL:1/8\\nK:C\\nCDEF GABc|\\n")', str(output)]
            with patch('app.yue_lora_runtime.default_environment', return_value=environment), patch('app.yue_lora_runtime.score_ready', return_value=True), patch('app.yue_lora_runtime.score_argv', side_effect=native_argv):
                target = prepare_package(self.pair_request(), [source], root / 'package.zip')
            extracted = root / 'extracted';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                sample = manifest['sources'][0]
                self.assertEqual(archive.read(sample['original_path']), original)
                self.assertEqual(archive.read(sample['path']), scored[0][0])
                self.assertEqual(scored[0][1], 6)
                self.assertEqual(sample['score_sha256'], hashlib.sha256(archive.read(sample['score_path'])).hexdigest())
                self.assertIn(sample['score_path'], [entry['path'] for entry in manifest['files']])
                self.assertIn('adapter-ar.safetensors', archive.read('README.txt').decode())
                self.assertIn('adapter-nar.safetensors', archive.read('README.txt').decode())
                self.assertNotIn(str(root), archive.read('manifest.json').decode())
                archive.extractall(extracted)
            checked = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertFalse((extracted / 'runtime').exists())
            seen_plans: list[dict[str, object]] = []
            def command(argv: list[str], **kwargs: object) -> None:
                if '--request' not in argv:
                    return
                plan_path = Path(argv[-1])
                plan = json.loads(plan_path.read_bytes());seen_plans.append(plan)
                self.assertEqual(plan['prepared_scores'], [{'path': str(extracted / sample['score_path']), 'sha256': sample['score_sha256']}])
                self.assertEqual(plan['sources'], [str(extracted / sample['path'])])
                self.assertNotIn('score_cli', plan['runtime'])
                self.pair_result(plan_path.parent.parent, plan_path.parent, manifest)
            with patch.object(bootstrap.shutil, 'which', side_effect=lambda name: '/tools/' + name), patch.object(bootstrap.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), patch.object(bootstrap, 'command', side_effect=command), patch.object(bootstrap, 'checkout'), patch.object(bootstrap, 'fetch_asset'):
                bootstrap.run(extracted, manifest)
            self.assertEqual(len(seen_plans), 1)
            (extracted / sample['score_path']).write_text('changed')
            helper = extracted / 'backend/scripts/stable_lora_worker.py'
            helper.write_text('raise RuntimeError("packaged code executed")')
            for entry in manifest['files']:
                if entry['path'] == 'backend/scripts/stable_lora_worker.py':
                    entry.update({'sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), 'bytes': helper.stat().st_size})
            (extracted / 'manifest.json').write_text(json.dumps(manifest))
            checked = subprocess.run([sys.executable, str(extracted / 'bootstrap.py')], capture_output=True, text=True)
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn('package_file_integrity', checked.stderr)
            self.assertNotIn('packaged code executed', checked.stderr)
            self.assertEqual(source.read_bytes(), original)

    def test_pair_rejects_lyrics_on_a_longer_recording_before_score_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root, 7)
            with patch.object(music_training_package, '_prepare_pair_sample', side_effect=AssertionError('score work started before lyrics validation')), self.assertRaisesRegex(ValueError, 'lyrics'):
                prepare_package(self.pair_request('matching words'), [source], root / 'package.zip')
            self.assertFalse((root / 'package.zip').exists())

    def test_pair_reserves_space_for_pcm_excerpts_before_starting_score_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root, 7)
            free = source.stat().st_size * 2 + 32 * 1024**2 + 500000
            with patch.object(music_training_package.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)), patch.object(music_training_package, '_prepare_pair_sample', side_effect=AssertionError('score work started before disk preflight')), self.assertRaisesRegex(ValueError, 'disk_space'):
                prepare_package(self.pair_request(), [source], root / 'package.zip')
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['original.wav'])

    def pair_result(self, root: Path, run: Path | None = None, package_manifest: dict[str, object] | None = None) -> tuple[Path, dict[str, object]]:
        from app.yue_lora_convert import BASE_SHA256
        run = run or root / ('7' * 32);run.mkdir(exist_ok=True)
        request = self.pair_request()
        fingerprint = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
        manifest = package_manifest or {'engine': 'yue2', 'request': request.model_dump(mode='json', exclude_none=True), 'request_sha256': fingerprint,
            'sources': [{'sha256': 'a' * 64, 'score_sha256': 'b' * 64}],
            'assets': [{'sha256': BASE_SHA256, 'path': 'training-assets/yue2/base.safetensors'}]}
        (run / 'dataset.json').write_text(json.dumps({'request_sha256': fingerprint, 'samples': [
            {'source_sha256': manifest['sources'][0]['sha256'], 'score_sha256': manifest['sources'][0]['score_sha256'],
             'caption': 'synth', 'lyrics': '', 'instrumental': False}]}))
        artifacts: list[dict[str, object]] = []
        for target in ('ar', 'nar'):
            metadata = json.dumps({'__metadata__': {'openfabric_yue2_conversion': json.dumps({'stage': target, 'base_sha256': BASE_SHA256})}}).encode()
            artifact = run / f'adapter-{target}.safetensors'
            artifact.write_bytes(struct.pack('<Q', len(metadata)) + metadata + b'factor')
            artifacts.append({'target': target, 'checkpoint': artifact.name, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()})
        receipt = {'job_id': run.name, 'engine': 'yue2', 'request_sha256': fingerprint,
            'dataset_sha256': hashlib.sha256((run / 'dataset.json').read_bytes()).hexdigest(), 'base_sha256': BASE_SHA256,
            'max_steps': request.max_steps, 'planner_steps': 2, 'artifacts': artifacts}
        (run / 'completion.json').write_text(json.dumps(receipt))
        return run, manifest

    def test_pair_result_checks_both_targets_hashes_paths_and_receipt_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();run, manifest = self.pair_result(root)
            self.assertEqual([path.name for path in bootstrap.verify_result(run, manifest)], ['adapter-ar.safetensors', 'adapter-nar.safetensors'])
            original = json.loads((run / 'completion.json').read_bytes())
            changes = [('job_id', '8' * 32), ('request_sha256', '0' * 64), ('dataset_sha256', '0' * 64),
                ('base_sha256', '0' * 64), ('max_steps', 3), ('planner_steps', 3), ('engine', 'heartmula')]
            for field, value in changes:
                with self.subTest(field=field):
                    receipt = copy.deepcopy(original);receipt[field] = value
                    (run / 'completion.json').write_text(json.dumps(receipt))
                    with self.assertRaises(ValueError):
                        bootstrap.verify_result(run, manifest)
            for failure in ('missing', 'same-path', 'duplicate-target', 'swapped', 'traversal', 'bad-hash'):
                with self.subTest(failure=failure):
                    receipt = copy.deepcopy(original)
                    if failure == 'missing': receipt['artifacts'].pop()
                    if failure == 'same-path': receipt['artifacts'][1].update(receipt['artifacts'][0], target='nar')
                    if failure == 'duplicate-target': receipt['artifacts'][1]['target'] = 'ar'
                    if failure == 'swapped':
                        receipt['artifacts'][0]['target'], receipt['artifacts'][1]['target'] = 'nar', 'ar'
                    if failure == 'traversal': receipt['artifacts'][0]['checkpoint'] = '../adapter-ar.safetensors'
                    if failure == 'bad-hash': receipt['artifacts'][1]['sha256'] = '0' * 64
                    (run / 'completion.json').write_text(json.dumps(receipt))
                    with self.assertRaises(ValueError):
                        bootstrap.verify_result(run, manifest)
            (run / 'completion.json').write_text(json.dumps(original))
            dataset = json.loads((run / 'dataset.json').read_bytes())
            for failure in ('request', 'source', 'score', 'caption', 'lyrics', 'instrumental', 'missing-sample'):
                with self.subTest(dataset=failure):
                    changed_dataset = copy.deepcopy(dataset)
                    if failure == 'request': changed_dataset['request_sha256'] = '0' * 64
                    if failure == 'source': changed_dataset['samples'][0]['source_sha256'] = '0' * 64
                    if failure == 'score': changed_dataset['samples'][0]['score_sha256'] = '0' * 64
                    if failure in ('caption', 'lyrics'): changed_dataset['samples'][0][failure] = 'changed'
                    if failure == 'instrumental': changed_dataset['samples'][0]['instrumental'] = True
                    if failure == 'missing-sample': changed_dataset['samples'].clear()
                    (run / 'dataset.json').write_text(json.dumps(changed_dataset))
                    receipt = copy.deepcopy(original);receipt['dataset_sha256'] = hashlib.sha256((run / 'dataset.json').read_bytes()).hexdigest()
                    (run / 'completion.json').write_text(json.dumps(receipt))
                    with self.assertRaises(ValueError):
                        bootstrap.verify_result(run, manifest)
            (run / 'dataset.json').write_text(json.dumps(dataset))
            (run / 'completion.json').write_text(json.dumps(original))
            (run / 'adapter-nar.safetensors').unlink()
            with self.assertRaises(ValueError):
                bootstrap.verify_result(run, manifest)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX signal cancellation; Windows uses the existing owned job')
    def test_package_score_child_is_terminated_and_waited_on_sigterm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();marker = root / 'child.pid'
            child_code = 'from pathlib import Path;import os,sys,time;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)'
            code = ('import signal,sys;from scripts.music_training_package_worker import cancel;'
                'from app.music_training_package import _command;signal.signal(signal.SIGTERM,cancel);'
                '_command([sys.executable,"-c",sys.argv[1],sys.argv[2]],timeout=30)')
            worker = subprocess.Popen([sys.executable, '-c', code, child_code, str(marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not marker.is_file() and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.is_file())
                worker.terminate()
                stdout, stderr = worker.communicate(timeout=10)
                self.assertEqual(worker.returncode, 143, (stdout, stderr))
                pid = int(marker.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                if worker.poll() is None:
                    worker.kill();worker.communicate()

    @unittest.skipIf(sys.platform == 'win32', 'POSIX signals; Windows tree cleanup requires a Windows run')
    def test_standalone_bootstrap_sigterm_drains_child_and_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();marker = root / 'children.json'
            child_code = ('import json,os,signal,subprocess,sys,time;from pathlib import Path;'
                'descendant=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                'signal.signal(signal.SIGTERM,lambda signum,frame:sys.exit(143));'
                'Path(sys.argv[1]).write_text(json.dumps([os.getpid(),descendant.pid]));'
                '\ntry: time.sleep(60)\nfinally: descendant.wait(timeout=3)')
            code = ('import sys;from scripts import music_training_package_bootstrap as bootstrap;'
                'bootstrap.verify=lambda root:{};'
                'arguments=sys.argv[1:];'
                'bootstrap.run=lambda root,manifest:bootstrap.command([sys.executable,"-c",arguments[0],arguments[1]]);'
                'sys.argv=["bootstrap.py"];bootstrap.main()')
            worker = subprocess.Popen([sys.executable, '-c', code, child_code, str(marker)], start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not marker.is_file() and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.is_file())
                pids = json.loads(marker.read_bytes())
                worker.terminate()
                stdout, stderr = worker.communicate(timeout=15)
                self.assertEqual(worker.returncode, 143, (stdout, stderr))
                for pid in pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
            finally:
                if marker.is_file():
                    for pid in json.loads(marker.read_bytes()):
                        try: os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                try: os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                worker.communicate()

    def test_bootstrap_captured_queries_use_the_same_owned_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();repository = root / 'repository';(repository / '.git').mkdir(parents=True)
            revision = 'a' * 40
            with patch.object(bootstrap, 'execute', return_value=subprocess.CompletedProcess(['git'], 0, revision + '\n')) as query, patch.object(bootstrap, 'command') as command:
                bootstrap.checkout(repository, 'https://example.invalid/no-network', revision)
            query.assert_called_once_with(['git', '-C', str(repository), 'rev-parse', 'HEAD'], capture=True, check=False, timeout=20)
            command.assert_called_once_with(['git', '-C', str(repository), 'diff-index', '--quiet', 'HEAD', '--'])
            downloaded = root / 'downloaded';downloaded.write_bytes(b'verified fixture')
            environment = {'ONLY': 'fixture'}
            artifact = {'path': 'models/test.safetensors', 'bytes': downloaded.stat().st_size,
                'sha256': hashlib.sha256(downloaded.read_bytes()).hexdigest(),
                'url': 'https://huggingface.co/fixture/model/resolve/' + revision + '/test.safetensors'}
            with patch.object(bootstrap, 'execute', return_value=subprocess.CompletedProcess(['fixture'], 0, json.dumps(str(downloaded)))) as query:
                bootstrap.fetch_asset(root, artifact, Path(sys.executable), environment)
            self.assertEqual((root / 'models/test.safetensors').read_bytes(), downloaded.read_bytes())
            self.assertEqual(query.call_args.kwargs, {'capture': True, 'environment': environment, 'timeout': 6 * 3600})

    def test_bootstrap_executor_preserves_capture_exit_errors_and_timeouts(self) -> None:
        argv = [sys.executable, '-c', 'print("verified fixture");raise SystemExit(7)']
        result = bootstrap.execute(argv, capture=True, check=False, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (7, 'verified fixture\n'))
        with self.assertRaises(subprocess.CalledProcessError):
            bootstrap.execute(argv, capture=True, timeout=5)
        with self.assertRaises(subprocess.TimeoutExpired):
            bootstrap.execute([sys.executable, '-c', 'import time;time.sleep(60)'], capture=True, timeout=.1)

    def test_bootstrap_windows_executor_retains_job_until_all_descendants_exit(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        events: list[str] = []
        job = Mock(spec=WindowsJob);job.name = 'Local\\OpenFabricNative_' + 'a' * 32
        job.active_processes.side_effect = [2, 0]
        job.terminate.side_effect = lambda: events.append('terminated')
        job.close.side_effect = lambda: events.append('closed')
        process = Mock();process.stdin = Mock();process.wait.side_effect = lambda **kwargs: events.append('waited') or 0
        with patch.object(bootstrap.sys, 'platform', 'win32'), patch('app.orchestrator.windows_job.WindowsJob.create', return_value=job) as create, patch.object(bootstrap.subprocess, 'Popen', return_value=process) as spawn:
            bootstrap.execute(['fixture', '--argument'], capture=True)
        create.assert_called_once_with()
        self.assertEqual(spawn.call_args.args[0][-5:], ['--job', job.name, '--', 'fixture', '--argument'])
        self.assertEqual(spawn.call_args.kwargs['stdin'], subprocess.PIPE)
        self.assertEqual(events, ['waited', 'terminated', 'waited', 'closed'])
        self.assertEqual(job.active_processes.call_count, 2)
        process.stdin.close.assert_called_once_with()

    def test_bootstrap_windows_executor_closes_job_when_command_is_rejected(self) -> None:
        job = Mock();job.name = 'Local\\OpenFabricNative_' + 'b' * 32
        with patch.object(bootstrap.sys, 'platform', 'win32'), patch('app.orchestrator.windows_job.WindowsJob.create', return_value=job), self.assertRaises(ValueError):
            bootstrap.execute([])
        job.close.assert_called_once_with()

    @unittest.skipUnless(sys.platform == 'win32', 'Real bootstrap Job Object containment requires Windows')
    def test_windows_bootstrap_launcher_exit_drains_orphaned_descendant(self) -> None:
        from app.orchestrator.windows_job import WindowsJob
        original_create = WindowsJob.create
        observer = None
        def create() -> WindowsJob:
            nonlocal observer
            job = original_create();observer = WindowsJob.open(job.name)
            return job
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'ready'
            code = ('import subprocess,sys;from pathlib import Path;'
                'subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                'Path(sys.argv[1]).write_text("ready")')
            try:
                with patch('app.orchestrator.windows_job.WindowsJob.create', side_effect=create):
                    bootstrap.execute([sys.executable, '-c', code, str(marker)], timeout=10)
                self.assertTrue(marker.is_file())
                self.assertIsNotNone(observer)
                if observer is not None:
                    self.assertEqual(observer.active_processes(), 0)
            finally:
                if observer is not None:
                    observer.terminate();observer.close()

    def test_packages_originals_pins_and_offline_check_without_models_or_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root);original = source.read_bytes()
            target = prepare_package(self.request(), [source], root / 'package.zip')
            extracted = root / 'extracted';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertIn('LICENSE', names)
                self.assertIn('NOTICE', names)
                self.assertIn('backend/scripts/yue_lora_worker.py', names)
                self.assertFalse(any('heart' in name.lower() for name in names))
                self.assertIn('backend/app/__init__.py', names)
                self.assertIn('backend/app/orchestrator/__init__.py', names)
                self.assertIn('backend/app/orchestrator/windows_job.py', names)
                self.assertIn('backend/app/orchestrator/windows_supervisor.py', names)
                self.assertFalse(any(name.endswith('.safetensors') or '.env' in name for name in names))
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['nvidia_validation'], 'unverified')
                self.assertEqual(manifest['request']['engine'], 'yue2')
                self.assertEqual(sum(item['bytes'] for item in manifest['assets']), 7799983228)
                self.assertEqual(archive.read(manifest['sources'][0]['path']), original)
                self.assertNotIn(str(root), archive.read('manifest.json').decode())
                archive.extractall(extracted)
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Package files verified', result.stdout)
            self.assertFalse((extracted / 'runtime').exists())
            self.assertEqual(source.read_bytes(), original)
            original_manifest = (extracted / 'manifest.json').read_bytes()
            for field in ('engine', 'request'):
                with self.subTest(removed_engine=field):
                    changed = copy.deepcopy(manifest)
                    if field == 'engine':
                        changed['engine'] = 'heartmula'
                    else:
                        changed['request']['engine'] = 'heartmula'
                        changed['request_json'] = json.dumps(changed['request'])
                        changed['request_sha256'] = hashlib.sha256(changed['request_json'].encode()).hexdigest()
                    (extracted / 'manifest.json').write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, 'package_manifest_invalid'):
                        bootstrap.verify(extracted)
            (extracted / 'manifest.json').write_bytes(original_manifest)
            manifest['request']['max_steps'] = 2
            (extracted / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('package_request_integrity', result.stderr)
            (extracted / 'manifest.json').write_bytes(original_manifest)
            helper = extracted / 'backend/scripts/stable_lora_worker.py'
            original_helper = helper.read_bytes()
            helper.write_text('raise RuntimeError("unverified code executed")')
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('package_file_integrity', result.stderr)
            self.assertNotIn('unverified code executed', result.stderr)
            helper.write_bytes(original_helper)
            (extracted / 'audio/000.wav').write_bytes(b'changed')
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_yue_bundle_contains_complete_lightweight_worker_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            target = prepare_package(self.request('yue2'), [source], root / 'package.zip')
            extracted = root / 'extracted';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extracted)
            result = subprocess.run([sys.executable, str(extracted / 'backend/scripts/yue_lora_worker.py'), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_desktop_layout_uses_resources_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            backend = root / 'resources/backend';licenses = root / 'resources/licenses'
            original_backend = music_training_package._BACKEND
            for name in music_training_package._COMMON + music_training_package._YUE + ('scripts/music_training_package_bootstrap.py',):
                target = backend / name;target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original_backend / name, target)
            licenses.mkdir(parents=True)
            for name in ('LICENSE', 'LICENSE-MIT', 'NOTICE'):
                shutil.copyfile(original_backend.parent / name, licenses / name)
            with patch.object(music_training_package, '_BACKEND', backend), patch.object(music_training_package, '_REPOSITORY', backend.parent):
                target = prepare_package(self.request(), [source], root / 'package.zip')
            with zipfile.ZipFile(target) as archive:
                for name in ('LICENSE', 'LICENSE-MIT', 'NOTICE'):
                    self.assertEqual(archive.read(name), (licenses / name).read_bytes())

    def test_rejects_existing_destination_symlinks_and_misaligned_lyrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root, 7)
            target = root / 'package.zip';target.write_bytes(b'keep')
            with self.assertRaises((ValueError, FileExistsError)):
                prepare_package(self.request(), [source], target)
            self.assertEqual(target.read_bytes(), b'keep')
            with self.assertRaisesRegex(ValueError, 'lyrics'):
                prepare_package(self.pair_request(lyrics='full song words'), [source], root / 'other.zip')
            try:
                (root / 'link.wav').symlink_to(source)
            except OSError:
                return
            with self.assertRaises(ValueError):
                prepare_package(self.request(), [root / 'link.wav'], root / 'other.zip')

    def test_interrupted_archive_is_never_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            with patch('zipfile.ZipFile.write', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                prepare_package(self.request(), [source], root / 'package.zip')
            self.assertFalse((root / 'package.zip').exists())
            self.assertFalse(any(path.name.endswith('.part') for path in root.iterdir()))
