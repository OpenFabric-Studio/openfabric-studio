"""Offline source-patch and build provenance checks; no model weights are used."""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ROOT / 'external/patches'
FIXTURES = Path(__file__).parent / 'test_fixtures/yue-native'
PINS = ('39f9013463053e206aa160f8453d734f78999b9d', 'f2b4937306daa25f5c78520f3c626ed31495a37a')


def source_fixture(destination: Path, pin: str) -> None:
    raw: object = json.loads(gzip.decompress((FIXTURES / 'sources.json.gz').read_bytes()))
    if not isinstance(raw, dict) or not isinstance(raw.get(pin), dict):
        raise ValueError('invalid_native_fixture')
    for name, content in raw[pin].items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError('invalid_native_fixture_file')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def apply_patches(destination: Path) -> None:
    subprocess.run(['git', 'apply', '--check', str(PATCHES / 'yue-workspace-release.patch'), str(PATCHES / 'yue-progress.patch')], cwd=destination, check=True, capture_output=True)
    subprocess.run(['git', 'apply', str(PATCHES / 'yue-workspace-release.patch'), str(PATCHES / 'yue-progress.patch')], cwd=destination, check=True, capture_output=True)


class NativeSourcePatchTests(unittest.TestCase):
    def test_complete_patches_apply_to_both_pinned_native_sources(self) -> None:
        self.assertTrue((PATCHES / 'yue-progress.patch').is_file(), 'Reproducible native progress patch must exist')
        self.assertTrue((PATCHES / 'yue-workspace-release.patch').is_file(), 'Workspace release patch must exist')
        for pin in PINS:
            with self.subTest(pin=pin), tempfile.TemporaryDirectory() as directory:
                source = Path(directory)
                source_fixture(source, pin)
                apply_patches(source)
                ar = (source / 'src/models/yue2/ar_runtime.cpp').read_text()
                session = (source / 'src/models/yue2/session.cpp').read_text()
                self.assertIn('RemiqoraProgressRun progress(request.options)', session)
                self.assertIn('release_compute_workspace();', ar)
                self.assertIn('return steps == s && graph != nullptr;', ar)
                release = ar.split('void release_compute_workspace()', 1)[1].split('~PrefixStateGraph()', 1)[0]
                self.assertLess(release.index('ggml_backend_synchronize'), release.index('ggml_gallocr_free'))
                self.assertNotIn('state_buffer_free', release)
                self.assertNotIn('state_ctx.reset', release)
                self.assertNotIn('key_values.clear', release)

    def test_optional_progress_header_compiles_and_filters_stale_foreign_runs(self) -> None:
        self.assertTrue((PATCHES / 'yue-progress.patch').exists(), 'Native progress implementation must exist')
        compiler = shutil.which(os.environ.get('CXX', 'c++'))
        if compiler is None:
            self.skipTest('C++17 compiler is unavailable')
        if subprocess.run([compiler, '--version'], capture_output=True).returncode != 0:
            self.skipTest('C++ compiler toolchain is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            source_fixture(source, PINS[0])
            apply_patches(source)
            probe = source / 'probe.cpp'
            probe.write_text('''#include "engine/models/yue2/remiqora_progress.h"
#include <iostream>
#include <thread>
#include <condition_variable>
#include <atomic>
#include <vector>
using namespace engine::models::yue2;
std::string read() { std::ifstream f(std::getenv("REMIQORA_YUE2_PROGRESS_PATH")); return {std::istreambuf_iterator<char>(f), {}}; }
int main() {
    { RemiqoraProgressRun invalid({{"remiqora_run_id", "bad"}}); remiqora_set_progress(RemiqoraProgressPhase::Semantic, 32); }
    if (!read().empty()) return 2;
    std::mutex mutex; std::condition_variable ready; bool entered=false, release=false;
    std::thread foreign([&] {
        RemiqoraProgressRun old({{"remiqora_run_id", std::string(32,'a')}});
        remiqora_set_progress(RemiqoraProgressPhase::Semantic, 32);
        { std::lock_guard<std::mutex> lock(mutex); entered=true; } ready.notify_one();
        { std::unique_lock<std::mutex> lock(mutex); ready.wait(lock,[&] { return release; }); }
        remiqora_advance_progress(64, true);
    });
    { std::unique_lock<std::mutex> lock(mutex); ready.wait(lock,[&] { return entered; }); }
    std::cout << read() << '\\n';
    {
        RemiqoraProgressRun active({{"remiqora_run_id", std::string(32,'b')}});
        remiqora_set_progress(RemiqoraProgressPhase::Acoustic,0,4);
        { std::lock_guard<std::mutex> lock(mutex); release=true; } ready.notify_one(); foreign.join();
        std::cout << read() << '\\n';
        remiqora_advance_progress(4,true);
        remiqora_set_progress(RemiqoraProgressPhase::Done,1,1);
    }
    remiqora_advance_progress(32,true);
    std::cout << read() << '\\n';
    std::atomic<bool> observing(true); std::vector<std::string> snapshots;
    std::thread observer([&] {
        while (observing.load()) {
            auto snapshot = read();
            if (snapshots.size() < 4096) snapshots.push_back(snapshot);
        }
    });
    {
        RemiqoraProgressRun active({{"remiqora_run_id", std::string(32,'c')}});
        remiqora_set_progress(RemiqoraProgressPhase::Semantic,0);
        if (remiqora_total_work(100000001,1).has_value()) return 3;
        for (int i=1; i<=512; ++i) remiqora_advance_progress(i,true);
    }
    observing.store(false); observer.join();
    for (const auto & snapshot : snapshots) std::cout << snapshot << '\\n';
}
''')
            binary = source / ('probe.exe' if os.name == 'nt' else 'probe')
            compiled = subprocess.run([compiler, '-std=c++17', '-D_POSIX_C_SOURCE=200809L', '-pthread', '-I', str(source / 'include'), str(probe), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            environment = {**os.environ, 'REMIQORA_YUE2_PROGRESS_PATH': str(source / 'progress.json')}
            result = subprocess.run([str(binary)], env=environment, check=True, capture_output=True, text=True)
            states = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(states[0]['run_id'], 'a' * 32)
            self.assertEqual(states[0]['phase'], 'semantic')
            self.assertIsNone(states[0]['total'])
            self.assertEqual(states[1]['run_id'], 'b' * 32)
            self.assertEqual(states[1]['current'], 0)
            self.assertEqual(states[1]['total'], 4)
            self.assertEqual(states[2]['phase'], 'done')
            self.assertEqual(states[2]['current'], 1)
            self.assertLessEqual(states[2]['started_ms'], states[2]['phase_started_ms'])
            self.assertLessEqual(states[2]['phase_started_ms'], states[2]['updated_ms'])
            self.assertGreater(len(states), 3, 'Concurrent reader must observe complete publications')
            for snapshot in states[3:]:
                self.assertIn(snapshot['run_id'], ('b' * 32, 'c' * 32))
                self.assertLessEqual(snapshot['started_ms'], snapshot['phase_started_ms'])
                self.assertLessEqual(snapshot['phase_started_ms'], snapshot['updated_ms'])
                self.assertLessEqual(snapshot['current'], 512)
            self.assertEqual(list(source.glob('progress.json.*.tmp')), [])


class NativeBuildHelperTests(unittest.TestCase):
    def test_build_enables_management_required_by_the_app_startup(self) -> None:
        from scripts import setup_yue_native as helper
        command = helper.cmake_command(Path('source'), Path('build'), 'cpu', PINS[0])
        self.assertIn('-DAUDIOCPP_BUILD_NATIVE_MODEL_MANAGER=ON', command)

    def test_helper_rejects_unsupported_source_or_dirty_existing_checkout(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('scripts.setup_yue_native'), 'Native build helper must exist')
        from scripts import setup_yue_native as helper
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with patch.object(helper, 'git_output', return_value='unknown'):
                with self.assertRaisesRegex(ValueError, 'unsupported_native_source'):
                    helper.validate_source(source)
            with patch.object(helper, 'git_output', side_effect=[PINS[0], ' M tracked.cpp']):
                with self.assertRaisesRegex(ValueError, 'native_source_not_clean'):
                    helper.validate_source(source)

    def test_build_manifest_requires_complete_patch_and_binary_identity(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('scripts.setup_yue_native'), 'Native build helper must exist')
        from scripts import setup_yue_native as helper
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'audiocpp_server'
            binary.write_bytes(b'fixture binary')
            manifest = helper.create_manifest(binary, PINS[0], 'cpu')
            helper.write_manifest(binary, manifest)
            self.assertEqual(helper.read_build_manifest(binary).progress_schema, 1)
            binary.write_bytes(b'changed binary')
            with self.assertRaisesRegex(ValueError, 'native_binary_identity_mismatch'):
                helper.read_build_manifest(binary)

    def test_workspace_inside_read_only_source_is_rejected_before_any_write(self) -> None:
        from scripts import setup_yue_native as helper
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            destination = source / 'nested-build'
            with patch.object(helper, 'check_patches', return_value=PINS[0]), \
                 patch.object(helper, 'verify_patched_source'), \
                 patch.object(helper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                with self.assertRaisesRegex(ValueError, 'native_workspace_inside_source'):
                    helper.prepare_source(destination, PINS[0], source)
            self.assertFalse(destination.exists())

    def test_manifest_rejects_boolean_versions_and_partial_patch_identity(self) -> None:
        from scripts import setup_yue_native as helper
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'audiocpp_server'
            binary.write_bytes(b'fixture binary')
            original = helper.create_manifest(binary, PINS[0], 'cpu')
            for field in ('schema_version', 'progress_schema', 'patches'):
                helper.write_manifest(binary, original)
                raw = json.loads(helper.manifest_path(binary).read_text())
                raw[field] = [] if field == 'patches' else True
                helper.manifest_path(binary).write_text(json.dumps(raw))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'invalid_native_build_manifest'):
                    helper.read_build_manifest(binary)

    def test_failed_rebuild_removes_stale_capability_manifest(self) -> None:
        from scripts import setup_yue_native as helper
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            binary = workspace / 'build/bin' / ('audiocpp_server.exe' if os.name == 'nt' else 'audiocpp_server')
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'previous successful binary')
            helper.write_manifest(binary, helper.create_manifest(binary, PINS[0], 'cpu'))
            with patch.object(helper, 'verify_patched_source'), \
                 patch.object(helper.shutil, 'which', return_value='/fixture/tool'), \
                 patch.object(helper.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['cmake'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    helper.build_native(workspace, workspace / 'source', PINS[0], 'cpu', 2)
            self.assertFalse(helper.manifest_path(binary).exists())
            self.assertEqual(binary.read_bytes(), b'previous successful binary')
