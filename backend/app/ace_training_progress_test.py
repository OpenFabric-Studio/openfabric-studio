"""Exercise the maintained native ACE progress patch without ACE, torch, or a GPU."""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


PATCH = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
FIXTURE = Path(__file__).parent / 'fixtures/ace_training_loops.py'


def _file_patch(path: str) -> str:
    for section in PATCH.read_text().split('diff --git ')[1:]:
        if section.startswith(f'a/{path} b/{path}\n'):
            return 'diff --git ' + section
    return ''


class Loss:
    """A scalar tensor stand-in with an observable backward boundary."""

    def __init__(self, value: float, backward: Mock) -> None:
        self.value = value
        self._backward = backward

    def __truediv__(self, divisor: int) -> Loss:
        return Loss(self.value / divisor, self._backward)

    def item(self) -> float:
        return self.value

    def backward(self) -> None:
        self._backward()


class AceTrainingProgressTest(unittest.TestCase):
    def _api_log_interval(self, adapter: str = 'lora') -> int:
        source = _file_patch(f'acestep/api/train_api_{adapter}_start_route.py')
        match = re.search(r'^\+\s+log_every_n_steps=(\d+),$', source, re.MULTILINE)
        self.assertIsNotNone(match, f'{adapter} API must report every real optimizer update')
        if match is None:
            raise AssertionError('missing API log cadence')
        return int(match.group(1))

    def _loop(self, method: str, state: dict[str, object], batches: int = 3) -> tuple[Iterator[tuple[int, float, str]], Mock, Mock]:
        source = _file_patch('acestep/training/trainer.py')
        # The fixture retains the inspected LoRA loop regions, including the
        # real gradient accumulation/remainder/logging branches. Only apply its
        # hunks; LoKR has the same additional phase reporting in its own loops.
        header, *hunks = re.split(r'(?=^@@ )', source, flags=re.MULTILINE)
        selected: list[str] = []
        for hunk in hunks:
            match = re.match(r'@@ -(\d+)', hunk)
            if match and int(match.group(1)) < 1340:
                selected.append(hunk)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'acestep/training/trainer.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(FIXTURE.read_bytes())
            if selected:
                subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'],
                               input=header + ''.join(selected), text=True, cwd=root,
                               check=True, capture_output=True)
            patched = target.read_text()
        forward = Mock()
        backward = Mock()
        optimizer = Mock()
        fabric = Mock(backward=Mock(side_effect=lambda loss: loss.backward()))
        loss = Loss(4.0, backward)

        def observe_forward(batch: int) -> Loss:
            self.assertEqual(state['current_epoch'], 1)
            self.assertIn(f'Batch {batch + 1}/{batches}', str(state['status']))
            self.assertIn('forward', str(state['status']))
            if batch == 0:
                self.assertIsNone(state['current_loss'])
                self.assertEqual(state['current_step'], 0)
                fabric.log.assert_not_called()
            forward(batch)
            return loss

        def observe_backward() -> None:
            self.assertIn('backward', str(state['status']))

        backward.side_effect = observe_backward
        config = SimpleNamespace(max_epochs=1, gradient_accumulation_steps=2,
                                 max_grad_norm=1.0, log_every_n_steps=self._api_log_interval())
        namespace: dict[str, object] = {
            'time': time, 'train_loader': list(range(batches)), 'trainable_params': [],
            '_count_nonfinite_grads': lambda params: (0, 1), 'optimizer': optimizer,
            'scheduler': Mock(get_last_lr=Mock(return_value=[0.001])), 'torch': Mock(),
        }
        exec(compile(patched, '<patched-ace-loops>', 'exec'), namespace)
        factory = namespace['LoRATrainer']
        self.assertTrue(callable(factory))
        if not callable(factory):
            raise AssertionError('missing fixture trainer')
        trainer = factory()
        trainer.training_config = config
        trainer.module = SimpleNamespace(training_step=observe_forward,
                                         model=SimpleNamespace(decoder=Mock()))
        trainer.fabric = fabric
        return getattr(trainer, method)(state), optimizer, fabric

    @staticmethod
    def _state() -> dict[str, object]:
        return {'should_stop': False, 'current_epoch': 0, 'current_step': 0,
                'current_loss': None, 'status': 'Starting...',
                'plot_steps': [], 'plot_loss': [], 'plot_ema': []}

    def test_api_requests_real_metrics_on_every_optimizer_update(self) -> None:
        for adapter in ('lora', 'lokr'):
            with self.subTest(adapter=adapter):
                self.assertEqual(self._api_log_interval(adapter), 1)

    def test_startup_loss_remains_unknown_until_first_update(self) -> None:
        for adapter in ('lora', 'lokr'):
            with self.subTest(adapter=adapter):
                source = _file_patch(f'acestep/api/train_api_{adapter}_start_route.py')
                assignment = next((line[1:].strip() for line in source.splitlines()
                                   if line.startswith('+') and 'training_state["current_loss"] =' in line), None)
                self.assertIsNotNone(assignment)
                if assignment is None:
                    continue
                for step, loss, expected in ((0, 0.0, None), (1, 0.25, 0.25), (1, 0.0, 0.0)):
                    state: dict[str, object] = {}
                    exec(assignment, {'training_state': state, 'step': step, 'loss': loss})
                    self.assertEqual(state['current_loss'], expected)

    def test_phase_is_visible_before_forward_backward_and_remainder_updates_are_logged(self) -> None:
        for method in ('_train_with_fabric', '_train_basic'):
            with self.subTest(method=method):
                state = self._state()
                updates, optimizer, fabric = self._loop(method, state)
                rows = []
                for step, loss, status in updates:
                    self.assertGreater(optimizer.step.call_count, 0)
                    state.update(current_step=step, current_loss=loss, status=status)
                    rows.append((step, loss))
                self.assertEqual(rows, [(1, 2.0), (2, 2.0)])
                self.assertEqual(optimizer.step.call_count, 2)
                if method == '_train_with_fabric':
                    self.assertEqual(fabric.log.call_count, 4)
                    for call in fabric.log.call_args_list:
                        self.assertIn(call.kwargs['step'], (1, 2))
                    self.assertEqual(state['plot_steps'], [1, 2])

    def test_stop_before_first_batch_does_not_run_or_log_training(self) -> None:
        for method in ('_train_with_fabric', '_train_basic'):
            with self.subTest(method=method):
                state = self._state()
                state['should_stop'] = True
                updates, optimizer, fabric = self._loop(method, state)
                rows = list(updates)
                self.assertEqual(len(rows), 1)
                self.assertIn('stopped', rows[0][2])
                optimizer.step.assert_not_called()
                fabric.backward.assert_not_called()
                fabric.log.assert_not_called()
                self.assertIsNone(state['current_loss'])


if __name__ == '__main__':
    unittest.main()
