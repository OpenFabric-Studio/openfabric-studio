#!/usr/bin/env python3
"""Run inside an optional engine virtualenv. The API process never imports these packages.

Kokoro and Chatterbox only. Weights must already be on disk. This process sets
HF_HUB_OFFLINE so a missing file fails instead of downloading. Wan and RVC are
started from their own CLIs, not from this file.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KokoroJob:
    text: str
    lang: str
    config_path: str
    model_path: str
    voice_path: str
    output_path: str


@dataclass(frozen=True)
class ChatterboxJob:
    text: str
    model: str
    weights: str
    t3_model: str
    language_id: str
    audio_prompt_path: str
    output_path: str


def _field(data: dict[str, object], name: str, *, empty: bool = False) -> str:
    value = data.get(name)
    if not isinstance(value, str) or len(value) > 4000 or (not empty and not value.strip()):
        raise ValueError(f'invalid_request_field:{name}')
    return value


def _parse_request(raw: object) -> KokoroJob | ChatterboxJob:
    if not isinstance(raw, dict):
        raise ValueError('invalid_request')
    data: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError('invalid_request_key')
        data[key] = value
    task = _field(data, 'task')
    if task == 'kokoro':
        lang = _field(data, 'lang')
        if lang not in {'a', 'b'}:
            raise ValueError('invalid_kokoro_language')
        return KokoroJob(_field(data, 'text'), lang, _field(data, 'config_path'),
                         _field(data, 'model_path'), _field(data, 'voice_path'), _field(data, 'output_path'))
    if task == 'chatterbox':
        model = _field(data, 'model')
        if model not in {'original', 'multilingual'}:
            raise ValueError('chatterbox_model_refused')
        return ChatterboxJob(_field(data, 'text'), model, _field(data, 'weights'),
                            _field(data, 't3_model', empty=True), _field(data, 'language_id'),
                            _field(data, 'audio_prompt_path', empty=True), _field(data, 'output_path'))
    raise ValueError('unsupported_task')


def _device() -> str:
    import torch
    if torch.cuda.is_available():
        return 'cuda'
    if sys.platform == 'darwin' and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


def _kokoro(request: KokoroJob) -> None:
    os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
    from kokoro import KModel, KPipeline
    import soundfile as sf
    import torch
    config = request.config_path
    model_path = request.model_path
    voice = request.voice_path
    device = _device()
    model = KModel(repo_id='hexgrad/Kokoro-82M', config=config, model=model_path).to(device).eval()
    pipeline = KPipeline(lang_code=request.lang, repo_id='hexgrad/Kokoro-82M', model=model)
    chunks: list[torch.Tensor] = []
    for item in pipeline(request.text, voice=voice):
        audio = item.audio
        if audio is not None:
            chunks.append(audio.detach().cpu())
    if not chunks:
        raise RuntimeError('kokoro_produced_no_audio')
    output = Path(request.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), torch.cat(chunks).numpy(), 24000)
    print(json.dumps({'device': device, 'runtime': 'pytorch'}), flush=True)


def _chatterbox(request: ChatterboxJob) -> None:
    kind = request.model
    if kind == 'turbo':
        raise RuntimeError('chatterbox_turbo_refused')
    device = _device()
    prompt_path = request.audio_prompt_path or None
    output = Path(request.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'original':
        from chatterbox.tts import ChatterboxTTS
        import torchaudio
        engine = ChatterboxTTS.from_local(request.weights, device)
        wav = engine.generate(request.text, audio_prompt_path=prompt_path)
        torchaudio.save(str(output), wav, engine.sr, encoding='PCM_S', bits_per_sample=16)
    elif kind == 'multilingual':
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        import torchaudio
        engine = ChatterboxMultilingualTTS.from_local(
            request.weights, device, t3_model=request.t3_model)
        wav = engine.generate(
            request.text, language_id=request.language_id, audio_prompt_path=prompt_path)
        torchaudio.save(str(output), wav, engine.sr, encoding='PCM_S', bits_per_sample=16)
    else:
        raise RuntimeError('chatterbox_model_refused')
    print(json.dumps({'device': device, 'runtime': 'pytorch', 'model': kind}), flush=True)


def main() -> int:
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
    if len(sys.argv) != 2:
        print('usage: optional_engine_worker.py request.json', file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if path.stat().st_size > 65536:
        raise ValueError('request_too_large')
    raw: object = json.loads(path.read_text(encoding='utf-8'))
    request = _parse_request(raw)
    if isinstance(request, KokoroJob):
        _kokoro(request)
    else:
        _chatterbox(request)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — the parent reports the code, not a download
        print(f'optional-engine: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
