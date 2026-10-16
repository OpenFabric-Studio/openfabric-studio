"""Ensure cached native MLX base weights cannot bypass active ACE adapters."""
from __future__ import annotations

import ast
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock


PATCH = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
FIXTURE = Path(__file__).parent / 'fixtures/ace-generation/service_generate_execute.py.txt'
SOURCE = 'acestep/core/generation/handler/service_generate_execute.py'


class AceLoraBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix='ace-generation-source-') as directory:
            root = Path(directory)
            target = root / SOURCE
            target.parent.mkdir(parents=True)
            target.write_bytes(FIXTURE.read_bytes())
            sections = ['diff --git ' + section for section in PATCH.read_text().split('diff --git ')[1:]
                        if section.startswith(f'a/{SOURCE} b/{SOURCE}\n')]
            if sections:
                subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=''.join(sections),
                               text=True, cwd=root, check=True, capture_output=True)
            cls.source = ast.parse(target.read_text())

    def setUp(self) -> None:
        self.logger = Mock()
        tensor = Mock(shape=(1, 4, 4), device='cpu', dtype='float32')
        self.model = Mock()
        self.model.prepare_condition.return_value = ('encoded', 'mask', 'context')
        self.model.generate_audio.return_value = {'backend': 'pytorch'}
        self.host = Mock(
            use_mlx_dit=True, mlx_decoder=object(), device='cpu', lora_loaded=True, use_lora=True,
            model=self.model, silence_latent=tensor,
        )
        self.host._load_model_context.return_value = nullcontext()
        self.host._resolve_service_dcw_enabled.return_value = False
        self.host._mlx_run_diffusion.return_value = {'backend': 'mlx'}
        self.payload: dict[str, object] = {key: tensor for key in (
            'text_hidden_states', 'text_attention_mask', 'lyric_hidden_states', 'lyric_attention_mask',
            'refer_audio_acoustic_hidden_states_packed', 'refer_audio_order_mask', 'src_latents',
            'chunk_mask', 'is_covers',
        )}
        self.payload.update({'precomputed_lm_hints_25Hz': None, 'non_cover_text_hidden_states': None,
                             'non_cover_text_attention_masks': None})
        namespace: dict[str, object] = {
            'torch': Mock(inference_mode=Mock(return_value=nullcontext())), 'logger': self.logger,
        }
        methods = [method for node in self.source.body if isinstance(node, ast.ClassDef)
                   for method in node.body if isinstance(method, ast.FunctionDef)
                   and method.name == '_execute_service_generate_diffusion']
        self.assertEqual(len(methods), 1)
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<patched-ace-generation>', 'exec'), namespace)
        self.execute = namespace['_execute_service_generate_diffusion']

    def generate(self) -> object:
        if not callable(self.execute):
            raise AssertionError('Missing diffusion execution function')
        result = self.execute(self.host, self.payload, {}, 123, 'ode', 1.0, 1.0)
        return result[0]

    def test_active_adapter_uses_live_pytorch_decoder_instead_of_cached_mlx_base(self) -> None:
        self.assertEqual(self.generate(), {'backend': 'pytorch'})
        self.model.generate_audio.assert_called_once_with(dcw_enabled=False)
        self.host._mlx_run_diffusion.assert_not_called()
        self.assertIn('DiT backend: PyTorch (cpu)', self.logger.info.call_args_list[0].args[0])

    def test_disabling_adapter_restores_existing_mlx_base_path_without_reloading(self) -> None:
        decoder = self.host.mlx_decoder
        self.assertEqual(self.generate(), {'backend': 'pytorch'})
        self.host.use_lora = False
        self.assertEqual(self.generate(), {'backend': 'mlx'})
        self.assertIs(self.host.mlx_decoder, decoder)
        self.assertTrue(self.host.use_mlx_dit)
        self.host._mlx_run_diffusion.assert_called_once()

    def test_unloaded_adapter_uses_mlx_base_path(self) -> None:
        self.host.lora_loaded = False
        self.assertEqual(self.generate(), {'backend': 'mlx'})
        self.model.generate_audio.assert_not_called()
        self.assertIn('DiT backend: MLX (native)', self.logger.info.call_args_list[0].args[0])

    def test_older_host_without_adapter_state_retains_mlx_base_path(self) -> None:
        del self.host.lora_loaded
        del self.host.use_lora
        self.assertEqual(self.generate(), {'backend': 'mlx'})

    def test_missing_mlx_decoder_keeps_existing_pytorch_path(self) -> None:
        self.host.mlx_decoder = None
        self.assertEqual(self.generate(), {'backend': 'pytorch'})
        self.host._mlx_run_diffusion.assert_not_called()

    def test_failed_mlx_base_generation_keeps_existing_pytorch_fallback(self) -> None:
        self.host.use_lora = False
        self.host._mlx_run_diffusion.side_effect = RuntimeError('injected MLX failure')
        self.assertEqual(self.generate(), {'backend': 'pytorch'})
        self.model.generate_audio.assert_called_once_with(dcw_enabled=False)
        self.logger.warning.assert_called_once()
