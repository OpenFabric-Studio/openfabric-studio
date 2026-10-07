"""Idempotent patches for inspected Seed-VC source; unknown signatures fail closed.

No models are imported. All source changes are validated before writing any
file. Invoke after setup and before training/inference so fresh clones also get
these fixes. A different upstream implementation requires a reviewed patch.

Inspected vendor source excerpts retain Seed-VC's GPL version 3 license;
see fixtures/README.md and fixtures/COPYING.seed-vc for provenance and terms.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class EngineCompatibilityError(Exception):
    code = 'engine_incompatible'

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class SourcePatch:
    relative_path: str
    name: str
    before: str
    after: str


_SELECTION_BEFORE = '''        if pretrained_ckpt_path is None:
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
'''
_SELECTION_AFTER = '''        # OpenFabric: continuation is explicit; a fresh build never discovers old weights.
        if resume:
            latest_checkpoint = pretrained_ckpt_path or os.path.join(self.log_dir, "resume.pth")
            if not os.path.isfile(latest_checkpoint):
                raise ValueError("resume_checkpoint_missing")
        elif pretrained_ckpt_path is not None:
            if not os.path.isfile(pretrained_ckpt_path):
                raise ValueError("pretrained_checkpoint_missing")
            latest_checkpoint = pretrained_ckpt_path
        elif config.get('pretrained_model', ''):
            latest_checkpoint = load_custom_model_from_hf("Plachta/Seed-VC", config['pretrained_model'], None)
            if not os.path.isfile(latest_checkpoint):
                raise ValueError("pretrained_checkpoint_missing")
        else:
            raise ValueError("pretrained_checkpoint_missing")
'''
_SAVE_BEFORE = '''            if self.iters >= self.max_steps:
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
'''
_SAVE_AFTER = '''            # OpenFabric: save before stopping, retaining comparison milestones.
            if self.iters % self.save_interval == 0 or self.iters in (200, 500, 1000) or self.iters >= self.max_steps:
                self._save_training_state()
            if self.iters >= self.max_steps:
                break
'''
_STATE_METHOD = '''    def _save_training_state(self):
        state = {
            'net': {key: self.model[key].state_dict() for key in self.model},
            'optimizer': self.optimizer.state_dict(),
            'scheduler': self.optimizer.scheduler_state_dict(),
            'iters': self.iters,
            'epoch': self.epoch,
        }
        os.makedirs(self.log_dir, exist_ok=True)
        torch.save(state, os.path.join(self.log_dir, 'resume.pth'))
        if self.iters in (200, 500, 1000) or self.iters >= self.max_steps:
            torch.save(state, os.path.join(self.log_dir, f'step_{self.iters}.pth'))

'''
_PROGRESS_HELPERS = '''def _openfabric_progress(phase, current=0, total=0):
    import json
    print("OPENFABRIC_PROGRESS " + json.dumps({'phase': phase, 'current': current, 'total': total, 'at': time.time()}), flush=True)

def _openfabric_chunk_count(frames, window, overlap):
    # The first chunk covers window frames; subsequent chunks retain overlap.
    if window <= overlap or frames <= 0:
        raise ValueError("invalid_source_window")
    stride = window - overlap
    return 1 + (max(0, frames - window) + stride - 1) // stride

'''
_CONVERSION_LOOP_BEFORE = '''    max_source_window = max_context_window - mel2.size(2)
    # split source condition (cond) into chunks
    processed_frames = 0
    generated_wave_chunks = []
    # generate chunk by chunk and stream the output
    while processed_frames < cond.size(1):
        chunk_cond = cond[:, processed_frames:processed_frames + max_source_window]
        is_last_chunk = processed_frames + max_source_window >= cond.size(1)
        cat_condition = torch.cat([prompt_condition, chunk_cond], dim=1)
        with torch.autocast(device_type=device.type, dtype=torch.float16 if fp16 else torch.float32):
            # Voice Conversion
            vc_target = model.cfm.inference(cat_condition,
                                                       torch.LongTensor([cat_condition.size(1)]).to(mel2.device),
                                                       mel2, style2, None, diffusion_steps,
                                                       inference_cfg_rate=inference_cfg_rate)
            vc_target = vc_target[:, :, mel2.size(-1):]
        vc_wave = vocoder_fn(vc_target.float()).squeeze()
        vc_wave = vc_wave[None, :]
        if processed_frames == 0:
            if is_last_chunk:
                output_wave = vc_wave[0].cpu().numpy()
                generated_wave_chunks.append(output_wave)
                break
            output_wave = vc_wave[0, :-overlap_wave_len].cpu().numpy()
            generated_wave_chunks.append(output_wave)
            previous_chunk = vc_wave[0, -overlap_wave_len:]
            processed_frames += vc_target.size(2) - overlap_frame_len
        elif is_last_chunk:
            output_wave = crossfade(previous_chunk.cpu().numpy(), vc_wave[0].cpu().numpy(), overlap_wave_len)
            generated_wave_chunks.append(output_wave)
            processed_frames += vc_target.size(2) - overlap_frame_len
            break
        else:
            output_wave = crossfade(previous_chunk.cpu().numpy(), vc_wave[0, :-overlap_wave_len].cpu().numpy(),
                                    overlap_wave_len)
            generated_wave_chunks.append(output_wave)
            previous_chunk = vc_wave[0, -overlap_wave_len:]
            processed_frames += vc_target.size(2) - overlap_frame_len
'''
_CONVERSION_LOOP_AFTER = '''    max_source_window = max_context_window - mel2.size(2)
    _openfabric_total_chunks = _openfabric_chunk_count(cond.size(1), max_source_window, overlap_frame_len)
    _openfabric_completed_chunks = 0
    _openfabric_progress('converting', 0, _openfabric_total_chunks)
    # split source condition (cond) into chunks
    processed_frames = 0
    generated_wave_chunks = []
    # generate chunk by chunk and stream the output
    while processed_frames < cond.size(1):
        chunk_cond = cond[:, processed_frames:processed_frames + max_source_window]
        is_last_chunk = processed_frames + max_source_window >= cond.size(1)
        cat_condition = torch.cat([prompt_condition, chunk_cond], dim=1)
        with torch.autocast(device_type=device.type, dtype=torch.float16 if fp16 else torch.float32):
            # Voice Conversion
            vc_target = model.cfm.inference(cat_condition,
                                                       torch.LongTensor([cat_condition.size(1)]).to(mel2.device),
                                                       mel2, style2, None, diffusion_steps,
                                                       inference_cfg_rate=inference_cfg_rate)
            vc_target = vc_target[:, :, mel2.size(-1):]
        vc_wave = vocoder_fn(vc_target.float()).squeeze()
        vc_wave = vc_wave[None, :]
        if processed_frames == 0:
            if is_last_chunk:
                output_wave = vc_wave[0].cpu().numpy()
                generated_wave_chunks.append(output_wave)
                _openfabric_completed_chunks += 1
                _openfabric_progress('converting', _openfabric_completed_chunks, _openfabric_total_chunks)
                break
            output_wave = vc_wave[0, :-overlap_wave_len].cpu().numpy()
            generated_wave_chunks.append(output_wave)
            _openfabric_completed_chunks += 1
            _openfabric_progress('converting', _openfabric_completed_chunks, _openfabric_total_chunks)
            previous_chunk = vc_wave[0, -overlap_wave_len:]
            processed_frames += vc_target.size(2) - overlap_frame_len
        elif is_last_chunk:
            output_wave = crossfade(previous_chunk.cpu().numpy(), vc_wave[0].cpu().numpy(), overlap_wave_len)
            generated_wave_chunks.append(output_wave)
            _openfabric_completed_chunks += 1
            _openfabric_progress('converting', _openfabric_completed_chunks, _openfabric_total_chunks)
            processed_frames += vc_target.size(2) - overlap_frame_len
            break
        else:
            output_wave = crossfade(previous_chunk.cpu().numpy(), vc_wave[0, :-overlap_wave_len].cpu().numpy(),
                                    overlap_wave_len)
            generated_wave_chunks.append(output_wave)
            _openfabric_completed_chunks += 1
            _openfabric_progress('converting', _openfabric_completed_chunks, _openfabric_total_chunks)
            previous_chunk = vc_wave[0, -overlap_wave_len:]
            processed_frames += vc_target.size(2) - overlap_frame_len
'''
PATCHES: tuple[SourcePatch, ...] = (
    SourcePatch('modules/length_regulator.py', 'bounded_f0',
                '''  f0_coarse = f0_coarse * (f0_coarse > 0)
  f0_coarse = f0_coarse + ((f0_coarse < 1) * 1)
  f0_coarse = f0_coarse * (f0_coarse < f0_bin)
  f0_coarse = f0_coarse + ((f0_coarse >= f0_bin) * (f0_bin - 1))''',
                '  f0_coarse = f0_coarse.clamp(min=1, max=f0_bin - 1)'),
    SourcePatch('train.py', 'resume_parameter', '                 device="cuda:0",\n                 ):',
                '                 device="cuda:0",\n                 resume=False,\n                 ):'),
    SourcePatch('train.py', 'explicit_selection', _SELECTION_BEFORE, _SELECTION_AFTER),
    SourcePatch('train.py', 'full_resume_load', '''                self.model, self.optimizer, latest_checkpoint,
                load_only_params=True,''', '''                self.model, self.optimizer, latest_checkpoint,
                load_only_params=not resume,'''),
    SourcePatch('train.py', 'complete_snapshots', _SAVE_BEFORE, _SAVE_AFTER),
    SourcePatch('train.py', 'training_state_method', '    def train_one_epoch(self):', _STATE_METHOD + '    def train_one_epoch(self):'),
    SourcePatch('train.py', 'resume_epoch', '        for epoch in range(self.n_epochs):\n            self.epoch = epoch',
                '        for epoch in range(self.epoch, self.n_epochs):\n            if self.iters >= self.max_steps:\n                break\n            self.epoch = epoch'),
    SourcePatch('train.py', 'final_training_state', "        print('Saving final model..')\n        state = {", "        self._save_training_state()\n        print('Saving final model..')\n        state = {"),
    SourcePatch('train.py', 'resume_argument', '        device=args.device\n    )', '        device=args.device,\n        resume=args.resume\n    )'),
    SourcePatch('train.py', 'resume_cli', "    parser.add_argument('--num-workers', type=int, default=0)",
                "    parser.add_argument('--num-workers', type=int, default=0)\n    parser.add_argument('--resume', action='store_true')"),
    SourcePatch('inference.py', 'seed_before_inference', '''def main(args):
    model, semantic_fn, f0_fn, vocoder_fn, campplus_model, mel_fn, mel_fn_args = load_models(args)''',
                '''def main(args):
    # OpenFabric: reproducible noise draws for like-for-like listening trials.
    if not 0 <= args.seed <= 2**32 - 1:
        raise ValueError("invalid_seed")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    model, semantic_fn, f0_fn, vocoder_fn, campplus_model, mel_fn, mel_fn_args = load_models(args)'''),
    SourcePatch('inference.py', 'seed_cli', '    parser.add_argument("--fp16", type=str2bool, default=True)',
                '    parser.add_argument("--fp16", type=str2bool, default=True)\n    parser.add_argument("--seed", type=int, default=42)'),
    SourcePatch('inference.py', 'progress_helpers', '@torch.no_grad()\ndef main(args):',
                _PROGRESS_HELPERS + '@torch.no_grad()\ndef main(args):'),
    SourcePatch('inference.py', 'progress_loading', 'def load_models(args):\n    global fp16',
                "def load_models(args):\n    _openfabric_progress('loading')\n    global fp16"),
    SourcePatch('inference.py', 'progress_analyzing', "    sr = mel_fn_args['sampling_rate']",
                "    _openfabric_progress('analyzing')\n    sr = mel_fn_args['sampling_rate']"),
    SourcePatch('inference.py', 'progress_chunks', _CONVERSION_LOOP_BEFORE, _CONVERSION_LOOP_AFTER),
)


def ensure_compatibility(engine_dir: Path) -> None:
    root = engine_dir.resolve()
    staged: dict[Path, str] = {}
    original: dict[Path, str] = {}
    for patch in PATCHES:
        path = (root / patch.relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise EngineCompatibilityError(f'{patch.relative_path}: missing or outside engine')
        try:
            source = staged.get(path)
            if source is None:
                source = path.read_text(encoding='utf-8')
                original[path] = source
        except (OSError, UnicodeError) as exc:
            raise EngineCompatibilityError(f'{patch.relative_path}: unreadable') from exc
        # The after signature may contain the before signature (argument insertion).
        legacy_after = (patch.after.replace('# OpenFabric:', '# Remiqora:')
                        .replace('_openfabric_', '_remiqora_')
                        .replace('OPENFABRIC_PROGRESS ', 'REMIQORA_PROGRESS '))
        if source.count(patch.after) == 1:
            staged[path] = source
        elif legacy_after != patch.after and source.count(legacy_after) == 1:
            staged[path] = source.replace(legacy_after, patch.after, 1)
        elif source.count(patch.before) == 1:
            staged[path] = source.replace(patch.before, patch.after, 1)
        else:
            raise EngineCompatibilityError(f'{patch.relative_path}: unsupported {patch.name} signature')
    for path, source in staged.items():
        try:
            ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise EngineCompatibilityError(f'{path.name}: invalid patched syntax') from exc
    for path, source in staged.items():
        if source == original[path]:
            continue
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix=f'.{path.name}.', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(source)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(path.stat().st_mode)
            temporary.replace(path)
        except OSError as exc:
            raise EngineCompatibilityError(f'{path.name}: cannot write compatibility patch') from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({'error_code': 'engine_incompatible'}), file=sys.stderr)
        return 2
    try:
        ensure_compatibility(Path(argv[1]))
    except EngineCompatibilityError as exc:
        print(json.dumps({'error_code': exc.code}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
