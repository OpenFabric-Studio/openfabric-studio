"""Exercise native ACE numerical failures before checkpoint publication."""
from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import nullcontext
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = 'acestep/training/trainer.py'
PATCH = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
FIXTURE = Path(__file__).parent / 'fixtures/ace-training/trainer.py.txt'


class _Loss:
    def __truediv__(self, divisor: int) -> _Loss:
        return self

    def item(self) -> float:
        return 1.0

    def backward(self) -> None:
        pass


class AceTrainingNumericsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix='ace-numerical-source-') as directory:
            target = Path(directory) / SOURCE
            target.parent.mkdir(parents=True)
            target.write_bytes(FIXTURE.read_bytes())
            sections = ['diff --git ' + section for section in PATCH.read_text().split('diff --git ')[1:]
                        if section.startswith(f'a/{SOURCE} b/{SOURCE}\n')]
            if sections:
                subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=''.join(sections),
                               text=True, cwd=directory, check=True, capture_output=True)
            cls.source = ast.parse(target.read_text())

    def setUp(self) -> None:
        self.torch = Mock(float32='float32', float16='float16', bfloat16='bfloat16')
        self.save_lora = Mock()
        self.save_lokr = Mock()
        self.save_checkpoint = Mock()
        self.save_lokr_checkpoint = Mock()
        self.optimizer = Mock()
        self.scheduler = Mock(get_last_lr=Mock(return_value=[0.0001]))
        self.namespace: dict[str, object] = {
            'torch': self.torch, 'time': time, 'os': os, 'math': math, 'nullcontext': nullcontext,
            'optimizer': self.optimizer, 'scheduler': self.scheduler, 'logger': Mock(),
            'device_type': 'cpu', 'precision': '32-true',
            'trainable_params': [], 'param_name_lookup': {},
            'save_lora_weights': self.save_lora, 'save_lokr_weights': self.save_lokr,
            'save_training_checkpoint': self.save_checkpoint,
            'save_lokr_training_checkpoint': self.save_lokr_checkpoint,
            '_unwrap_stale_fabric_decoder': Mock(),
        }
        exec(compile(self.source, '<patched-ace-numerics>', 'exec'), self.namespace)

    def _call(self, name: str, *args: object) -> object:
        function = self.namespace[name]
        if not callable(function):
            raise AssertionError(f'missing numerical fixture function {name}')
        return function(*args)

    def test_mps_uses_float32_computation_and_fabric_without_loss_scaling(self) -> None:
        self.assertEqual(self._call('_select_compute_dtype', 'mps'), 'float32')
        self.assertEqual(self._call('_select_fabric_precision', 'mps'), '32-true')
        for device in ('cuda', 'xpu'):
            self.assertEqual(self._call('_select_compute_dtype', device), 'bfloat16')
            self.assertEqual(self._call('_select_fabric_precision', device), 'bf16-mixed')

    def test_mps_does_not_enter_an_unsupported_float32_autocast_context(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            with self.subTest(adapter=adapter):
                module = self._call(f'Preprocessed{adapter}Module')
                setattr(module, 'device_type', 'mps')
                setattr(module, 'dtype', 'float32')
                getattr(module, 'training_step')(None)
                self.torch.autocast.assert_not_called()

    def _loop(self, adapter: str, method: str, batches: int, accumulation: int,
              nonfinite: bool, device: str = 'cpu') -> Iterator[tuple[int, float, str]]:
        self.namespace['train_loader'] = list(range(batches))
        self.namespace['_count_nonfinite_grads'] = Mock(return_value=(int(nonfinite), 1))
        self.namespace['_count_nonfinite_grads_detailed'] = Mock(return_value=(int(nonfinite), 1, []))
        trainer = self._call(f'{adapter}Trainer')
        setattr(trainer, 'training_config', SimpleNamespace(
            max_epochs=1, gradient_accumulation_steps=accumulation, max_grad_norm=1.0,
            log_every_n_steps=1, output_dir='/unused-scratch-output', save_every_n_epochs=1,
        ))
        setattr(trainer, 'module', SimpleNamespace(
            training_step=Mock(return_value=_Loss()), model=Mock(), lycoris_net=Mock(),
            training_losses=[1.0], dtype='float32', device_type=device,
        ))
        setattr(trainer, 'fabric', Mock())
        setattr(trainer, 'lokr_config', Mock())
        setattr(trainer, 'run_metadata', {})
        self.trainer = trainer
        return getattr(trainer, method)(None)

    def _assert_not_published(self) -> None:
        self.save_lora.assert_not_called()
        self.save_lokr.assert_not_called()
        self.save_checkpoint.assert_not_called()
        self.save_lokr_checkpoint.assert_not_called()

    def test_nonfinite_full_and_remainder_updates_fail_before_publishing(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            for method in ('_train_with_fabric', '_train_basic'):
                for accumulation in (1, 2):
                    with self.subTest(adapter=adapter, method=method, accumulation=accumulation):
                        self.setUp()
                        rows = []
                        with self.assertRaisesRegex(FloatingPointError, 'Non-finite gradients'):
                            rows.extend(self._loop(adapter, method, 1, accumulation, True))
                        self.optimizer.step.assert_not_called()
                        self.scheduler.step.assert_not_called()
                        self._assert_not_published()
                        self.assertFalse(any('Training complete' in status for _, _, status in rows))

    def test_empty_epoch_cannot_publish_an_untrained_adapter(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            for method in ('_train_with_fabric', '_train_basic'):
                with self.subTest(adapter=adapter, method=method):
                    self.setUp()
                    with self.assertRaisesRegex(RuntimeError, 'No optimizer updates'):
                        list(self._loop(adapter, method, 0, 1, False))
                    self._assert_not_published()

    def test_native_wrapper_reports_numerical_failure_and_never_completion(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            for method in ('_train_with_fabric', '_train_basic'):
                with self.subTest(adapter=adapter, method=method):
                    self.setUp()
                    loop = self._loop(adapter, method, 1, 1, True)
                    setattr(self.trainer, '_smoke_loop', Mock(return_value=loop))
                    rows = list(getattr(self.trainer, 'train_from_preprocessed')(None))
                    self.assertIn('❌ Training failed: Non-finite gradients', rows[-1][2])
                    self.assertFalse(any('Training complete' in status for _, _, status in rows))
                    self.assertFalse(getattr(self.trainer, 'is_training'))
                    self._assert_not_published()

    def test_finite_full_and_remainder_updates_complete_one_step(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            for method in ('_train_with_fabric', '_train_basic'):
                for accumulation in (1, 2):
                    with self.subTest(adapter=adapter, method=method, accumulation=accumulation):
                        self.setUp()
                        rows = list(self._loop(adapter, method, 1, accumulation, False))
                        self.optimizer.step.assert_called_once()
                        self.scheduler.step.assert_called_once()
                        self.assertEqual(rows[-1][0], 1)
                        self.assertTrue(math.isfinite(rows[-1][1]))
                        self.assertIn('Training complete', rows[-1][2])
                        saved = self.save_lora if adapter == 'LoRA' else self.save_lokr
                        self.assertEqual(saved.call_count, 2 if adapter == 'LoRA' and method == '_train_basic' else 1)
                        self.assertEqual(saved.call_args.args[1], '/unused-scratch-output/final')

    def test_remainder_update_counts_even_when_its_loss_is_not_logged(self) -> None:
        for adapter in ('LoRA', 'LoKR'):
            for method in ('_train_with_fabric', '_train_basic'):
                with self.subTest(adapter=adapter, method=method):
                    self.setUp()
                    loop = self._loop(adapter, method, 1, 2, False)
                    getattr(self.trainer, 'training_config').log_every_n_steps = 10
                    rows = list(loop)
                    self.optimizer.step.assert_called_once()
                    self.assertEqual(rows[-1][0], 1)
                    self.assertIn('Training complete', rows[-1][2])

    def test_bfloat16_lokr_checks_nonfinite_gradients_without_a_scaler(self) -> None:
        for device in ('cuda', 'xpu'):
            for accumulation in (1, 2):
                with self.subTest(device=device, accumulation=accumulation):
                    self.setUp()
                    self.assertEqual(self._call('_select_fabric_precision', device), 'bf16-mixed')
                    with self.assertRaisesRegex(FloatingPointError, 'Non-finite gradients'):
                        list(self._loop('LoKR', '_train_with_fabric', 1, accumulation, True, device))
                    self.optimizer.step.assert_not_called()
                    self._assert_not_published()
