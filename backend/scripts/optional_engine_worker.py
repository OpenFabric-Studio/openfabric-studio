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
from pathlib import Path


def _device() -> str:
    import torch
    if torch.cuda.is_available():
        return 'cuda'
    if sys.platform == 'darwin' and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


def _kokoro(request: dict[str, object]) -> None:
    os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
    from kokoro import KModel, KPipeline
    import soundfile as sf
    import torch
    config = str(request['config_path'])
    model_path = str(request['model_path'])
    voice = str(request['voice_path'])
    device = _device()
    model = KModel(repo_id='hexgrad/Kokoro-82M', config=config, model=model_path).to(device).eval()
    pipeline = KPipeline(lang_code=str(request['lang']), repo_id='hexgrad/Kokoro-82M', model=model)
    chunks: list[torch.Tensor] = []
    for item in pipeline(str(request['text']), voice=voice):
        audio = item.audio
        if audio is not None:
            chunks.append(audio.detach().cpu())
    if not chunks:
        raise RuntimeError('kokoro_produced_no_audio')
    output = Path(str(request['output_path']))
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), torch.cat(chunks).numpy(), 24000)
    print(json.dumps({'device': device, 'runtime': 'pytorch'}), flush=True)


def _chatterbox(request: dict[str, object]) -> None:
    kind = str(request['model'])
    if kind == 'turbo':
        raise RuntimeError('chatterbox_turbo_refused')
    device = _device()
    prompt = request.get('audio_prompt_path')
    prompt_path = str(prompt) if isinstance(prompt, str) and prompt else None
    output = Path(str(request['output_path']))
    output.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'original':
        from chatterbox.tts import ChatterboxTTS
        import torchaudio
        engine = ChatterboxTTS.from_local(str(request['weights']), device)
        wav = engine.generate(str(request['text']), audio_prompt_path=prompt_path)
        torchaudio.save(str(output), wav, engine.sr)
    elif kind == 'multilingual':
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        import torchaudio
        engine = ChatterboxMultilingualTTS.from_local(
            str(request['weights']), device, t3_model=str(request['t3_model']))
        wav = engine.generate(
            str(request['text']), language_id=str(request['language_id']), audio_prompt_path=prompt_path)
        torchaudio.save(str(output), wav, engine.sr)
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
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if not isinstance(request, dict):
        return 2
    task = request.get('task')
    if task == 'kokoro':
        _kokoro(request)
    elif task == 'chatterbox':
        _chatterbox(request)
    else:
        print('unsupported task', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — the parent reports the code, not a download
        print(f'optional-engine: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
