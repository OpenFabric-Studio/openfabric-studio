"""Tracked vendor patches fail closed and preserve explicit training semantics."""
from __future__ import annotations

import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import seed_vc_compat

# These are the inspected vendor source regions, retained independently of the
# implementation so tests also work without an external engine checkout.
F0_SOURCE = '''import torch
import numpy as np
f0_max = 1100.0
f0_min = 50.0
f0_mel_min = 1127 * np.log(1 + f0_min / 700)
f0_mel_max = 1127 * np.log(1 + f0_max / 700)
def f0_to_coarse(f0, f0_bin):
  f0_mel = 1127 * (1 + f0 / 700).log()
  a = (f0_bin - 2) / (f0_mel_max - f0_mel_min)
  b = f0_mel_min * a - 1.
  f0_mel = torch.where(f0_mel > 0, f0_mel * a - b, f0_mel)
  # torch.clip_(f0_mel, min=1., max=float(f0_bin - 1))
  f0_coarse = torch.round(f0_mel).long()
  f0_coarse = f0_coarse * (f0_coarse > 0)
  f0_coarse = f0_coarse + ((f0_coarse < 1) * 1)
  f0_coarse = f0_coarse * (f0_coarse < f0_bin)
  f0_coarse = f0_coarse + ((f0_coarse >= f0_bin) * (f0_bin - 1))
  return f0_coarse
'''
TRAIN_SOURCE = '''import os
import glob
import torch
import argparse
class Trainer:
    def __init__(self,
                 config_path,
                 pretrained_ckpt_path,
                 data_dir,
                 run_name,
                 batch_size=0,
                 num_workers=0,
                 steps=1000,
                 save_interval=500,
                 max_epochs=1000,
                 device="cuda:0",
                 ):
        self.log_dir = config_path
        config = {'pretrained_model': 'base.pth'}
        self.model, self.optimizer = {}, None
        if pretrained_ckpt_path is None:
            # find latest checkpoint
            available_checkpoints = glob.glob(os.path.join(self.log_dir, "DiT_epoch_*_step_*.pth"))
            if len(available_checkpoints) > 0:
                latest_checkpoint = max(
                    available_checkpoints, key=lambda x: int(x.split("_")[-1].split(".")[0])
                )
                earliest_checkpoint = min(
                    available_checkpoints, key=lambda x: int(x.split("_")[-1].split(".")[0])
                )
                # delete the earliest checkpoint if we have more than 2
                if (
                    earliest_checkpoint != latest_checkpoint
                    and len(available_checkpoints) > 2
                ):
                    os.remove(earliest_checkpoint)
                    print(f"Removed {earliest_checkpoint}")
            elif config.get('pretrained_model', ''):
                latest_checkpoint = load_custom_model_from_hf("Plachta/Seed-VC", config['pretrained_model'], None)
            else:
                latest_checkpoint = ""
        else:
            assert os.path.exists(pretrained_ckpt_path), f"Pretrained checkpoint {pretrained_ckpt_path} not found"
            latest_checkpoint = pretrained_ckpt_path

        if os.path.exists(latest_checkpoint):
            self.model, self.optimizer, self.epoch, self.iters = load_checkpoint(
                self.model, self.optimizer, latest_checkpoint,
                load_only_params=True,
                ignore_modules=[],
                is_distributed=False
            )
            print(f"Loaded checkpoint from {latest_checkpoint}")
        else:
            self.epoch, self.iters = 0, 0
            print("Failed to load any checkpoint, training from scratch.")

    def train_one_epoch(self):
        for i in range(1000):
            self.iters += 1

            if self.iters >= self.max_steps:
                break

            if self.iters % self.save_interval == 0:
                print('Saving..')
                state = {
                    'net': {key: self.model[key].state_dict() for key in self.model},
                    'optimizer': self.optimizer.state_dict(),
                    'scheduler': self.optimizer.scheduler_state_dict(),
                    'iters': self.iters,
                    'epoch': self.epoch,
                }
                save_path = os.path.join(
                    self.log_dir,
                    f'DiT_epoch_{self.epoch:05d}_step_{self.iters:05d}.pth'
                )
                torch.save(state, save_path)

                # find all checkpoints and remove old ones
                checkpoints = glob.glob(os.path.join(self.log_dir, 'DiT_epoch_*.pth'))
                if len(checkpoints) > 2:
                    checkpoints.sort(key=lambda x: int(x.split('_')[-1].split('.')[0]))
                    for cp in checkpoints[:-2]:
                        os.remove(cp)

    def train(self):
        self.ema_loss = 0
        self.loss_smoothing_rate = 0.99
        for epoch in range(self.n_epochs):
            self.epoch = epoch
            self.train_one_epoch()
            if self.iters >= self.max_steps:
                break

        print('Saving final model..')
        state = {
            'net': {key: self.model[key].state_dict() for key in self.model},
        }
        os.makedirs(self.log_dir, exist_ok=True)
        save_path = os.path.join(self.log_dir, 'ft_model.pth')
        torch.save(state, save_path)

def main(args):
    trainer = Trainer(
        config_path=args.config,
        pretrained_ckpt_path=args.pretrained_ckpt,
        data_dir=args.dataset_dir,
        run_name=args.run_name,
        batch_size=args.batch_size,
        steps=args.max_steps,
        max_epochs=args.max_epochs,
        save_interval=args.save_every,
        num_workers=args.num_workers,
        device=args.device
    )
    trainer.train()
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-workers', type=int, default=0)
'''
INFERENCE_SOURCE = (Path(__file__).with_name('fixtures') / 'seed_vc_inference.txt').read_text()


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Path(self.temp.name)
        (self.engine / 'modules').mkdir()
        for path, source in [('modules/length_regulator.py', F0_SOURCE), ('train.py', TRAIN_SOURCE), ('inference.py', INFERENCE_SOURCE)]:
            (self.engine / path).write_text(source)

    def apply(self):
        helper = getattr(seed_vc_compat, 'ensure_compatibility', None)
        self.assertTrue(callable(helper), 'tracked compatibility helper is missing')
        helper(self.engine)

    def test_patch_is_idempotent_and_fixes_upper_pitch_clamp(self):
        self.apply()
        source = (self.engine / 'modules/length_regulator.py').read_text()
        self.assertIn('f0_coarse.clamp(min=1, max=f0_bin - 1)', source)
        before = {path: path.read_bytes() for path in self.engine.rglob('*.py')}
        self.apply()
        self.assertEqual(before, {path: path.read_bytes() for path in self.engine.rglob('*.py')})

    def test_fresh_build_ignores_old_checkpoints_and_resume_loads_full_state(self):
        self.apply()
        source = ast.parse((self.engine / 'train.py').read_text())
        trainer = next(node for node in source.body if isinstance(node, ast.ClassDef))
        init = next(node for node in trainer.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        module = ast.Module(body=[ast.ClassDef(name='Trainer', bases=[], keywords=[], body=[init], decorator_list=[])], type_ignores=[])
        loader = Mock(return_value=({}, None, 0, 0))
        namespace: dict[str, object] = {'os': __import__('os'), 'glob': __import__('glob'), 'load_checkpoint': loader, 'load_custom_model_from_hf': Mock(return_value=str(self.engine / 'base.pth'))}
        exec(compile(ast.fix_missing_locations(module), '<patched-vendor>', 'exec'), namespace)
        factory = namespace['Trainer']
        self.assertTrue(callable(factory))
        (self.engine / 'base.pth').touch()
        (self.engine / 'DiT_epoch_00000_step_00100.pth').touch()
        factory(str(self.engine), None, '', '', resume=False)
        self.assertEqual(loader.call_args.args[2], str(self.engine / 'base.pth'))
        self.assertTrue(loader.call_args.kwargs['load_only_params'])
        (self.engine / 'resume.pth').touch()
        factory(str(self.engine), None, '', '', resume=True)
        self.assertEqual(loader.call_args.args[2], str(self.engine / 'resume.pth'))
        self.assertFalse(loader.call_args.kwargs['load_only_params'])
        (self.engine / 'base.pth').unlink()
        with self.assertRaisesRegex(ValueError, 'pretrained_checkpoint_missing'):
            factory(str(self.engine), None, '', '', resume=False)

    def test_full_state_is_saved_at_final_step_and_snapshots_are_retained(self):
        self.apply()
        tree = ast.parse((self.engine / 'train.py').read_text())
        trainer = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        epoch_fn = next(node for node in trainer.body if isinstance(node, ast.FunctionDef) and node.name == 'train_one_epoch')
        state_fn = next(node for node in trainer.body if isinstance(node, ast.FunctionDef) and node.name == '_save_training_state')
        module = ast.Module(body=[epoch_fn, state_fn], type_ignores=[])
        saved: dict[str, object] = {}
        save = Mock(side_effect=lambda state, path: saved.update({Path(path).name: state.copy()}))
        torch = Mock(save=save)
        namespace: dict[str, object] = {'os': __import__('os'), 'glob': __import__('glob'), 'torch': torch}
        exec(compile(ast.fix_missing_locations(module), '<patched-vendor>', 'exec'), namespace)
        fn = namespace['train_one_epoch']
        self.assertTrue(callable(fn))
        job = Mock(iters=0, max_steps=1000, save_interval=100, model={}, epoch=0, log_dir=str(self.engine))
        state_fn = namespace['_save_training_state']
        self.assertTrue(callable(state_fn))
        job._save_training_state.side_effect = lambda: state_fn(job)
        fn(job)
        self.assertTrue({'step_200.pth', 'step_500.pth', 'step_1000.pth', 'resume.pth'} <= saved.keys())
        self.assertEqual(saved['resume.pth']['iters'], 1000)
        self.assertTrue({'net', 'optimizer', 'scheduler', 'iters', 'epoch'} <= saved['resume.pth'].keys())

    def test_seed_is_exposed_and_applied_before_loading_models(self):
        self.apply()
        source = (self.engine / 'inference.py').read_text()
        self.assertIn('parser.add_argument("--seed", type=int, default=42)', source)
        self.assertLess(source.index('torch.manual_seed(args.seed)'), source.index('= load_models(args)'))
        self.assertIn('np.random.seed(args.seed)', source)
        self.assertIn('random.seed(args.seed)', source)

    def test_progress_chunk_total_matches_independent_overlap_loop(self):
        self.apply()
        tree = ast.parse((self.engine / 'inference.py').read_text())
        count_fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_openfabric_chunk_count')
        namespace: dict[str, object] = {}
        exec(compile(ast.Module(body=[count_fn], type_ignores=[]), '<vendor-progress>', 'exec'), namespace)
        count = namespace['_openfabric_chunk_count']
        self.assertTrue(callable(count))
        for window in [17, 100, 1720]:
            for frames in [1, window, window + 1, 2 * window, 20000]:
                position, expected = 0, 0
                while position < frames:
                    expected += 1
                    if position + window >= frames:
                        break
                    position += window - 16
                self.assertEqual(count(frames, window, 16), expected)
        with self.assertRaisesRegex(ValueError, 'invalid_source_window'):
            count(100, 16, 16)

    def test_progress_emits_actual_completed_chunks_and_timestamped_phases(self):
        self.apply()
        source = (self.engine / 'inference.py').read_text()
        self.assertIn("'at': time.time()", source)
        self.assertLess(source.index("_openfabric_progress('loading')"), source.index('= load_models(args)'))
        self.assertLess(source.index('= load_models(args)'), source.index("_openfabric_progress('analyzing')"))
        # Every branch publishes the output before it reports completion,
        # including the first/final short chunk and the overlapping final one.
        tree = ast.parse(source)
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        loop = next(node for node in main.body if isinstance(node, ast.While) and 'processed_frames' in ast.unparse(node.test))
        completions = [node for node in ast.walk(loop) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == '_openfabric_progress']
        self.assertEqual(len(completions), 4)
        self.assertEqual(source.count("_openfabric_completed_chunks += 1"), 4)

    def test_unknown_chunk_loop_rejects_before_partial_edits(self):
        inference = self.engine / 'inference.py'
        inference.write_text(INFERENCE_SOURCE.replace('processed_frames += vc_target.size(2) - overlap_frame_len',
                                                      'processed_frames += different_stride'))
        original = {path: path.read_bytes() for path in self.engine.rglob('*.py')}
        with self.assertRaises(seed_vc_compat.EngineCompatibilityError):
            self.apply()
        self.assertEqual(original, {path: path.read_bytes() for path in self.engine.rglob('*.py')})

    def test_fresh_setup_invokes_tracked_compatibility_helper(self):
        from app import module_install
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService

        repo = Path(__file__).resolve().parents[2]
        setup = (repo / 'setup_voice.sh').read_text()
        self.assertIn('"$ROOT/desktop/scripts/setup-feature.js" singing "$@"', setup)
        environment = ModuleEnvironment.for_root(self.engine / 'managed')
        service = ModuleJobService(environment)
        context = InstallContext(service, 'a' * 32)
        staged = context.workspace / 'singing-source'
        helper = Path(seed_vc_compat.__file__).resolve()
        helper_calls: list[list[str]] = []

        async def simulate_vendor_boundary(argv: list[str], *, cwd: Path | None = None) -> None:
            if argv[0] == 'git':
                if 'checkout' in argv:
                    for relative in ('modules/length_regulator.py', 'train.py', 'inference.py'):
                        target = staged / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes((self.engine / relative).read_bytes())
                return
            self.assertEqual(argv, [str(environment.python), str(helper), str(staged)])
            helper_calls.append(argv)
            self.assertEqual(seed_vc_compat.main(argv[1:]), 0)

        async def exercise() -> None:
            try:
                with patch.object(context, 'run', side_effect=simulate_vendor_boundary):
                    await module_install._clone(context, 'singing', module_install.SOURCE_PINS['singing'], environment.paths['singing'])
            finally:
                await service.shutdown()

        asyncio.run(exercise())
        self.assertEqual(len(helper_calls), 1)
        installed = environment.paths['singing']
        self.assertIn('f0_coarse.clamp(min=1, max=f0_bin - 1)', (installed / 'modules/length_regulator.py').read_text())
        self.assertIn('resume', (installed / 'train.py').read_text())

    def test_unknown_source_rejects_without_partial_edits(self):
        (self.engine / 'train.py').write_text(TRAIN_SOURCE.replace('load_only_params=True,', 'load_only_params=False,'))
        original = {path: path.read_bytes() for path in self.engine.rglob('*.py')}
        helper = getattr(seed_vc_compat, 'ensure_compatibility', None)
        self.assertTrue(callable(helper), 'tracked compatibility helper is missing')
        with self.assertRaises(Exception) as raised:
            helper(self.engine)
        self.assertEqual(getattr(raised.exception, 'code', ''), 'engine_incompatible')
        self.assertEqual(original, {path: path.read_bytes() for path in self.engine.rglob('*.py')})
