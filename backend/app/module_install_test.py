from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

import httpx

from app import module_install as install
from app.module_catalog import ModuleEnvironment, engine_python
from app.module_evidence import environment_verified
from app.module_contracts import ModuleInfo, ModuleInventory
from app.module_jobs import InstallContext, InstallOutcome, ModuleJobService, ModuleSetupError


class ModuleInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        self.enterContext(patch.object(InstallContext, 'update_detail'))

    def native_fixture(self) -> tuple[Path, ModuleInventory]:
        target = self.environment.paths['yue2']
        binaries = target / 'build/macos-metal-release/bin'
        binaries.mkdir(parents=True, exist_ok=True)
        for name in ('audiocpp_server', 'audiocpp_cli', 'audiocpp_gguf'):
            path = binaries / name; path.write_bytes(b'existing ' + name.encode()); path.chmod(0o755)
        (target / 'tools').mkdir(exist_ok=True)
        (target / 'tools/model_manager_v2.py').write_text('# existing downloader')
        (target / 'model_specs').mkdir(exist_ok=True)
        (target / 'model_specs/sheetsage2.json').write_text('{"family":"sheetsage2","preserve":"existing spec"}')
        (target / 'models').mkdir(exist_ok=True)
        (target / 'models/private.gguf').write_bytes(b'existing model')
        install._record_owned(self.context, 'yue2', target, install.load_manifest().engine.tag)
        inventory = ModuleInventory(platform='darwin', architecture='arm64', acceleration='apple_silicon',
            managed_root=str(self.environment.root), checked_at='2026-10-16', modules=[ModuleInfo(id='yue2', name='YuE',
                description='Native music', state='ready', supported=True, managed=True, automation='automatic',
                dependencies=[], capabilities=[], evidence=[], actions=[])])
        return target, inventory

    async def extract_native_fixture(self, argv: list[str], **_: object) -> None:
        directory = Path(argv[-1]); directory.mkdir(parents=True)
        for name in ('audiocpp_server', 'audiocpp_cli', 'audiocpp_gguf'):
            (directory / name).write_bytes(b'pinned ' + name.encode())
        (directory / 'tools').mkdir()
        (directory / 'tools/model_manager_v2.py').write_text('# pinned downloader')
        (directory / 'model_specs').mkdir()
        (directory / 'model_specs/sheetsage2.json').write_text('{"family":"sheetsage2"}')

    async def test_verify_repairs_missing_score_cli_and_spec_without_replacing_existing_files(self) -> None:
        from app import module_native_setup
        for relative in ('build/macos-metal-release/bin/audiocpp_cli', 'model_specs/sheetsage2.json'):
            with self.subTest(relative=relative):
                target, inventory = self.native_fixture()
                missing = target / relative; missing.unlink()
                preserved = {path: path.read_bytes() for path in target.rglob('*') if path.is_file()}
                with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), \
                        patch.object(self.context, 'run', side_effect=self.extract_native_fixture), \
                        patch.object(install, 'download_artifact', AsyncMock()) as download, \
                        patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing model files reused.'))) as models:
                    outcome = await install.verify(self.context, 'yue2', download_models=True)
                self.assertEqual(outcome.state, 'verified')
                self.assertTrue(missing.is_file())
                self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
                self.assertTrue(download.await_count)
                models.assert_awaited_once_with(self.context, True)
                if missing.name == 'audiocpp_cli':
                    self.assertEqual(missing.read_bytes(), b'pinned audiocpp_cli')
                    self.assertEqual(missing.stat().st_mode & 0o111, 0o111)

    async def test_ready_native_verification_repairs_permissions_without_downloading(self) -> None:
        from app import module_native_setup
        target, inventory = self.native_fixture()
        cli = target / 'build/macos-metal-release/bin/audiocpp_cli'; cli.chmod(0o644)
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), \
                patch.object(self.context, 'run', AsyncMock()) as run, patch.object(install, 'download_artifact', AsyncMock()) as download, \
                patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing model files reused.'))) as models:
            await install.verify(self.context, 'yue2', download_models=True)
        self.assertEqual(cli.stat().st_mode & 0o111, 0o111)
        models.assert_awaited_once_with(self.context, True)
        run.assert_not_awaited(); download.assert_not_awaited()

    async def test_ready_native_verification_checks_missing_models(self) -> None:
        from app import module_native_setup
        _, inventory = self.native_fixture()
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), \
                patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Missing score model installed.'))) as models:
            await install.verify(self.context, 'yue2', download_models=True)
        models.assert_awaited_once_with(self.context, True)

    async def test_native_prerequisite_repair_rejects_symlink_and_directory_conflicts_before_download(self) -> None:
        target, inventory = self.native_fixture()
        spec = target / 'model_specs/sheetsage2.json'; spec.unlink()
        outside = self.root / 'private-spec'; outside.write_text('preserve private')
        spec.symlink_to(outside)
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), patch.object(install, 'download_artifact', AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await install.verify(self.context, 'yue2', download_models=True)
        download.assert_not_awaited()
        self.assertEqual(outside.read_text(), 'preserve private')
        spec.unlink(); spec.mkdir()
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), patch.object(install, 'download_artifact', AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await install.verify(self.context, 'yue2', download_models=True)
        download.assert_not_awaited()

    async def test_native_prerequisite_repair_validates_the_release_before_publishing_any_files(self) -> None:
        target, inventory = self.native_fixture()
        cli, spec = target / 'build/macos-metal-release/bin/audiocpp_cli', target / 'model_specs/sheetsage2.json'
        cli.unlink(); spec.unlink()
        async def incomplete(argv: list[str], **kwargs: object) -> None:
            await self.extract_native_fixture(argv, **kwargs)
            (Path(argv[-1]) / 'model_specs/sheetsage2.json').unlink()
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), \
                patch.object(self.context, 'run', side_effect=incomplete), patch.object(install, 'download_artifact', AsyncMock()):
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await install.verify(self.context, 'yue2', download_models=True)
        self.assertFalse(cli.exists())
        self.assertFalse(spec.exists())

    async def test_native_prerequisite_repair_cannot_replace_a_file_created_during_staging(self) -> None:
        target, inventory = self.native_fixture()
        cli = target / 'build/macos-metal-release/bin/audiocpp_cli'; cli.unlink()
        async def race(argv: list[str], **kwargs: object) -> None:
            await self.extract_native_fixture(argv, **kwargs)
            cli.write_bytes(b'preserve concurrent file')
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), \
                patch.object(self.context, 'run', side_effect=race), patch.object(install, 'download_artifact', AsyncMock()):
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await install.verify(self.context, 'yue2', download_models=True)
        self.assertEqual(cli.read_bytes(), b'preserve concurrent file')
        self.assertEqual(list(cli.parent.glob('.native-score-*')), [])

    async def test_native_prerequisite_repair_rejects_linked_ancestors_and_preserves_unowned_files(self) -> None:
        from app import module_native_setup
        target, inventory = self.native_fixture()
        specification = target / 'model_specs/sheetsage2.json'; specification.unlink(); specification.parent.rmdir()
        outside = self.root / 'private-specs'; outside.mkdir()
        private = outside / 'sheetsage2.json'; private.write_text('{"family":"sheetsage2"}')
        specification.parent.symlink_to(outside, target_is_directory=True)
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), patch.object(install, 'download_artifact', AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await install.verify(self.context, 'yue2', download_models=True)
        download.assert_not_awaited()
        self.assertEqual(private.read_text(), '{"family":"sheetsage2"}')
        specification.parent.unlink()
        install._owned_path(self.context, 'yue2').unlink()
        with patch('app.module_catalog.inventory', AsyncMock(return_value=inventory)), patch.object(install, 'download_artifact', AsyncMock()) as download, \
                patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing native files reused.'))):
            await install.verify(self.context, 'yue2', download_models=True)
        download.assert_not_awaited()
        self.assertFalse(specification.exists())

    async def test_music_setup_uses_isolated_dependencies_then_automatic_model_setup(self) -> None:
        from app.module_catalog import DEFINITIONS
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'c' * 32)
        async def clone(context: InstallContext, identifier: install.ModuleId, pin: install.SourcePin, target: Path) -> None:
            for marker in DEFINITIONS[identifier].source_markers:
                path = target / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# pinned source')
        with patch.object(context, 'run', AsyncMock()) as run, patch.object(install, '_clone', clone), patch.object(install, '_check_environment', AsyncMock()), patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Models installed automatically.')), create=True) as models:
            for identifier in ('stable_audio3',):
                outcome = await install.install(context, identifier, True)
                self.assertEqual(outcome.state, 'verified')
                models.assert_awaited_with(context, identifier, True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        stable = environment.paths['stable_audio3']
        self.assertEqual(commands[0], ['/fixed/uv', 'venv', '--python', '3.11', str(stable / '.venv')])
        self.assertEqual(commands[1], ['/fixed/uv', 'pip', 'install', '--python', str(engine_python(stable, 'darwin')), '-r', str(stable / 'optimized/mlx/requirements.txt')])
        self.assertTrue(all('download' not in argument and 'bootstrap' not in argument for command in commands for argument in command))

    async def test_existing_external_music_environment_finishes_automatic_models_without_source_changes(self) -> None:
        from app.module_catalog import DEFINITIONS
        engine = self.root / 'external-ace'
        for marker in DEFINITIONS['ace_step'].source_markers:
            path = engine / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# preserved source')
        python = engine_python(engine, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        environment = replace(self.environment, paths={**self.environment.paths, 'ace_step': engine})
        context = InstallContext(ModuleJobService(environment), 'd' * 32)
        original = sorted((str(path.relative_to(engine)), path.read_bytes()) for path in engine.rglob('*') if path.is_file())
        with patch.object(install, '_check_environment', AsyncMock()) as check, patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.')), create=True) as models:
            outcome = await install.verify(context, 'ace_step', download_models=True)
        self.assertEqual(outcome.state, 'verified')
        check.assert_awaited_once_with(context, 'ace_step', engine)
        models.assert_awaited_once_with(context, 'ace_step', True)
        self.assertEqual(original, sorted((str(path.relative_to(engine)), path.read_bytes()) for path in engine.rglob('*') if path.is_file()))

    async def test_explicit_reinstall_refreshes_only_owned_dependencies(self) -> None:
        from app.module_catalog import DEFINITIONS
        identifiers: tuple[install.ModuleId, ...] = ('ace_step', 'separation', 'speech', 'singing', 'stable_audio3', 'kokoro', 'chatterbox', 'wan22', 'rvc')
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'e' * 32)
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                target = environment.paths[identifier]
                for marker in DEFINITIONS[identifier].source_markers:
                    path = target / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('# preserved source')
                python = engine_python(target, 'darwin')
                python.parent.mkdir(parents=True)
                python.write_text('# existing interpreter')
                (target / 'checkpoint.bin').write_bytes(b'preserved weights')
                version = install.load_manifest().aceStep.commit if identifier == 'ace_step' else install.SOURCE_PINS[identifier].commit if identifier in install.SOURCE_PINS else hashlib.sha256(install.manifest_path().read_bytes()).hexdigest()
                install._record_owned(context, identifier, target, version)
                original = sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file())
                with patch.object(context, 'run', AsyncMock()) as run, patch.object(install, '_clone', AsyncMock()) as clone, patch.object(install.shutil, 'rmtree') as delete, patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, '_check_environment', AsyncMock()), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.'))):
                    await install.install(context, identifier, False, reinstall=True)
                clone.assert_not_awaited()
                delete.assert_not_called()
                run.assert_awaited_once()
                command = run.call_args.args[0]
                prefix = ['/fixed/uv', 'sync'] if identifier in ('ace_step', 'separation') else ['/fixed/uv', 'pip', 'install']
                self.assertEqual(command[:len(prefix)], prefix)
                self.assertEqual(command.count('--reinstall'), 1)
                self.assertNotIn('--download-models', command)
                self.assertEqual(original, sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file()))
                with patch.object(context, 'run', AsyncMock()) as normal, patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, '_check_environment', AsyncMock()), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.'))):
                    await install.install(context, identifier, False)
                normal.assert_awaited_once()
                self.assertNotIn('--reinstall', normal.call_args.args[0])

    async def test_reinstall_refuses_unowned_external_and_excluded_modules_without_changes(self) -> None:
        target = self.environment.paths['speech']
        target.mkdir(parents=True)
        (target / 'private.txt').write_text('keep')
        external = self.root / 'external-speech'
        external.mkdir()
        (external / 'private.txt').write_text('keep external')
        external_environment = replace(self.environment, paths={**self.environment.paths, 'speech': external})
        external_context = InstallContext(ModuleJobService(external_environment), 'f' * 32)
        cases: tuple[tuple[InstallContext, install.ModuleId], ...] = ((self.context, 'speech'), (self.context, 'stable_audio3'), (external_context, 'speech'), *((self.context, identifier) for identifier in ('media', 'yue2', 'video', 'speaker_review', 'transcription', 'source_import', 'ebooks')))
        with patch.object(InstallContext, 'run', AsyncMock()) as run, patch.object(install, '_clone', AsyncMock()) as clone, patch.object(install.shutil, 'rmtree') as delete:
            for context, identifier in cases:
                with self.subTest(identifier=identifier, external=context is external_context):
                    with self.assertRaisesRegex(ModuleSetupError, 'reinstall_unavailable'):
                        await install.install(context, identifier, False, reinstall=True)
        run.assert_not_awaited()
        clone.assert_not_awaited()
        delete.assert_not_called()
        self.assertEqual((target / 'private.txt').read_text(), 'keep')
        self.assertEqual((external / 'private.txt').read_text(), 'keep external')

    async def test_reinstall_refuses_modified_owned_source_and_unsupported_platform(self) -> None:
        target = self.environment.paths['stable_audio3']
        target.mkdir(parents=True)
        source = target / 'optimized/mlx/requirements.txt'
        source.parent.mkdir(parents=True)
        source.write_text('# pinned source')
        install._record_owned(self.context, 'stable_audio3', target, install.SOURCE_PINS['stable_audio3'].commit)
        source.write_text('# user modification')
        unsupported = replace(self.environment, platform='linux', architecture='x64')
        unsupported_context = InstallContext(ModuleJobService(unsupported), 'd' * 32)
        with patch.object(InstallContext, 'run', AsyncMock()) as run:
            with self.assertRaisesRegex(ModuleSetupError, 'reinstall_unavailable'):
                await install.install(self.context, 'stable_audio3', False, reinstall=True)
            with self.assertRaisesRegex(ModuleSetupError, 'unsupported_platform'):
                await install.install(unsupported_context, 'stable_audio3', False, reinstall=True)
        run.assert_not_awaited()
        self.assertEqual(source.read_text(), '# user modification')

    async def test_reinstall_refuses_symlinked_environment_without_running_commands(self) -> None:
        target = self.environment.paths['stable_audio3']
        target.mkdir(parents=True)
        outside = self.root / 'external-environment'
        outside.mkdir()
        (outside / 'private.txt').write_text('keep')
        (target / '.venv').symlink_to(outside, target_is_directory=True)
        with patch.object(install, '_owned', return_value=True), patch.object(self.context, 'run', AsyncMock()) as run:
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await install.install(self.context, 'stable_audio3', False, reinstall=True)
        run.assert_not_awaited()
        self.assertEqual((outside / 'private.txt').read_text(), 'keep')

    def test_reinstall_availability_checks_saved_ownership_without_writes(self) -> None:
        target = self.environment.paths['stable_audio3']
        target.mkdir(parents=True)
        source = target / 'optimized/mlx/requirements.txt'
        source.parent.mkdir(parents=True)
        source.write_text('# pinned source')
        version = install.SOURCE_PINS['stable_audio3'].commit
        install._record_owned(self.context, 'stable_audio3', target, version)
        def available(environment: ModuleEnvironment, identifier: install.ModuleId) -> bool:
            before = sorted((str(path.relative_to(self.root)), path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file())
            result = install.reinstall_available(environment, identifier)
            after = sorted((str(path.relative_to(self.root)), path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file())
            self.assertEqual(before, after)
            return result
        self.assertTrue(available(self.environment, 'stable_audio3'))
        self.assertFalse(available(self.environment, 'speech'))
        self.assertFalse(available(self.environment, 'media'))
        self.assertFalse(available(replace(self.environment, platform='linux', architecture='x64'), 'stable_audio3'))
        external = self.root / 'external-stable'
        self.assertFalse(available(replace(self.environment, paths={**self.environment.paths, 'stable_audio3': external}), 'stable_audio3'))
        source.write_text('# user source')
        self.assertFalse(available(self.environment, 'stable_audio3'))
        source.write_text('# pinned source')
        install._record_owned(self.context, 'stable_audio3', target, 'outdated-source-version')
        self.assertFalse(available(self.environment, 'stable_audio3'))
        install._record_owned(self.context, 'stable_audio3', target, version)
        outside = self.root / 'external-environment'
        outside.mkdir()
        (target / '.venv').symlink_to(outside, target_is_directory=True)
        self.assertFalse(available(self.environment, 'stable_audio3'))

    def test_reinstall_availability_bounds_source_reads_and_refuses_receipt_links(self) -> None:
        target = self.environment.paths['stable_audio3']
        target.mkdir(parents=True)
        source = target / 'optimized/mlx/requirements.txt'
        source.parent.mkdir(parents=True)
        source.write_text('# pinned source')
        install._record_owned(self.context, 'stable_audio3', target, install.SOURCE_PINS['stable_audio3'].commit)
        with source.open('ab') as output:
            output.truncate(9 * 1024**2)
        with patch.object(install, '_sha256', side_effect=AssertionError('oversized source must not be hashed')):
            self.assertFalse(install.reinstall_available(self.environment, 'stable_audio3'))
        receipt = install._owned_path(self.context, 'stable_audio3')
        outside = self.root / 'external-receipt.json'
        receipt.rename(outside)
        receipt.symlink_to(outside)
        self.assertFalse(install.reinstall_available(self.environment, 'stable_audio3'))

    def test_owned_media_binaries_are_not_limited_by_the_python_source_budget(self) -> None:
        target = self.environment.paths['media']
        (target / 'bin').mkdir(parents=True)
        for name in ('ffmpeg', 'ffprobe'):
            with (target / 'bin' / name).open('wb') as output:
                output.truncate(9 * 1024**2)
        version = hashlib.sha256(install.manifest_path().read_bytes()).hexdigest()
        install._record_owned(self.context, 'media', target, version)
        self.assertTrue(install._owned(self.context, 'media', target, version))

    async def test_failed_or_cancelled_reinstall_revokes_prior_environment_verification(self) -> None:
        from app.module_catalog import DEFINITIONS
        from app.module_evidence import VerificationReceipt
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'c' * 32)
        target = environment.paths['stable_audio3']
        for marker in DEFINITIONS['stable_audio3'].source_markers:
            path = target / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# pinned source')
        python = engine_python(target, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# existing interpreter')
        (target / '.venv/lib/python3.11/site-packages').mkdir(parents=True)
        (target / 'checkpoint.bin').write_bytes(b'preserved model')
        install._record_owned(context, 'stable_audio3', target, install.SOURCE_PINS['stable_audio3'].commit)
        original = sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file())
        for failure in (ModuleSetupError('installer_failed'), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(context, 'run', AsyncMock()):
                    await install._check_environment(context, 'stable_audio3', target)
                self.assertTrue(environment_verified(environment, 'stable_audio3'))
                async def failed_command(argv: list[str], **kwargs: object) -> None:
                    self.assertIn('--reinstall', argv)
                    self.assertFalse(environment_verified(environment, 'stable_audio3'), 'revoke verification before package mutation starts')
                    raise failure
                with patch.object(context, 'run', side_effect=failed_command), patch.object(install.shutil, 'which', return_value='/fixed/git'):
                    with self.assertRaises(type(failure)):
                        await install.install(context, 'stable_audio3', False, reinstall=True)
                receipt = VerificationReceipt.model_validate_json((environment.root / '.setup/verification/stable_audio3.json').read_bytes())
                self.assertFalse(receipt.environment_verified)
                self.assertEqual(original, sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file()))

    async def test_cancel_waits_for_staging_deletion_before_releasing_setup(self) -> None:
        await self._assert_staging_deletion_drained(shutdown=False)

    async def test_shutdown_waits_for_staging_deletion_before_releasing_setup(self) -> None:
        await self._assert_staging_deletion_drained(shutdown=True)

    async def _assert_staging_deletion_drained(self, *, shutdown: bool) -> None:
        from app import module_catalog as catalog, module_jobs as jobs
        from app.module_contracts import ModuleInstallRequest, ModulePlanRequest
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        actual = install.shutil.rmtree
        def delete(path: Path) -> None:
            entered.set()
            release.wait(5)
            actual(path)
            finished.set()
        async def installer(context: InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            staged = context.workspace / 'speech-source'
            staged.mkdir(parents=True)
            await install._clone(context, 'speech', install.SOURCE_PINS['speech'], context.environment.paths['speech'])
            return jobs.InstallOutcome('verified', 'unused')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(jobs, '_application_busy', return_value=False), patch.object(install.shutil, 'rmtree', delete):
            service = ModuleJobService(self.environment, installer=installer)
            reviewed = await catalog.plan(ModulePlanRequest(features=['media']), self.environment)
            created = await service.create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
            cancelled: asyncio.Task[jobs.ModuleInstallJob] | asyncio.Task[None] | None = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                cancelled = asyncio.create_task(service.shutdown()) if shutdown else asyncio.create_task(service.cancel(created.id))
                await asyncio.sleep(0.05)
                self.assertFalse(cancelled.done(), 'cancel must wait for the deletion thread')
                self.assertTrue(jobs.work_busy(), 'scratch cannot be reused until deletion drains')
                with self.assertRaisesRegex(ModuleSetupError, 'setup_busy'):
                    await ModuleJobService(self.environment).create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
            finally:
                release.set()
                if cancelled is not None:
                    await cancelled
                await service.shutdown()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            self.assertEqual(service.get(created.id).state, 'interrupted' if shutdown else 'cancelled')
            self.assertFalse(jobs.work_busy())
            staged = service.environment.root / '.setup/staging' / created.id / 'speech-source'
            staged.mkdir()
            (staged / 'new-source').write_text('retained after cleanup')
            await asyncio.sleep(0.01)
            self.assertEqual((staged / 'new-source').read_text(), 'retained after cleanup')

    async def test_verified_download_resumes_known_artifact_only(self) -> None:
        content = b'verified-content'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'file.bin'
        target.parent.mkdir()
        target.with_suffix('.bin.part').write_bytes(content[:4])
        def response(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers['range'], f'bytes=4-{len(content)-1}')
            return httpx.Response(206, headers={'content-range': f'bytes 4-{len(content)-1}/{len(content)}'}, content=content[4:])
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(target.read_bytes(), content)

    async def test_download_completes_bounded_ranges_before_publishing(self) -> None:
        content = b'bounded range download'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'bounded.bin'
        ranges: list[str] = []
        def response(request: httpx.Request) -> httpx.Response:
            start = len(ranges) * 4
            end = min(start + 4, len(content)) - 1
            ranges.append(request.headers.get('range', ''))
            self.assertEqual(ranges[-1], f'bytes={start}-{end}')
            self.assertFalse(target.exists(), 'publication must wait for every range and the final checksum')
            return httpx.Response(206, headers={'content-range': f'bytes {start}-{end}/{len(content)}'}, content=content[start:end + 1])
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(len(ranges), 6)
        self.assertEqual(target.read_bytes(), content)
        self.assertFalse(target.with_name(target.name + '.part').exists())

    async def test_completed_ranges_each_get_their_own_bounded_retry_budget(self) -> None:
        content = b'abcdefghijkl'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'retry-ranges.bin'
        calls = 0
        def response(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            start = (calls // 2) * 4
            calls += 1
            self.assertEqual(request.headers.get('range'), f'bytes={start}-{start + 3}')
            return httpx.Response(503) if calls % 2 else httpx.Response(206, headers={'content-range': f'bytes {start}-{start + 3}/{len(content)}'}, content=content[start:start + 4])
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True), patch.object(install.asyncio, 'sleep', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(calls, 6)
        self.assertEqual(target.read_bytes(), content)

    async def test_interrupted_range_resumes_its_prefix_without_extending_the_window(self) -> None:
        content = b'a' * 65536 + b'b' * 65536 + b'c'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'interrupted-range.bin'
        ranges = iter(((0, 131071), (65536, 131071), (131072, 131072)))
        class Interrupted(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield content[:65536]
                raise httpx.RemoteProtocolError('interrupted range')
        calls = 0
        def response(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            start, end = next(ranges)
            self.assertEqual(request.headers.get('range'), f'bytes={start}-{end}')
            headers = {'content-range': f'bytes {start}-{end}/{len(content)}'}
            return httpx.Response(206, headers=headers, stream=Interrupted()) if calls == 1 else httpx.Response(206, headers=headers, content=content[start:end + 1])
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 131072, create=True), patch.object(install.asyncio, 'sleep', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(calls, 3)
        self.assertEqual(target.read_bytes(), content)

    async def test_incomplete_range_does_not_reset_retry_budget_for_partial_progress(self) -> None:
        content = b'abcdefghijkl'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'incomplete-range.bin'
        calls = 0
        def response(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            start = calls
            calls += 1
            self.assertEqual(request.headers.get('range'), f'bytes={start}-3')
            return httpx.Response(206, headers={'content-range': f'bytes {start}-3/{len(content)}', 'content-length': str(4 - start)}, content=content[start:start + 1])
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True), patch.object(install.asyncio, 'sleep', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                with self.assertRaisesRegex(ModuleSetupError, 'download_failed'):
                    await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(calls, 3)
        self.assertEqual(target.with_name(target.name + '.part').read_bytes(), content[:3])
        self.assertFalse(target.exists())

    async def test_invalid_range_headers_and_overlong_range_bodies_are_rejected(self) -> None:
        content = b'abcdefghij'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        class Overlong(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield b'abcde'
        for index, header in enumerate(('bytes 1-3/10', 'bytes 0-4/10', 'bytes 0-2/10', 'bytes 0-3/11', 'bytes 0-3/10')):
            target = self.root / 'cache' / f'invalid-range-{index}.bin'
            with self.subTest(header=header), patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True):
                response = httpx.Response(206, headers={'content-range': header}, stream=Overlong())
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                    with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                        await install.download_artifact(artifact, target, self.context, client=client)
            self.assertFalse(target.exists())
            partial = target.with_name(target.name + '.part')
            self.assertTrue(not partial.exists() or partial.stat().st_size <= 4)

    async def test_server_ignoring_bounded_range_restarts_and_verifies_the_full_body(self) -> None:
        content = b'full body fallback'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'ignored-range.bin'
        target.parent.mkdir()
        target.with_name(target.name + '.part').write_bytes(content[:2])
        calls = 0
        def response(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            self.assertEqual(request.headers.get('range'), 'bytes=2-5')
            return httpx.Response(200, content=content)
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(calls, 1)
        self.assertEqual(target.read_bytes(), content)

    async def test_short_ignored_range_cannot_reset_the_budget_by_rewinding_a_prefix(self) -> None:
        content = b'abcdefghijklmnopqrstuvwx'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'rewound-range.bin'
        target.parent.mkdir()
        target.with_name(target.name + '.part').write_bytes(content[:12])
        ranges = iter(((12, 15), (2, 5), (6, 9)))
        calls = 0
        def response(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            self.assertLessEqual(calls, 3, 'rewound partial progress cannot grant a fresh retry budget')
            start, end = next(ranges)
            self.assertEqual(request.headers.get('range'), f'bytes={start}-{end}')
            if calls == 1:
                return httpx.Response(200, content=content[:2])
            return httpx.Response(206, headers={'content-range': f'bytes {start}-{end}/{len(content)}'}, content=content[start:end + 1])
        with patch.object(install, 'DOWNLOAD_RANGE_BYTES', 4, create=True), patch.object(install.asyncio, 'sleep', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                with self.assertRaisesRegex(ModuleSetupError, 'download_failed'):
                    await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(calls, 3)
        self.assertFalse(target.exists())

    async def test_bad_hash_preserves_existing_artifact(self) -> None:
        target = self.root / 'cache' / 'file.bin'
        target.parent.mkdir()
        target.write_bytes(b'old-good-file')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256='0' * 64, bytes=3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'bad'))) as client:
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(target.read_bytes(), b'old-good-file')

    async def test_transient_download_retries_are_bounded_and_keep_existing_artifact(self) -> None:
        content = b'verified retry'
        target = self.root / 'cache' / 'retry.bin'
        target.parent.mkdir()
        target.write_bytes(b'original')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        calls = 0
        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503) if calls < 3 else httpx.Response(200, content=content)
        with patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    await install.download_artifact(artifact, target, self.context, client=client)
                except httpx.HTTPError:
                    self.fail('a transient asset response must retry within the reviewed attempt bound')
        self.assertEqual(calls, 3)
        self.assertEqual(target.read_bytes(), content)

    async def test_short_download_resumes_received_bytes_without_starting_again(self) -> None:
        content = b'verified partial transfer'
        target = self.root / 'cache' / 'short.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        calls = 0
        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, content=content[:4])
            self.assertEqual(request.headers.get('range'), f'bytes=4-{len(content)-1}')
            return httpx.Response(206, headers={'content-range': f'bytes 4-{len(content)-1}/{len(content)}'}, content=content[4:])
        with patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    await install.download_artifact(artifact, target, self.context, client=client)
                except ModuleSetupError:
                    self.fail('an incomplete transfer must retain and resume its received prefix')
        self.assertEqual(calls, 2)
        self.assertEqual(target.read_bytes(), content)

    async def test_inconsistent_resume_range_is_rejected_before_publication(self) -> None:
        content = b'verified range'
        target = self.root / 'cache' / 'range.bin'
        target.parent.mkdir()
        target.with_name(target.name + '.part').write_bytes(content[:4])
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        response = httpx.Response(206, headers={'content-range': 'bytes 4-999/1000'}, content=content[4:])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertFalse(target.exists())

    async def test_stalled_transfer_finishes_with_a_stable_error_and_no_publication(self) -> None:
        class Stalled(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                await asyncio.Event().wait()
                yield b'unreachable'
        target = self.root / 'cache' / 'stall.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256='0' * 64, bytes=16)
        with patch.object(install, 'DOWNLOAD_STALL_SECONDS', .01, create=True), patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stalled()))) as client:
                try:
                    await asyncio.wait_for(install.download_artifact(artifact, target, self.context, client=client), .3)
                except ModuleSetupError as error:
                    self.assertEqual(error.code, 'download_stalled')
                except TimeoutError:
                    self.fail('the downloader must bound a stalled stream itself')
                else:
                    self.fail('a stalled transfer cannot publish an artifact')
        self.assertFalse(target.exists())

    async def test_retry_exhaustion_and_integrity_failure_never_replace_prior_data(self) -> None:
        target = self.root / 'cache' / 'prior.bin'
        target.parent.mkdir()
        target.write_bytes(b'original')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(b'new').hexdigest(), bytes=3)
        with patch.object(install.asyncio, 'sleep', AsyncMock()):
            for status, content, code, attempts in ((503, b'', 'download_failed', 3), (403, b'', 'download_failed', 1), (200, b'bad', 'artifact_integrity', 1)):
                calls = 0
                def response(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(status, content=content)
                async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                    with self.assertRaisesRegex(ModuleSetupError, code):
                        await install.download_artifact(artifact, target, self.context, client=client)
                self.assertEqual(calls, attempts)
                self.assertEqual(target.read_bytes(), b'original')

    async def test_cancel_during_backoff_retains_received_prefix_and_closes_stream(self) -> None:
        target = self.root / 'cache' / 'cancel.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(b'complete').hexdigest(), bytes=8)
        entered = asyncio.Event()
        actual_sleep = asyncio.sleep
        async def backoff(delay: float) -> None:
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'comp'))) as client:
            with patch.object(install.asyncio, 'sleep', side_effect=backoff):
                task = asyncio.create_task(install.download_artifact(artifact, target, self.context, client=client))
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await actual_sleep(0)
        self.assertFalse(target.exists())
        self.assertEqual(target.with_name(target.name + '.part').read_bytes(), b'comp')

    async def test_native_managed_models_use_the_reviewed_adapter_without_source_patches(self) -> None:
        target = self.environment.paths['yue2']
        (target / 'tools').mkdir(parents=True)
        source = target / 'tools/model_manager_v2.py'
        source.write_bytes(b'# preserved downloader')
        from app import module_native_setup
        with patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Pinned model setup finished.'))) as models, patch.object(self.context, 'run', AsyncMock()) as run:
            outcome = await install._native_weights(self.context, target, install.load_manifest(), True)
        self.assertEqual(outcome.state, 'verified')
        models.assert_awaited_once_with(self.context, True)
        run.assert_not_awaited()
        self.assertEqual(source.read_bytes(), b'# preserved downloader')

    async def test_native_install_makes_all_bundled_tools_executable(self) -> None:
        assets = install.load_manifest()
        target = self.environment.paths['yue2']
        async def extract(argv: list[str], **_: object) -> None:
            directory = Path(argv[-1]); directory.mkdir(parents=True)
            (directory / 'tools').mkdir()
            (directory / 'tools/model_manager_v2.py').write_text('# pinned downloader')
            for name in ('audiocpp_server', 'audiocpp_cli', 'audiocpp_gguf'):
                path = directory / name; path.write_bytes(b'preserved executable'); path.chmod(0o644)
        with patch.object(self.context, 'run', side_effect=extract), patch.object(install, 'download_artifact', new=AsyncMock()), patch.object(install, '_native_weights', new=AsyncMock(return_value=InstallOutcome('verified', 'fixture'))):
            await install._native(self.context, target, assets, False)
        binary_dir = target / 'build/macos-metal-release/bin'
        for name in ('audiocpp_server', 'audiocpp_cli', 'audiocpp_gguf'):
            path = binary_dir / name
            self.assertEqual(path.stat().st_mode & 0o111, 0o111, name)
            self.assertEqual(path.read_bytes(), b'preserved executable')

    async def test_owned_native_tool_permission_repair_preserves_unowned_and_linked_files(self) -> None:
        assets = install.load_manifest()
        target = self.environment.paths['yue2']
        binary_dir = target / 'build/macos-metal-release/bin'
        binary_dir.mkdir(parents=True)
        cli = binary_dir / 'audiocpp_cli'; cli.write_bytes(b'preserve'); cli.chmod(0o644)
        (target / 'tools').mkdir(); (target / 'tools/model_manager_v2.py').write_text('# pinned')
        self.assertFalse(install.repair_native_tools(self.context))
        self.assertEqual(cli.stat().st_mode & 0o111, 0)
        install._record_owned(self.context, 'yue2', target, assets.engine.tag)
        self.assertTrue(install.repair_native_tools(self.context))
        self.assertEqual(cli.stat().st_mode & 0o111, 0o111)
        self.assertEqual(cli.read_bytes(), b'preserve')
        outside = self.root / 'private'; outside.write_bytes(b'private'); outside.chmod(0o644)
        cli.unlink(); cli.symlink_to(outside)
        with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
            install.repair_native_tools(self.context)
        self.assertEqual(outside.stat().st_mode & 0o111, 0)

    async def test_speaker_setup_is_dependency_only_even_when_weights_selected(self) -> None:
        from app import speaker_review
        from app.speaker_review_contracts import SpeakerReviewCapability
        target = self.environment.paths['speaker_review']
        with patch.object(self.context, 'run', AsyncMock()) as run, patch.object(speaker_review, 'capability', return_value=SpeakerReviewCapability(available=False, deps_available=True, reason='speaker_weights_missing')):
            outcome = await install.install(self.context, 'speaker_review', True)
        self.assertEqual(outcome.state, 'manual')
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertTrue(command[1].endswith('/scripts/setup_speaker_review.py'))
        self.assertEqual(command[command.index('--runtime-root') + 1], str(target))
        self.assertNotIn('--download-models', command)
        self.assertIn('No weights', outcome.detail)

    def test_zip_traversal_and_tar_links_are_rejected_before_extraction(self) -> None:
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('../outside', 'unsafe')
        with self.assertRaisesRegex(ModuleSetupError, 'archive_unsafe'):
            install.extract_archive(archive, self.root / 'staged')
        self.assertFalse((self.root / 'outside').exists())
        archive = self.root / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('escaped')
            member.type = tarfile.SYMTYPE
            member.linkname = '/private'
            output.addfile(member)
        with self.assertRaisesRegex(ModuleSetupError, 'archive_unsafe'):
            install.extract_archive(archive, self.root / 'staged')

    def test_tar_root_directory_marker_does_not_prevent_safe_extraction(self) -> None:
        for root_name in ('.', './'):
            with self.subTest(root_name=root_name):
                archive = self.root / 'root-directory.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    marker = tarfile.TarInfo(root_name)
                    marker.type = tarfile.DIRTYPE
                    output.addfile(marker)
                    member = tarfile.TarInfo('./engine/launcher')
                    member.size = 4
                    output.addfile(member, io.BytesIO(b'safe'))
                target = self.root / ('root-' + str(len(root_name)))
                try:
                    install.extract_archive(archive, target)
                except ModuleSetupError:
                    self.fail('a typed root directory marker must not block safe archive members')
                self.assertEqual((target / 'engine/launcher').read_bytes(), b'safe')

    def test_tar_root_marker_cannot_hide_files_links_or_devices(self) -> None:
        for member_type in (tarfile.REGTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE):
            with self.subTest(member_type=member_type):
                archive = self.root / 'unsafe-root.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    marker = tarfile.TarInfo('.')
                    marker.type = member_type
                    marker.linkname = '/private'
                    output.addfile(marker)
                with self.assertRaisesRegex(ModuleSetupError, 'archive_unsafe'):
                    install.extract_archive(archive, self.root / 'unsafe-root')

    async def test_external_and_unowned_existing_installations_are_preserved(self) -> None:
        target = self.environment.paths['speech']
        target.mkdir(parents=True)
        private = target / 'private.txt'
        private.write_text('keep')
        with patch.object(self.context, 'run', AsyncMock(side_effect=AssertionError('must not run'))):
            outcome = await install.install(self.context, 'speech', True)
        self.assertEqual(outcome.state, 'manual')
        self.assertEqual(private.read_text(), 'keep')

    def test_platform_interpreter_and_source_pins_are_fixed(self) -> None:
        self.assertEqual(install.SOURCE_PINS['stable_audio3'].commit, '77e0398c26f7d22d801ec1bb036307e8b231f741')
        self.assertEqual(install.SOURCE_PINS['speech'].commit, '48b1a0169a28582a8984402f82cf438d3bfa6aca')
        self.assertEqual(install.SOURCE_PINS['singing'].commit, '51383efd921027683c89e5348211d93ff12ac2a8')
        self.assertEqual(install.SOURCE_PINS['kokoro'].commit, 'dfb907a02bba8152ca444717ca5d78747ccb4bec')
        self.assertEqual(install.SOURCE_PINS['chatterbox'].commit, '5de7a54aa4e5e2baadb0182dde554908b48b85c2')
        self.assertEqual(install.SOURCE_PINS['wan22'].commit, '87db56a51758fefb748a359b90a5283bb8ba4837')
        self.assertEqual(install.SOURCE_PINS['rvc'].commit, '81eed5e8f68b6bed1789f682fe78cdd324495afc')
        self.assertNotIn('14B', install.SOURCE_PINS['wan22'].repository)
        self.assertEqual(install.venv_python(Path('/engine'), 'win32'), Path('/engine/.venv/Scripts/python.exe'))

    async def test_ace_verification_decodes_audio_before_publishing_receipt(self) -> None:
        marker = self.root / '.setup/verification/ace_step.json'
        with patch.object(self.context, 'run', AsyncMock()) as run, patch.object(install, 'environment_fingerprint', return_value='a' * 64):
            await install._check_environment(self.context, 'ace_step', self.environment.paths['ace_step'])
        self.assertEqual(run.await_count, 3)
        command = run.await_args_list[2].args[0]
        self.assertIn('load_audio_stereo', command[-2])
        self.assertEqual(command[-1], str(self.environment.paths['ace_step']))
        self.assertTrue(marker.is_file())

    async def test_ace_decoder_failure_revokes_previous_verification(self) -> None:
        marker = self.root / '.setup/verification/ace_step.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('previous verification')
        with patch.object(self.context, 'run', AsyncMock(side_effect=[None, None, ModuleSetupError('command_failed')])), patch.object(install, 'environment_fingerprint', return_value='a' * 64):
            with self.assertRaises(ModuleSetupError):
                await install._check_environment(self.context, 'ace_step', self.environment.paths['ace_step'])
        self.assertFalse(marker.exists())

    def test_ace_decoder_probe_rejects_another_editable_checkout(self) -> None:
        module = ModuleType('acestep.training.dataset_builder_modules.preprocess_audio')
        module.__file__ = str(self.root / 'old-engine/preprocess_audio.py')
        def wrong_decoder(*args: object) -> None:
            raise RuntimeError('Decoder from another checkout was invoked')
        setattr(module, 'load_audio_stereo', wrong_decoder)
        modules = {'torch': ModuleType('torch'), module.__name__: module}
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['-c', str(self.root / 'configured-engine')]):
            with self.assertRaisesRegex(AssertionError, 'ACE training source mismatch'):
                exec(install._ACE_AUDIO_CHECK, {})

    async def test_explicit_external_verification_changes_only_managed_receipt(self) -> None:
        engine = self.root / 'external-speech'
        (engine / 'GPT_SoVITS').mkdir(parents=True)
        (engine / 'api.py').write_text('# source')
        python = engine_python(engine, self.environment.platform)
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        (engine / '.venv/lib/python3.11/site-packages/torch-2.dist-info').mkdir(parents=True)
        paths = dict(self.environment.paths)
        paths['speech'] = engine
        environment = ModuleEnvironment.for_root(self.root, paths=paths, platform='darwin', architecture='arm64')
        context = InstallContext(ModuleJobService(environment), 'b' * 32)
        original = sorted(str(path.relative_to(engine)) for path in engine.rglob('*'))
        with patch.object(context, 'run', AsyncMock()) as run:
            outcome = await install.verify(context, 'speech')
        self.assertEqual(outcome.state, 'manual')
        self.assertTrue(environment_verified(environment, 'speech'))
        self.assertEqual(original, sorted(str(path.relative_to(engine)) for path in engine.rglob('*')))
        self.assertTrue(all('install' not in argument for call in run.call_args_list for argument in call.args[0]))

    async def test_native_ownership_survives_interrupted_weight_download(self) -> None:
        from app import module_native_setup
        target, _ = self.native_fixture()
        script = target / 'tools/model_manager_v2.py'
        script.write_text('# original')
        assets = install.load_manifest()
        install._record_owned(self.context, 'yue2', target, assets.engine.tag)
        with patch.object(module_native_setup, 'setup_native_models', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await install._native_weights(self.context, target, assets, True)
        self.assertTrue(install._owned(self.context, 'yue2', target, assets.engine.tag))
        self.assertEqual(script.read_text(), '# original')


if __name__ == '__main__':
    unittest.main()
