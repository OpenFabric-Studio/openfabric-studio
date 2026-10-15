from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch, sentinel
import zipfile

from app import module_music_models as models
from app.module_catalog import ModuleEnvironment
from app.module_install import DownloadArtifact
from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError


def tensor() -> bytes:
    header = json.dumps({'__metadata__': None, 'weight': {'dtype': 'BF16', 'shape': [2], 'data_offsets': [0, 4]}}).encode()
    return struct.pack('<Q', len(header)) + header + b'\0' * 4


def npy(descr: str = '<f4', shape: tuple[int, ...] = (1,), data: bytes = b'\0' * 4,
        version: tuple[int, int] = (1, 0), header_override: str | None = None) -> bytes:
    header = (header_override or repr({'descr': descr, 'fortran_order': False, 'shape': shape})).encode()
    length_size = 2 if version == (1, 0) else 4
    padding = (-(8 + length_size + len(header) + 1)) % 64
    header += b' ' * padding + b'\n'
    return b'\x93NUMPY' + bytes(version) + len(header).to_bytes(length_size, 'little') + header + data


def npz(content: bytes | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('weight.npy', date_time=(2000, 1, 1, 0, 0, 0)), npy() if content is None else content)
    return output.getvalue()


class MusicModelSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = ModuleEnvironment.for_root(self.root / 'managed', platform='darwin', architecture='arm64')
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        self.detail = patch.object(self.context, 'update_detail', create=True)
        self.detail.start()
        self.addCleanup(self.detail.stop)

    def put(self, engine: str, relative: str, content: bytes) -> Path:
        path = self.environment.paths[engine] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def artifact(self, relative: str, content: bytes) -> models.ModelArtifact:
        return models.ModelArtifact(path=relative, url='https://huggingface.co/test/model/resolve/' + 'a' * 40 + '/' + Path(relative).name,
            sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))

    def test_training_manifest_pins_base_encoder_and_shared_text_weights(self) -> None:
        bundles = models._manifest().bundles
        expected = {
            'stable-small-training': ('dit_sm-music-base_f16.npz', 918797526, '4a8ee6dc2f5ef3fbaa595cb337f1c0718c0fbc1916071f929f78beda4335e231', 'same_s_encoder_f32.npz', 214946620, 'a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c'),
            'stable-medium-training': ('dit_medium-base_f16.npz', 2907276556, 'cb829e33d7e64fe07cd190acb1231a9716be11e58eacc04e835e0652dd888931', 'same_l_encoder_f32.npz', 1704313504, 'c5caafc6bd29fc3d4cb7a08b0d3725041fa2100711f86a4f05d2638c532871d2'),
        }
        for bundle, (base, size, digest, encoder, encoder_size, encoder_digest) in expected.items():
            with self.subTest(bundle=bundle):
                self.assertIn(bundle, bundles)
                files = {Path(item.path).name: item for item in bundles[bundle]}
                self.assertEqual(set(files), {'t5gemma_f16.npz', base, encoder})
                self.assertEqual((files[base].bytes, files[base].sha256), (size, digest))
                self.assertEqual((files[encoder].bytes, files[encoder].sha256), (encoder_size, encoder_digest))
                self.assertTrue(all('/resolve/08c64b96b1e59942aade69759f60fb88c58c90c4/MLX/' in item.url for item in files.values()))

    async def test_training_downloads_only_missing_selected_base_and_encoder(self) -> None:
        self.assertTrue(hasattr(models, 'setup_stable_training_models'))
        shared = self.artifact('optimized/mlx/models/mlx/t5gemma_f16.npz', npz())
        base = self.artifact('optimized/mlx/models/mlx/dit_sm-music-base_f16.npz', npz())
        encoder = self.artifact('optimized/mlx/models/mlx/same_s_encoder_f32.npz', npz())
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small-training': [shared, base, encoder]})
        self.put('stable_audio3', shared.path, npz())
        inference = self.put('stable_audio3', 'optimized/mlx/models/mlx/dit_sm-music_f16.npz', npz(npy(data=b'abcd')))
        original = inference.read_bytes()
        seen: list[str] = []
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            seen.append(destination.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download):
            result = await models.setup_stable_training_models(self.context, 'small-music')
            await models.setup_stable_training_models(self.context, 'small-music')
        self.assertEqual(result.state, 'verified')
        self.assertEqual(seen, [Path(base.path).name, Path(encoder.path).name])
        self.assertEqual(inference.read_bytes(), original)

    async def test_training_preserves_invalid_existing_files_and_rejects_symlink_escape(self) -> None:
        self.assertTrue(hasattr(models, 'setup_stable_training_models'))
        existing = self.put('stable_audio3', 'optimized/mlx/models/mlx/dit_sm-music-base_f16.npz', b'user original')
        with patch('app.module_install.download_artifact', new=AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await models.setup_stable_training_models(self.context, 'small-music')
            self.assertEqual(existing.read_bytes(), b'user original')
            existing.unlink()
            outside = self.root / 'outside.npz'
            outside.write_bytes(npz())
            existing.symlink_to(outside)
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await models.setup_stable_training_models(self.context, 'small-music')
        download.assert_not_awaited()

    async def test_download_flag_is_respected(self) -> None:
        with patch('app.module_install.download_artifact', AsyncMock()) as download:
            result = await models.setup_music_models(self.context, 'stable_audio3', False)
        self.assertEqual(result.state, 'manual')
        download.assert_not_awaited()

    async def test_existing_medium_is_reused_without_small_downloads(self) -> None:
        for name in ('t5gemma_f16.npz', 'dit_medium_f16.npz', 'same_l_decoder_f32.npz'):
            self.put('stable_audio3', 'optimized/mlx/models/mlx/' + name, npz())
        with patch('app.module_install.download_artifact', AsyncMock()) as download:
            result = await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertEqual(result.state, 'verified')
        self.assertTrue(models.music_models_present(self.environment, 'stable_audio3'))
        self.assertEqual(models.model_download_bytes(self.environment, 'stable_audio3'), 0)
        download.assert_not_awaited()

    async def test_missing_weights_are_verified_in_cache_before_exclusive_publication(self) -> None:
        names = ('t5gemma_f16.npz', 'dit_sm-music_f16.npz', 'same_s_decoder_f32.npz')
        artifacts = [self.artifact('optimized/mlx/models/mlx/' + name, npz()) for name in names]
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small': artifacts})
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            self.assertTrue(destination.is_relative_to(context.environment.root))
            self.assertFalse((context.environment.paths['stable_audio3'] / 'optimized/mlx/models/mlx' / destination.name).exists())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download):
            self.assertEqual(models.model_download_bytes(self.environment, 'stable_audio3'), sum(item.bytes for item in artifacts))
            result = await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertEqual(result.state, 'verified')
        for artifact in artifacts:
            self.assertEqual((self.environment.paths['stable_audio3'] / artifact.path).read_bytes(), npz())

    async def test_invalid_existing_file_is_preserved_before_any_download(self) -> None:
        existing = self.put('stable_audio3', 'optimized/mlx/models/mlx/t5gemma_f16.npz', b'user data')
        with patch('app.module_install.download_artifact', AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertEqual(existing.read_bytes(), b'user data')
        download.assert_not_awaited()

    async def test_model_directory_symlink_escape_is_rejected(self) -> None:
        outside = self.root / 'outside'
        outside.mkdir()
        engine = self.environment.paths['stable_audio3']
        engine.mkdir(parents=True)
        (engine / 'optimized').symlink_to(outside, target_is_directory=True)
        with patch('app.module_install.download_artifact', AsyncMock()) as download:
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertEqual(list(outside.iterdir()), [])
        download.assert_not_awaited()

    async def test_external_engine_can_receive_missing_models_without_source_mutation(self) -> None:
        external = self.root / 'external'
        external.mkdir()
        source = external / 'source.py'
        source.write_text('# user source\n')
        environment = replace(self.environment, paths={**self.environment.paths, 'stable_audio3': external})
        context = InstallContext(ModuleJobService(environment), 'b' * 32)
        content = npz()
        artifact = self.artifact('optimized/mlx/models/mlx/t5gemma_f16.npz', content)
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small': [artifact]})
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            return destination
        with patch.object(context, 'update_detail', create=True), patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download):
            result = await models.setup_music_models(context, 'stable_audio3', True)
        self.assertEqual(result.state, 'verified')
        self.assertEqual(source.read_text(), '# user source\n')
        self.assertEqual((external / artifact.path).read_bytes(), content)

    async def test_cancelled_download_never_publishes_partial_final(self) -> None:
        artifact = self.artifact('optimized/mlx/models/mlx/t5gemma_f16.npz', npz())
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small': [artifact]})
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.with_suffix('.part').write_bytes(b'partial')
            raise asyncio.CancelledError()
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download):
            with self.assertRaises(asyncio.CancelledError):
                await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertFalse((self.environment.paths['stable_audio3'] / artifact.path).exists())

    async def test_file_created_during_download_is_never_overwritten(self) -> None:
        artifact = self.artifact('optimized/mlx/models/mlx/t5gemma_f16.npz', npz())
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small': [artifact]})
        target = self.environment.paths['stable_audio3'] / artifact.path
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            target.parent.mkdir(parents=True)
            target.write_bytes(b'created by another contributor')
            return destination
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download):
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await models.setup_music_models(self.context, 'stable_audio3', True)
        self.assertEqual(target.read_bytes(), b'created by another contributor')
        self.assertEqual(list(target.parent.glob('*.part')), [])

    async def test_cancel_waits_for_publication_thread_to_finish(self) -> None:
        artifact = self.artifact('optimized/mlx/models/mlx/t5gemma_f16.npz', npz())
        manifest = models.ModelManifest(schema_version=1, bundles={'stable-small': [artifact]})
        entered, release = threading.Event(), threading.Event()
        publish = models._publish
        def blocked(source: Path, root: Path, relative: str) -> None:
            entered.set()
            release.wait(5)
            publish(source, root, relative)
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download), patch.object(models, '_publish', side_effect=blocked):
            task = asyncio.create_task(models.setup_music_models(self.context, 'stable_audio3', True))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(models._valid_file(self.environment.paths['stable_audio3'] / artifact.path))

    async def test_heart_conversion_validates_complete_components_before_publication(self) -> None:
        artifacts = [self.artifact('ckpt/HeartMuLa-oss-3B/config.json', b'{"model_type":"heartmula"}'),
            self.artifact('ckpt/HeartCodec-oss-20260123/config.json', b'{"model_type":"heartcodec"}'),
            self.artifact('ckpt/tokenizer.json', b'{"model":{}}')]
        manifest = models.ModelManifest(schema_version=1, bundles={'heartmula': artifacts[:1], 'heartcodec': artifacts[1:2], 'heart-tokenizer': artifacts[2:]})
        contents = {artifact.sha256: content for artifact, content in zip(artifacts, (b'{"model_type":"heartmula"}', b'{"model_type":"heartcodec"}', b'{"model":{}}'))}
        async def download(artifact: DownloadArtifact, destination: Path, context: InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents[artifact.sha256])
            return destination
        async def convert(argv: list[str], **_: object) -> None:
            self.assertEqual(argv[1:3], ['-m', 'heartlib_mlx.utils.convert'])
            self.assertEqual(argv[-2:], ['--dtype', 'bfloat16'])
            destination = Path(argv[argv.index('--dst') + 1])
            self.assertTrue(destination.is_relative_to(self.context.workspace))
            destination.mkdir(parents=True)
            (destination / 'config.json').write_text('{"model_type":"converted"}')
            (destination / 'model.safetensors').write_bytes(tensor())
        with patch.object(models, '_manifest', return_value=manifest), patch('app.module_install.download_artifact', side_effect=download), patch.object(self.context, 'run', side_effect=convert) as run:
            result = await models.setup_music_models(self.context, 'heartmula', True)
        self.assertEqual(result.state, 'verified')
        self.assertEqual(run.await_count, 2)
        self.assertTrue(models.music_models_present(self.environment, 'heartmula'))

    async def test_failed_second_heart_component_reuses_completed_first_component_on_retry(self) -> None:
        tokenizer = self.artifact('ckpt/tokenizer.json', b'{"model":{}}')
        manifest = models.ModelManifest(schema_version=1, bundles={'heart-tokenizer': [tokenizer]})
        self.put('heartmula', tokenizer.path, b'{"model":{}}')
        converted: list[str] = []
        fail_codec = True
        async def convert(argv: list[str], **_: object) -> None:
            kind = argv[argv.index('--model-type') + 1]
            converted.append(kind)
            destination = Path(argv[argv.index('--dst') + 1])
            destination.mkdir(parents=True)
            (destination / 'config.json').write_text('{"model_type":"converted"}')
            (destination / 'model.safetensors').write_bytes(b'partial' if kind == 'heartcodec' and fail_codec else tensor())
        with patch.object(models, '_manifest', return_value=manifest), patch.object(self.context, 'run', side_effect=convert):
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await models.setup_music_models(self.context, 'heartmula', True)
            engine = self.environment.paths['heartmula']
            self.assertTrue(models._all_valid(engine, ('ckpt-mlx/heartmula/config.json', 'ckpt-mlx/heartmula/model.safetensors')))
            self.assertFalse((engine / 'ckpt-mlx/heartcodec').exists())
            self.assertFalse(models.music_models_present(self.environment, 'heartmula'))
            self.assertEqual(list(self.context.workspace.glob('heart-convert-*')), [])
            fail_codec = False
            result = await models.setup_music_models(self.context, 'heartmula', True)
        self.assertEqual(converted, ['heartmula', 'heartcodec', 'heartcodec'])
        self.assertEqual(result.state, 'verified')
        self.assertTrue(models.music_models_present(self.environment, 'heartmula'))

    async def test_failed_heart_conversion_leaves_no_final_converted_files(self) -> None:
        tokenizer = self.artifact('ckpt/tokenizer.json', b'{"model":{}}')
        manifest = models.ModelManifest(schema_version=1, bundles={'heart-tokenizer': [tokenizer]})
        self.put('heartmula', tokenizer.path, b'{"model":{}}')
        async def convert(argv: list[str], **_: object) -> None:
            destination = Path(argv[argv.index('--dst') + 1])
            destination.mkdir(parents=True)
            (destination / 'model.safetensors').write_bytes(b'partial')
        with patch.object(models, '_manifest', return_value=manifest), patch.object(self.context, 'run', side_effect=convert):
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await models.setup_music_models(self.context, 'heartmula', True)
        self.assertFalse((self.environment.paths['heartmula'] / 'ckpt-mlx').exists())
        self.assertEqual(list(self.context.workspace.glob('heart-convert-*')), [])

    async def test_unverified_converter_drain_retains_owned_scratch(self) -> None:
        tokenizer = self.artifact('ckpt/tokenizer.json', b'{"model":{}}')
        manifest = models.ModelManifest(schema_version=1, bundles={'heart-tokenizer': [tokenizer]})
        self.put('heartmula', tokenizer.path, b'{"model":{}}')
        async def convert(argv: list[str], **_: object) -> None:
            destination = Path(argv[argv.index('--dst') + 1])
            destination.mkdir(parents=True)
            (destination / 'model.safetensors').write_bytes(b'worker still owns this')
            raise ModuleSetupError('worker_unverified')
        with patch.object(models, '_manifest', return_value=manifest), patch.object(self.context, 'run', side_effect=convert), patch.dict(self.context.service.processes, {self.context.identifier: sentinel.worker}):
            with self.assertRaisesRegex(ModuleSetupError, 'worker_unverified'):
                await models.setup_music_models(self.context, 'heartmula', True)
        self.assertEqual(len(list(self.context.workspace.glob('heart-convert-*'))), 1)
        self.assertFalse((self.environment.paths['heartmula'] / 'ckpt-mlx').exists())

    def test_existing_originals_still_reserve_conversion_space(self) -> None:
        artifact = self.artifact('ckpt/HeartMuLa-oss-3B/model.safetensors', tensor())
        manifest = models.ModelManifest(schema_version=1, bundles={'heartmula': [artifact]})
        self.put('heartmula', artifact.path, tensor())
        with patch.object(models, '_manifest', return_value=manifest):
            self.assertEqual(models.model_download_bytes(self.environment, 'heartmula'), 0)
            self.assertEqual(models.conversion_required_bytes(self.environment, 'heartmula'), 2 * artifact.bytes)

    def test_completed_heart_component_does_not_redownload_its_originals(self) -> None:
        self.put('heartmula', 'ckpt-mlx/heartmula/config.json', b'{"model_type":"heartmula"}')
        self.put('heartmula', 'ckpt-mlx/heartmula/model.safetensors', tensor())
        paths = [item.path for item in models._required_artifacts(self.environment, 'heartmula')]
        self.assertTrue(any('/HeartCodec-oss-20260123/' in path for path in paths))
        self.assertFalse(any('/HeartMuLa-oss-3B/' in path for path in paths))

    async def test_unreviewed_ace_variant_is_preserved_and_reported_manual(self) -> None:
        from app import config
        with patch.object(config, 'ACE_STEP_DIT_MODEL', '../outside'), patch('app.module_install.download_artifact', AsyncMock()) as download:
            result = await models.setup_music_models(self.context, 'ace_step', True)
            self.assertIsNone(models.model_download_bytes(self.environment, 'ace_step'))
        self.assertEqual(result.state, 'manual')
        download.assert_not_awaited()

    async def test_missing_ace_checkpoint_code_uses_installed_runtime_source(self) -> None:
        from app import config
        content = b'# version matched to the installed engine\n'
        self.put('ace_step', 'acestep/models/xl_sft/configuration_acestep_v15.py', content)
        artifact = self.artifact('checkpoints/acestep-v15-xl-sft/configuration_acestep_v15.py', b'# newer remote code\n')
        manifest = models.ModelManifest(schema_version=1, bundles={'ace-xl': [artifact]})
        with patch.object(models, '_manifest', return_value=manifest), patch.object(config, 'ACE_STEP_DIT_MODEL', 'acestep-v15-xl-sft'), patch.object(config, 'ACE_STEP_LM_MODEL', 'acestep-5Hz-lm-4B'), patch('app.module_install.download_artifact', AsyncMock()) as download:
            self.assertEqual(models.model_download_bytes(self.environment, 'ace_step'), 0)
            result = await models.setup_music_models(self.context, 'ace_step', True)
        self.assertEqual(result.state, 'verified')
        self.assertEqual((self.environment.paths['ace_step'] / artifact.path).read_bytes(), content)
        download.assert_not_awaited()

    def test_truncated_tensor_does_not_count_as_existing_weights(self) -> None:
        path = self.root / 'model.safetensors'
        path.write_bytes(tensor()[:-1])
        self.assertFalse(models._valid_file(path))

    def test_mlx_null_metadata_is_valid_but_null_tensor_is_not(self) -> None:
        path = self.root / 'model.safetensors'
        path.write_bytes(tensor())
        self.assertTrue(models._valid_file(path))
        header = json.dumps({'__metadata__': None, 'weight': {'dtype': 'BF16', 'shape': [2], 'data_offsets': [0, 4]}, 'missing': None}).encode()
        path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0' * 4)
        self.assertFalse(models._valid_file(path))

    def test_npy_magic_alone_does_not_establish_complete_existing_weights(self) -> None:
        path = self.root / 'model.npz'
        path.write_bytes(npz(b'\x93NUMPY\x01\x00test'))
        self.assertFalse(models._valid_file(path))

    def test_npz_rejects_truncated_payload_object_dtype_and_invalid_headers(self) -> None:
        path = self.root / 'model.npz'
        invalid = (
            npy(shape=(2,)),
            npy(descr='|O', data=b'pickle!!'),
            npy(descr='<f3', data=b'abc'),
            npy(shape=(-1,)),
            npy(header_override="{'descr': __import__('os'), 'fortran_order': False, 'shape': (1,)}"),
        )
        for payload in invalid:
            with self.subTest(payload=payload[:12]):
                path.write_bytes(npz(payload))
                self.assertFalse(models._valid_file(path))

    def test_supported_npy_versions_and_tokenizer_storage_are_structurally_valid(self) -> None:
        path = self.root / 'model.npz'
        valid = (
            npy(version=(1, 0)), npy(version=(2, 0)), npy(version=(3, 0)),
            npy(descr='|u1', shape=(4,), data=b'abcd'),
            npy(descr='<U1', shape=(), data=b'a\0\0\0'),
        )
        for payload in valid:
            with self.subTest(payload=payload[:12]):
                path.write_bytes(npz(payload))
                self.assertTrue(models._valid_file(path))

    def test_disk_directories_include_all_nested_heart_destinations_once(self) -> None:
        root = self.environment.paths['heartmula'].resolve()
        directories = models.model_directories(self.environment, 'heartmula')
        self.assertEqual(len(directories), len(set(directories)))
        self.assertEqual(set(directories), {root, root / 'ckpt', root / 'ckpt/HeartMuLa-oss-3B',
            root / 'ckpt/HeartCodec-oss-20260123', root / 'ckpt-mlx/heartmula', root / 'ckpt-mlx/heartcodec'})
        self.assertFalse(root.exists())

    def test_unreviewed_ace_disk_directories_fall_back_to_engine_root(self) -> None:
        from app import config
        with patch.object(config, 'ACE_STEP_DIT_MODEL', '../outside'):
            self.assertEqual(models.model_directories(self.environment, 'ace_step'), (self.environment.paths['ace_step'].resolve(),))

    def test_ace_selected_override_never_reports_default_only_as_ready(self) -> None:
        from app import config
        with patch.object(config, 'ACE_STEP_DIT_MODEL', 'acestep-v15-xl-sft'), patch.object(config, 'ACE_STEP_LM_MODEL', 'acestep-5Hz-lm-4B'):
            selected = models._required_artifacts(self.environment, 'ace_step')
        paths = [item.path for item in selected]
        self.assertTrue(any('/acestep-v15-xl-sft/' in path for path in paths))
        self.assertTrue(any('/acestep-5Hz-lm-4B/' in path for path in paths))
        self.assertFalse(any('/acestep-v15-turbo/' in path or '/acestep-5Hz-lm-1.7B/' in path for path in paths))
