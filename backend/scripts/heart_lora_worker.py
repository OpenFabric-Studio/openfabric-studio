"""Headless corrected HeartMuLa PEFT training; codec work uses its own interpreter."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
import torch
from torch import Tensor, nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.heart_lora_conversion import map_original_key
from scripts.stable_lora_worker import atomic_json, integer, object_value, text

if TYPE_CHECKING:
    from simpletuner.helpers.models.heartmula.modeling_heartmula import HeartMuLaModel


@runtime_checkable
class CodecFactory(Protocol):
    def from_encoder_decoder_pretrained(self, decoder_path: str, encoder_path: str, *, local_files_only: bool) -> object: ...
    def from_pretrained(self, decoder_path: str, *, local_files_only: bool) -> object: ...


@runtime_checkable
class CodecModel(Protocol):
    def tokenize(self, waveform: Tensor, sample_rate: int) -> object: ...
    def detokenize(self, codes: Tensor, *, duration: float, num_steps: int, disable_progress: bool) -> object: ...

TRAINER_REVISION = 'a0e7ffadfcd0d3d0f68b400f9697c464da6f9408'
CODEC_REVISION = '5858ca8f1ffe58d62be7c007ea55f1033c5ca456'
BASE_REVISION = 'd12ac79c6b3387d5c9eee456323495d8f08bb09d'
BASE_SHA256 = '852e5728588705c4722f3a562265626c1324632060eb4cfb541bbecb22514509'
TOKENIZER_SHA256 = '79e3e522635f3171300913bb421464a87de6222182a0570b9b2ccba2a964b2b4'
TARGETS = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular_file(value: object) -> Path:
    path = Path(text(value, 10000))
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('heart_training_invalid_path')
    return path


def source_root(value: object, revision: str) -> Path:
    path = Path(text(value, 10000))
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_dir():
        raise ValueError('heart_training_invalid_source')
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
    if result.stdout.strip() != revision:
        raise ValueError('heart_training_source_revision_mismatch')
    clean = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'], check=True, capture_output=True, text=True)
    if clean.stdout.strip():
        raise ValueError('heart_training_source_modified')
    untracked = subprocess.run(['git', '-C', str(path), 'ls-files', '--others', '--exclude-standard'], check=True, capture_output=True, text=True)
    if any(name.endswith('.py') for name in untracked.stdout.splitlines()):
        raise ValueError('heart_training_source_unreviewed')
    return path


def read_plan(path: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    regular_file(str(path))
    if path.stat().st_size > 16 * 1024 * 1024 or re.fullmatch(r'[0-9a-f]{32}', path.parent.name) is None:
        raise ValueError('heart_training_invalid_plan')
    plan = object_value(json.loads(path.read_bytes()))
    request = object_value(plan.get('request'))
    runtime = object_value(plan.get('runtime'))
    if request.get('engine') != 'heartmula' or not isinstance(plan.get('sources'), list):
        raise ValueError('heart_training_invalid_plan')
    return plan, request, runtime


def stage(directory: Path, name: str, *, step: int | None = None, total: int | None = None, loss: float | None = None, seconds_per_step: float | None = None) -> None:
    atomic_json(directory / 'stage.json', {'stage': name, 'step': step, 'total_steps': total, 'loss': loss,
        'seconds_per_step': seconds_per_step,
        'updated_at': datetime.now(timezone.utc).isoformat()})


def tensor(value: object) -> Tensor:
    if not isinstance(value, Tensor):
        raise ValueError('heart_training_invalid_model_output')
    return value


def device(runtime: dict[str, object]) -> torch.device:
    name = runtime.get('device', 'mps')
    if name not in ('mps', 'cuda', 'cpu') or not isinstance(name, str):
        raise ValueError('heart_training_invalid_device')
    return torch.device(name)


def load_base(runtime: dict[str, object]) -> tuple[HeartMuLaModel, str]:
    """Load original weights strictly, with both reviewed rotary corrections."""
    from safetensors.torch import load_file
    from transformers.models.llama.modeling_llama import LlamaModel, LlamaRotaryEmbedding

    root = source_root(runtime.get('simpletuner_root'), TRAINER_REVISION)
    os.environ['SIMPLETUNER_SKIP_TORCH'] = '1'
    sys.path.insert(0, str(root))
    from simpletuner.helpers.models.heartmula.configuration_heartmula import HeartMuLaConfig
    from simpletuner.helpers.models.heartmula.modeling_heartmula import HeartMuLaModel

    base = Path(text(runtime.get('base_path'), 10000))
    config_file = regular_file(str(base / 'config.json'))
    config = HeartMuLaConfig.from_pretrained(str(base), local_files_only=True)
    if config.backbone_flavor != 'llama-3B' or config.decoder_flavor != 'llama-300M' or config.audio_num_codebooks != 8:
        raise ValueError('heart_training_wrong_base')
    with torch.device('meta'):
        model = HeartMuLaModel(config)
    # Original HeartMuLa removes both unused text tables from the transformers.
    for stack in (model.backbone, model.decoder):
        del stack.embed_tokens
        stack.add_module('embed_tokens', nn.Identity())
    index = object_value(json.loads(regular_file(str(base / 'model.safetensors.index.json')).read_bytes()))
    mapping = object_value(index.get('weight_map'))
    filenames = sorted({text(value, 200) for value in mapping.values()})
    if len(filenames) != 4 or any(re.fullmatch(r'model-[0-9]{5}-of-00004\.safetensors', name) is None for name in filenames):
        raise ValueError('heart_training_invalid_base_shards')
    identity: dict[str, str] = {'config.json': file_hash(config_file)}
    weights: dict[str, Tensor] = {}
    for filename in filenames:
        path = regular_file(str(base / filename))
        identity[filename] = file_hash(path)
        for original, value in load_file(str(path), device='cpu').items():
            key = map_original_key(original)
            if key in weights:
                raise ValueError('heart_training_duplicate_base_tensor')
            if original.endswith(('.attn.q_proj.weight', '.attn.k_proj.weight')):
                query = '.q_proj.' in original
                heads = (24 if query else 8) if original.startswith('backbone.') else (8 if query else 4)
                if value.ndim != 2 or value.shape[0] % (heads * 2):
                    raise ValueError('heart_training_invalid_qk_shape')
                value = value.reshape(heads, value.shape[0] // heads // 2, 2, value.shape[1]).transpose(1, 2).reshape(value.shape)
            weights[key] = value.to(dtype=torch.bfloat16)
    model.load_state_dict(weights, strict=True, assign=True)
    del weights
    for stack in (model.backbone, model.decoder):
        if not isinstance(stack, LlamaModel):
            raise ValueError('heart_training_invalid_transformer')
        parameters = stack.config.rope_parameters
        if parameters is None:
            raise ValueError('heart_training_missing_rope_configuration')
        parameters['original_max_position_embeddings'] = 8192
        stack.rotary_emb = LlamaRotaryEmbedding(stack.config)
        stack.config._attn_implementation = 'sdpa'
    gc.collect()
    base_sha256 = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if base_sha256 != BASE_SHA256:
        raise ValueError('heart_training_base_digest_mismatch')
    nn.Module.to(model, device(runtime))
    return model, base_sha256


def prompt_ids(tokenizer_path: Path, caption: str, lyrics: str) -> tuple[list[int], int]:
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    def encode(value: str) -> list[int]:
        ids = tokenizer.encode(value).ids
        if not ids or ids[0] != 128000:
            ids.insert(0, 128000)
        if ids[-1] != 128001:
            ids.append(128001)
        return ids
    tags = caption.strip().lower()
    if not tags.startswith('<tag>'):
        tags = '<tag>' + tags
    if not tags.endswith('</tag>'):
        tags += '</tag>'
    tag_ids = encode(tags)
    ids = tag_ids + [0] + encode(lyrics.strip().lower())
    if len(ids) > 4096:
        raise ValueError('heart_training_prompt_too_long')
    return ids, len(tag_ids)


def training_batch(tokens_path: Path, tokenizer_path: Path, sample: dict[str, object], target: torch.device) -> tuple[Tensor, Tensor, Tensor, int]:
    raw = np.load(tokens_path, allow_pickle=False)
    if raw.dtype != np.int64 or raw.ndim != 2 or raw.shape[0] != 8 or not 1 <= raw.shape[1] <= 400 or np.any(raw < 0) or np.any(raw >= 8192):
        raise ValueError('heart_training_invalid_audio_tokens')
    caption = text(sample.get('caption'), 4000)
    lyrics = sample.get('lyrics', '')
    if not isinstance(lyrics, str) or len(lyrics) > 20000:
        raise ValueError('heart_training_invalid_lyrics')
    ids, muq_index = prompt_ids(tokenizer_path, caption, lyrics)
    length = len(ids)
    tokens = torch.zeros((1, length + raw.shape[1] + 1, 9), dtype=torch.long, device=target)
    mask = torch.zeros_like(tokens, dtype=torch.bool)
    tokens[0, :length, -1] = torch.tensor(ids, dtype=torch.long, device=target)
    mask[:, :length, -1] = True
    tokens[0, length:-1, :8] = torch.from_numpy(raw.T.copy()).to(target)
    tokens[:, -1, :8] = 8193
    mask[:, length:, :8] = True
    frames = torch.zeros(tokens.shape[:2], dtype=torch.bool, device=target)
    frames[:, length:] = True
    return tokens, mask, frames, muq_index


def forward_logits(model: HeartMuLaModel, tokens: Tensor, mask: Tensor, muq_index: int) -> tuple[Tensor, Tensor]:
    hidden = tensor(model._build_backbone_inputs(tokens, mask,
        continuous_segments=torch.zeros((tokens.shape[0], 512), dtype=model.muq_linear.weight.dtype, device=tokens.device),
        starts=[muq_index] * tokens.shape[0]))
    output = model.forward_backbone(inputs_embeds=hidden, attention_mask=mask.any(dim=-1).long(), use_cache=False)
    prediction = model._predict_audio_tokens(tensor(output.last_hidden_state), tokens)
    if not isinstance(prediction, tuple) or len(prediction) != 2:
        raise ValueError('heart_training_invalid_model_output')
    return tensor(prediction[0]), tensor(prediction[1])


def token_loss(logits: tuple[Tensor, Tensor], tokens: Tensor, frames: Tensor) -> Tensor:
    selected = frames[:, 1:]
    targets = tokens[:, 1:, :8]
    first, rest = logits
    return (torch.nn.functional.cross_entropy(first[selected].float(), targets[:, :, 0][selected])
            + torch.nn.functional.cross_entropy(rest[selected].reshape(-1, rest.shape[-1]).float(), targets[:, :, 1:][selected].reshape(-1)))


def codec_timestep_embedding(module: nn.Module, timesteps: Tensor, max_period: int = 10000, scale: int = 1000) -> Tensor:
    """Adapt HeartLib's Apache-2.0 equation; see assets/heart-lora-NOTICE.txt."""
    dimension = module.flow_t_size
    if not isinstance(dimension, int) or dimension < 2:
        raise ValueError('heart_training_invalid_codec_timestep')
    half = dimension // 2
    frequencies = torch.exp(-math.log(max_period) * torch.arange(half, device=timesteps.device) / half).to(dtype=timesteps.dtype)
    angles = timesteps[:, None] * frequencies[None] * scale
    embedded = torch.cat((torch.cos(angles), torch.sin(angles)), dim=-1)
    if dimension % 2:
        embedded = torch.cat((embedded, torch.zeros_like(embedded[:, :1])), dim=-1)
    return embedded


def snapshot_audio(source: Path, directory: Path, index: int, seconds: int, lyrics: str) -> tuple[NDArray[np.float32], int, dict[str, object]]:
    import soundfile as sf
    before = file_hash(source)
    info = sf.info(source)
    if info.duration > seconds + 1 / info.samplerate and lyrics:
        raise ValueError('heart_training_lyrics_require_matching_trimmed_clip')
    if not 8000 <= info.samplerate <= 192000 or info.channels not in (1, 2):
        raise ValueError('heart_training_invalid_audio')
    waveform, rate = sf.read(source, frames=seconds * info.samplerate, dtype='float32', always_2d=True)
    if waveform.shape[0] < rate // 10 or not np.isfinite(waveform).all():
        raise ValueError('heart_training_invalid_audio')
    if file_hash(source) != before:
        raise ValueError('heart_training_source_changed')
    audio = directory / 'audio'
    audio.mkdir(exist_ok=True)
    if audio.is_symlink() or audio.resolve().parent != directory:
        raise ValueError('heart_training_invalid_snapshot_directory')
    copied = audio / f'{index:03d}.wav'
    if copied.exists() or copied.is_symlink():
        raise ValueError('heart_training_existing_audio_snapshot')
    sf.write(copied, waveform, rate, subtype='FLOAT')
    # Encode exactly the persisted private sample, preserving float32 values.
    private, private_rate = sf.read(copied, dtype='float32', always_2d=True)
    provenance: dict[str, object] = {'source_sha256': before, 'original_duration_seconds': info.duration,
        'offset_seconds': 0, 'segment_seconds': seconds, 'audio': f'audio/{index:03d}.wav',
        'audio_sha256': file_hash(copied), 'duration_seconds': private.shape[0] / private_rate}
    return private, private_rate, provenance


def codec_operation(path: Path, mode: Literal['encode', 'decode'], index: int) -> None:
    import soundfile as sf
    plan, request, runtime = read_plan(path)
    root = source_root(runtime.get('heartlib_root'), CODEC_REVISION)
    sys.path.insert(0, str(root / 'src' / 'heartlib'))
    from heartcodec.modeling_heartcodec import HeartCodec
    from heartcodec.models.transformer import PixArtAlphaCombinedFlowEmbeddings
    setattr(PixArtAlphaCombinedFlowEmbeddings, 'timestep_embedding', codec_timestep_embedding)
    factory: object = HeartCodec
    if not isinstance(factory, CodecFactory):
        raise ValueError('heart_training_invalid_codec_factory')
    target = device(runtime)
    decoder_path = text(runtime.get('decoder_path'), 10000)
    if mode == 'encode':
        sources = plan['sources']
        samples = request.get('samples')
        if not isinstance(sources, list) or not isinstance(samples, list) or len(sources) != len(samples):
            raise ValueError('heart_training_invalid_dataset')
        source = regular_file(sources[index])
        sample = object_value(samples[index])
        seconds = integer(request.get('segment_seconds'), 6, 30)
        lyrics = sample.get('lyrics', '')
        if not isinstance(lyrics, str):
            raise ValueError('heart_training_invalid_lyrics')
        waveform, rate, provenance = snapshot_audio(source, path.parent, index, seconds, lyrics)
        loaded = factory.from_encoder_decoder_pretrained(decoder_path, text(runtime.get('encoder_path'), 10000), local_files_only=True)
        if not isinstance(loaded, nn.Module) or not isinstance(loaded, CodecModel):
            raise ValueError('heart_training_invalid_codec')
        nn.Module.float(loaded)
        nn.Module.eval(loaded)
        nn.Module.to(loaded, target)
        codec: CodecModel = loaded
        with torch.inference_mode():
            encoded = tensor(codec.tokenize(torch.from_numpy(waveform.T.copy()), rate))
        output = path.parent / f'tokens-{index}.npy'
        with output.open('xb') as stream:
            np.save(stream, encoded.cpu().numpy(), allow_pickle=False)
        provenance.update({'tokens': output.name, 'tokens_sha256': file_hash(output)})
        atomic_json(path.parent / f'encoding-{index}.json', provenance)
    else:
        loaded = factory.from_pretrained(decoder_path, local_files_only=True)
        if not isinstance(loaded, nn.Module) or not isinstance(loaded, CodecModel):
            raise ValueError('heart_training_invalid_codec')
        nn.Module.float(loaded)
        nn.Module.eval(loaded)
        nn.Module.to(loaded, target)
        codec = loaded
        codes = np.load(path.parent / 'generated-tokens.npy', allow_pickle=False)
        if codes.dtype != np.int64 or codes.ndim != 2 or codes.shape[0] != 8 or not 1 <= codes.shape[1] <= 3000 or np.any(codes < 0) or np.any(codes >= 8192):
            raise ValueError('heart_training_invalid_generated_tokens')
        with torch.inference_mode():
            decoded_waveform = tensor(codec.detokenize(torch.from_numpy(codes.copy()).to(target), duration=29.76,
                num_steps=10, disable_progress=True))
        if not bool(torch.isfinite(decoded_waveform).all()):
            raise ValueError('heart_training_nonfinite_preview')
        preview = path.parent / 'preview.wav'
        if preview.exists() or preview.is_symlink():
            raise ValueError('heart_training_existing_preview')
        sf.write(preview, decoded_waveform.cpu().numpy().T, 48000)


def run_codec(path: Path, runtime: dict[str, object], mode: str, index: int = 0) -> None:
    interpreter = Path(text(runtime.get('codec_python'), 10000))
    if not interpreter.is_absolute() or not interpreter.resolve(strict=True).is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError('heart_training_invalid_interpreter')
    subprocess.run([str(interpreter), str(Path(__file__).resolve()), '--request', str(path), '--mode', mode, '--index', str(index)],
        check=True, env={**os.environ, 'PYTORCH_ENABLE_MPS_FALLBACK': '1'})


def train(path: Path) -> None:
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict
    from safetensors.torch import save_file
    plan, request, runtime = read_plan(path)
    directory = path.parent
    samples = request.get('samples')
    sources = plan.get('sources')
    if not isinstance(samples, list) or not 1 <= len(samples) <= 100 or not isinstance(sources, list) or len(sources) != len(samples):
        raise ValueError('heart_training_invalid_dataset')
    seed = integer(request.get('seed'), 0, 2147483647)
    steps = integer(request.get('max_steps'), 1, 100000)
    rank = integer(request.get('rank'), 8, 32)
    if rank not in (8, 16, 32):
        raise ValueError('heart_training_invalid_rank')
    rate = request.get('learning_rate')
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate) or not 1e-7 <= rate <= .01:
        raise ValueError('heart_training_invalid_learning_rate')
    torch.manual_seed(seed)
    stage(directory, 'encoding')
    for index in range(len(samples)):
        run_codec(path, runtime, 'encode', index)
    tokenizer = regular_file(runtime.get('tokenizer_path'))
    if file_hash(tokenizer) != TOKENIZER_SHA256:
        raise ValueError('heart_training_tokenizer_digest_mismatch')
    provenance = [dict(object_value(json.loads((directory / f'encoding-{index}.json').read_bytes())), sample=sample)
                  for index, sample in enumerate(samples)]
    atomic_json(directory / 'dataset.json', provenance)
    stage(directory, 'loading')
    base, base_sha256 = load_base(runtime)
    lora = LoraConfig(r=rank, lora_alpha=rank, target_modules=TARGETS, lora_dropout=0.0, bias='none', use_dora=False)
    model = get_peft_model(base, lora)
    base.backbone.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    base.decoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.train()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=float(rate), weight_decay=0.0)
    batches = [training_batch(directory / f'tokens-{index}.npy', tokenizer, object_value(sample), device(runtime)) for index, sample in enumerate(samples)]
    before = {key: value.detach().cpu().clone() for key, value in get_peft_model_state_dict(model).items()}
    training_started = time.monotonic()
    for step in range(1, steps + 1):
        tokens, mask, frames, muq_index = batches[(step - 1) % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        loss = token_loss(forward_logits(base, tokens, mask, muq_index), tokens, frames)
        if not bool(torch.isfinite(loss)):
            raise ValueError('heart_training_nonfinite_loss')
        torch.autograd.backward(loss)
        if any(parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all()) for parameter in trainable):
            raise ValueError('heart_training_nonfinite_gradient')
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        optimizer.step()
        loss_value = float(loss.detach().cpu())
        seconds_per_step = (time.monotonic() - training_started) / step
        stage(directory, 'training', step=step, total=steps, loss=loss_value, seconds_per_step=seconds_per_step)
    stage(directory, 'publishing', step=steps, total=steps, loss=loss_value, seconds_per_step=seconds_per_step)
    weights = {key: value.detach().cpu().contiguous() for key, value in get_peft_model_state_dict(model).items()}
    if any(not bool(torch.isfinite(value).all()) for value in weights.values()) or not any(not torch.equal(before[key], value) for key, value in weights.items()):
        raise ValueError('heart_training_unchanged_or_invalid_adapter')
    metadata = {'schema_version': 1, 'format': 'peft_lora', 'base_repository': 'HeartMuLa/HeartMuLa-oss-3B',
        'base_revision': BASE_REVISION, 'trainer_revision': TRAINER_REVISION,
        'encoder_revision': 'e6020510612d6653b73d6cd54393ef80929e3c81', 'decoder_revision': 'f889dab0532cfa4bf459f2a3367eb6d346b8eeda',
        'qk_layout': 'hf_split_half', 'rope_original_context': 8192, 'rank': rank, 'alpha': float(rank), 'target_modules': TARGETS,
        'tokenizer_sha256': file_hash(tokenizer), 'base_sha256': base_sha256}
    checkpoint = directory / 'adapter.safetensors'
    if checkpoint.exists():
        raise ValueError('heart_training_existing_checkpoint')
    temporary = directory / '.adapter.safetensors.tmp'
    try:
        save_file(weights, str(temporary), metadata={'heart_lora_config': json.dumps(metadata, separators=(',', ':'))})
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.link(temporary, checkpoint)
    finally:
        temporary.unlink(missing_ok=True)
    atomic_json(directory / 'adapter_config.json', {'peft_type': 'LORA', 'r': rank, 'lora_alpha': rank,
        'target_modules': TARGETS, 'lora_dropout': 0.0, 'bias': 'none', 'base_model_name_or_path': 'HeartMuLa/HeartMuLa-oss-3B',
        'revision': BASE_REVISION, 'inference_mode': True})
    atomic_json(directory / 'completion.json', {'job_id': directory.name, 'engine': 'heartmula',
        'request_sha256': text(plan.get('request_sha256'), 64), 'checkpoint': 'adapter.safetensors', 'sha256': file_hash(checkpoint),
        'dataset_sha256': file_hash(directory / 'dataset.json'), 'base_sha256': base_sha256, 'max_steps': steps})
    stage(directory, 'done', step=steps, total=steps, loss=loss_value, seconds_per_step=seconds_per_step)


def sample_token(logits: Tensor) -> Tensor:
    scores, indices = logits.float().topk(50, dim=-1)
    if not bool(torch.isfinite(scores).all()):
        raise ValueError('heart_training_nonfinite_generation')
    selected = torch.multinomial(torch.softmax(scores, dim=-1), 1)
    return indices.gather(-1, selected)


def generate(path: Path) -> None:
    """Reload a published PEFT adapter, produce real tokens, then decode separately."""
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict, set_peft_model_state_dict
    from peft.tuners.lora.layer import LoraLayer
    from safetensors import safe_open
    from safetensors.torch import load_file

    started = time.monotonic()
    plan, request, runtime = read_plan(path)
    directory = path.parent
    adapter = regular_file(plan.get('adapter_path'))
    if not 8 < adapter.stat().st_size <= 512 * 1024 * 1024:
        raise ValueError('heart_training_invalid_adapter')
    with safe_open(str(adapter), framework='pt', device='cpu') as stream:
        metadata = stream.metadata()
    if metadata is None:
        raise ValueError('heart_training_invalid_adapter_metadata')
    config = object_value(json.loads(text(metadata.get('heart_lora_config'), 8192)))
    if (config.get('base_revision') != BASE_REVISION or config.get('base_sha256') != BASE_SHA256
            or config.get('qk_layout') != 'hf_split_half' or config.get('rope_original_context') != 8192
            or config.get('tokenizer_sha256') != TOKENIZER_SHA256):
        raise ValueError('heart_training_incompatible_adapter')
    targets = config.get('target_modules')
    if (not isinstance(targets, list) or not 1 <= len(targets) <= 7
            or any(not isinstance(name, str) or name not in TARGETS for name in targets)):
        raise ValueError('heart_training_invalid_adapter_targets')
    names = [name for name in targets if isinstance(name, str)]
    if len(set(names)) != len(names):
        raise ValueError('heart_training_invalid_adapter_targets')
    rank = integer(config.get('rank'), 1, 32)
    alpha = config.get('alpha')
    strength = plan.get('strength', 1.0)
    if (not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not math.isfinite(alpha) or not 0 < alpha <= 1024
            or not isinstance(strength, (int, float)) or isinstance(strength, bool) or not math.isfinite(strength) or not 0 <= strength <= 2):
        raise ValueError('heart_training_invalid_adapter_scale')
    tokenizer = regular_file(runtime.get('tokenizer_path'))
    if file_hash(tokenizer) != TOKENIZER_SHA256:
        raise ValueError('heart_training_tokenizer_digest_mismatch')
    seconds = integer(request.get('duration_seconds'), 1, 240)
    caption = text(request.get('prompt'), 4000)
    lyrics = request.get('lyrics', '')
    if not isinstance(lyrics, str) or len(lyrics) > 20000:
        raise ValueError('heart_training_invalid_lyrics')
    seed = integer(request.get('seed'), 0, 2147483647)
    torch.manual_seed(seed)
    stage(directory, 'loading')
    base, base_sha256 = load_base(runtime)
    model = get_peft_model(base, LoraConfig(r=rank, lora_alpha=rank,
        target_modules=names, lora_dropout=0.0, bias='none', use_dora=False))
    for module in model.modules():
        if isinstance(module, LoraLayer):
            module.set_scale('default', float(alpha) * float(strength) / rank)
    weights = load_file(str(adapter), device='cpu')
    expected = get_peft_model_state_dict(model)
    if weights.keys() != expected.keys() or any(value.shape != expected[key].shape or not bool(torch.isfinite(value).all()) for key, value in weights.items()):
        raise ValueError('heart_training_invalid_adapter_factors')
    loaded = set_peft_model_state_dict(model, weights)
    unexpected = getattr(loaded, 'unexpected_keys', None)
    if not isinstance(unexpected, list) or unexpected:
        raise ValueError('heart_training_unexpected_adapter_factors')
    model.eval()
    target = device(runtime)
    ids, muq_index = prompt_ids(tokenizer, caption, lyrics)
    tokens = torch.zeros((1, len(ids), 9), dtype=torch.long, device=target)
    tokens[0, :, -1] = torch.tensor(ids, dtype=torch.long, device=target)
    mask = torch.zeros_like(tokens, dtype=torch.bool)
    mask[:, :, -1] = True
    frames: list[Tensor] = []
    with torch.inference_mode():
        hidden = tensor(base._build_backbone_inputs(tokens, mask,
            continuous_segments=torch.zeros((1, 512), dtype=base.muq_linear.weight.dtype, device=target), starts=[muq_index]))
        output = base.forward_backbone(inputs_embeds=hidden, use_cache=True)
        past = output.past_key_values
        current = tensor(output.last_hidden_state)[:, -1]
        for frame_index in range(math.ceil(seconds * 12.5)):
            first = sample_token(tensor(base.codebook0_head(current)))
            samples = [first]
            local = tensor(base.projection(torch.cat((current.unsqueeze(1), tensor(base._embed_audio(0, first))), dim=1)))
            decoded = base.forward_decoder(inputs_embeds=local, use_cache=True)
            local_past = decoded.past_key_values
            for codebook in range(1, 8):
                logits = tensor(decoded.last_hidden_state)[:, -1] @ tensor(base.audio_head)[codebook - 1]
                selected = sample_token(logits)
                samples.append(selected)
                if codebook < 7:
                    local = tensor(base.projection(tensor(base._embed_audio(codebook, selected))))
                    decoded = base.forward_decoder(inputs_embeds=local, past_key_values=local_past,
                        position_ids=torch.full((1, 1), codebook + 1, dtype=torch.long, device=target), use_cache=True)
                    local_past = decoded.past_key_values
            frame = torch.cat(samples, dim=1)
            if bool((frame >= 8192).any()):
                break
            frames.append(frame)
            next_tokens = torch.zeros((1, 1, 9), dtype=torch.long, device=target)
            next_tokens[0, 0, :8] = frame[0]
            next_mask = torch.zeros_like(next_tokens, dtype=torch.bool)
            next_mask[:, :, :8] = True
            hidden = tensor(base._build_backbone_inputs(next_tokens, next_mask))
            output = base.forward_backbone(inputs_embeds=hidden, past_key_values=past,
                position_ids=torch.full((1, 1), len(ids) + frame_index, dtype=torch.long, device=target), use_cache=True)
            past = output.past_key_values
            current = tensor(output.last_hidden_state)[:, -1]
    if not frames:
        raise ValueError('heart_training_empty_generation')
    codes = torch.stack(frames, dim=-1)[0].cpu().numpy().astype(np.int64)
    with (directory / 'generated-tokens.npy').open('xb') as stream:
        np.save(stream, codes, allow_pickle=False)
    atomic_json(directory / 'generation.json', {'adapter_sha256': file_hash(adapter), 'base_sha256': base_sha256,
        'frames': int(codes.shape[1]), 'seed': seed, 'strength': strength, 'token_generation_seconds': time.monotonic() - started,
        'mps_driver_bytes_after_generation': torch.mps.driver_allocated_memory() if target.type == 'mps' else None})
    del model, base, output, past, local_past, decoded, expected, weights
    gc.collect()
    if target.type == 'mps':
        torch.mps.empty_cache()
    run_codec(path, runtime, 'decode')
    output_path = plan.get('output_path')
    if output_path is not None:
        output_file = Path(text(output_path, 10000))
        if output_file.parent != directory or output_file.exists() or output_file.is_symlink():
            raise ValueError('heart_training_invalid_preview_destination')
        (directory / 'preview.wav').replace(output_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--mode', choices=('train', 'encode', 'decode', 'generate'), default='train')
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args()
    if args.mode == 'train':
        train(args.request)
    elif args.mode == 'generate':
        generate(args.request)
    else:
        codec_operation(args.request, args.mode, args.index)


if __name__ == '__main__':
    main()
