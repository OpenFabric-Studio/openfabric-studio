"""Exercise the actual pinned native init class without importing torch or models."""
from __future__ import annotations

import ast
from pathlib import Path
import tempfile
from types import ModuleType
from typing import Optional, Tuple
import unittest
from unittest.mock import Mock, patch

from scripts.ace_conditioning_server import install_offline_guards, MINIMUM_FILES


class AceOfflineConditioningTests(unittest.TestCase):
    def test_base_only_init_and_missing_assets_never_call_either_download_backend(self) -> None:
        fixture = Path(__file__).parent / 'fixtures/ace-init/init_service_downloads.py.txt'
        parsed = ast.parse(fixture.read_text())
        source = ast.Module(body=[node for node in parsed.body if isinstance(node, ast.ClassDef)], type_ignores=[])
        download = Mock(return_value=(False, 'network must not run'))
        namespace: dict[str, object] = {'Path': Path, 'Optional': Optional, 'Tuple': Tuple, 'logger': Mock(),
            'DEFAULT_VAE_VARIANT': 'official', 'check_main_model_exists': lambda path: False,
            'check_model_exists': lambda name, path: True, 'check_vae_exists': lambda name, path: True,
            'ensure_main_model': download, 'ensure_dit_model': download, 'ensure_vae_model': download}
        exec(compile(source, '<pinned-ace-init>', 'exec'), namespace)
        factory = namespace['InitServiceDownloadsMixin']
        self.assertIsInstance(factory, type)
        if not isinstance(factory, type):
            raise AssertionError('Missing native mixin')
        core = ModuleType('acestep.model_downloader')
        api = ModuleType('acestep.api.model_download')
        mixins = ModuleType('acestep.core.generation.handler.init_service_downloads')
        setattr(mixins, 'InitServiceDownloadsMixin', factory)
        for module, names in ((core, ('_smart_download', '_download_from_huggingface_internal',
                '_download_from_modelscope_internal', 'ensure_main_model', 'ensure_dit_model', 'ensure_lm_model',
                'ensure_vae_model')), (api, ('ensure_model_downloaded', 'can_access_google',
                'download_from_huggingface', 'download_from_modelscope'))):
            for name in names:
                setattr(module, name, download)
        modules = {module.__name__: module for module in (core, api, mixins)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            required = MINIMUM_FILES
            for relative in required:
                path = root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            kwargs = {'checkpoint_path': root / 'checkpoints', 'config_path': 'acestep-v15-base',
                'prefer_source': 'modelscope', 'vae_variant': 'official'}
            # The actual pristine source requests the whole default bundle despite a complete base install.
            self.assertIsNotNone(getattr(factory(), '_ensure_models_present')(**kwargs))
            download.assert_called_once(); download.reset_mock()
            with patch('scripts.ace_conditioning_server.importlib.import_module', side_effect=modules.__getitem__):
                install_offline_guards(root, required)
            self.assertIsNone(getattr(factory(), '_ensure_models_present')(**kwargs))
            self.assertEqual(getattr(api, 'ensure_model_downloaded')('acestep-v15-base', str(root / 'checkpoints')),
                str(root / 'checkpoints/acestep-v15-base'))
            (root / required[1]).unlink()
            self.assertEqual(getattr(factory(), '_ensure_models_present')(**kwargs)[1], False)
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                getattr(api, 'ensure_model_downloaded')('acestep-v15-base', str(root / 'checkpoints'))
            for module, name in ((api, 'download_from_modelscope'), (api, 'download_from_huggingface'),
                    (core, '_smart_download'), (core, '_download_from_modelscope_internal'),
                    (core, 'ensure_main_model'), (core, 'ensure_lm_model')):
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'offline'):
                    getattr(module, name)('anything')
            download.assert_not_called()
