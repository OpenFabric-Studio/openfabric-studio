"""Exercise the maintained ACE initialization patch without models or a server.

The exact upstream helper is from ACE-Step-1.5 ca1e85fe9430179831e6bc6be790c332190a3866;
its MIT terms are retained in fixtures/ace-init/LICENSE-MIT.
"""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import tempfile
from typing import Optional, Tuple
import unittest
from unittest.mock import Mock


SOURCE = 'acestep/core/generation/handler/init_service_downloads.py'
PATCH = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
FIXTURE = Path(__file__).parent / 'fixtures/ace-init/init_service_downloads.py.txt'


class AceModelInitializationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix='ace-init-source-') as directory:
            target = Path(directory) / SOURCE
            target.parent.mkdir(parents=True)
            target.write_bytes(FIXTURE.read_bytes())
            sections = ['diff --git ' + section for section in PATCH.read_text().split('diff --git ')[1:]
                        if section.startswith(f'a/{SOURCE} b/{SOURCE}\n')]
            if sections:
                subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=''.join(sections),
                               text=True, cwd=directory, check=True, capture_output=True)
            parsed = ast.parse(target.read_text())
            cls.source = ast.Module(body=[node for node in parsed.body if isinstance(node, ast.ClassDef)], type_ignores=[])

    def setUp(self) -> None:
        self.models = {'vae', 'Qwen3-Embedding-0.6B', 'acestep-v15-xl-sft'}
        self.main = Mock(return_value=(False, 'main download blocked'))
        self.dit = Mock(return_value=(False, 'selected DiT missing'))
        self.vae = Mock(return_value=(False, 'selected VAE missing'))
        self.vae_present = Mock(return_value=False)
        self.check_model = Mock(side_effect=lambda name, root: name in self.models)
        namespace: dict[str, object] = {
            'Path': Path, 'Optional': Optional, 'Tuple': Tuple, 'logger': Mock(),
            'DEFAULT_VAE_VARIANT': 'official', 'check_main_model_exists': Mock(return_value=False),
            'check_model_exists': self.check_model, 'check_vae_exists': self.vae_present,
            'ensure_main_model': self.main, 'ensure_dit_model': self.dit, 'ensure_vae_model': self.vae,
        }
        exec(compile(self.source, '<patched-ace-init>', 'exec'), namespace)
        factory = namespace['InitServiceDownloadsMixin']
        if not callable(factory):
            raise AssertionError('missing native initialization helper')
        self.ensure = getattr(factory(), '_ensure_models_present')
        self.root = Path('/unused-test-checkpoints')

    def run_precheck(self, model: str = 'acestep-v15-xl-sft', variant: str = 'official') -> object:
        return self.ensure(checkpoint_path=self.root, config_path=model, prefer_source=None, vae_variant=variant)

    def test_complete_selected_xl_does_not_download_default_turbo_or_language_model(self) -> None:
        self.assertIsNone(self.run_precheck())
        self.main.assert_not_called()
        self.dit.assert_not_called()
        self.vae.assert_not_called()

    def test_complete_turbo_does_not_require_unused_language_model(self) -> None:
        self.models.add('acestep-v15-turbo')
        self.assertIsNone(self.run_precheck('acestep-v15-turbo'))
        self.main.assert_not_called()

    def test_missing_shared_components_preserve_download_failure(self) -> None:
        for missing in ('vae', 'Qwen3-Embedding-0.6B'):
            with self.subTest(component=missing):
                self.models.remove(missing)
                self.assertEqual(self.run_precheck(), ('ERROR: Failed to download main model: main download blocked', False))
                self.models.add(missing)
        self.assertEqual(self.main.call_count, 2)

    def test_missing_selected_dit_preserves_failure_without_fetching_defaults(self) -> None:
        self.models.remove('acestep-v15-xl-sft')
        self.assertEqual(self.run_precheck(), ("ERROR: Failed to download DiT model 'acestep-v15-xl-sft': selected DiT missing", False))
        self.main.assert_not_called()
        self.dit.assert_called_once_with('acestep-v15-xl-sft', self.root, prefer_source=None)

    def test_missing_selected_vae_preserves_failure(self) -> None:
        self.assertEqual(self.run_precheck(variant='custom'), ("ERROR: Failed to download VAE variant 'custom': selected VAE missing", False))
        self.main.assert_not_called()
        self.vae.assert_called_once_with('custom', self.root, prefer_source=None)

    def test_existing_selected_vae_is_reused(self) -> None:
        self.vae_present.return_value = True
        self.assertIsNone(self.run_precheck(variant='custom'))
        self.main.assert_not_called()
        self.vae.assert_not_called()
