#!/usr/bin/env python3
"""Train one local character LoRA with the pinned video engine.

This is OpenFabric's glue. It does not vendor the trainer. Point
OPENFABRIC_VIDEO_CHARACTER_TRAINER at the video engine's Python
(the ltx-2-mlx .venv) and OpenFabric runs this script. The script refuses
to start unless the pinned model files are already on disk. It never
downloads weights.

The engine command is the existing ``train`` / ``preprocess`` CLI
(ltx-trainer-mlx, Apache-2.0). Install that package inside the engine
environment. This script does not copy that project.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _fail(code: str, message: str, status: int) -> int:
    print(f"{code}: {message}", file=sys.stderr)
    return status


def _yaml(value: object, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, list) and not item:
                lines.append(f"{pad}{key}: []")
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}[]"
        return "\n".join(f"{pad}- {_scalar(item)}" for item in value)
    return f"{pad}{_scalar(value)}"


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _snapshot_with_weights(cache: Path, key: str) -> Path | None:
    from app.video_engine import VideoEngineError, local_snapshot

    try:
        path = local_snapshot(cache, key)
    except VideoEngineError:
        return None
    try:
        if path.is_dir() and any(path.glob("*.safetensors")):
            return path
    except OSError:
        return None
    return None


def _still_to_clip(ffmpeg: str, image: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-loop", "1", "-i", str(image),
            "-frames:v", "97", "-r", "24",
            "-vf", "scale=960:544:force_original_aspect_ratio=increase,crop=960:544",
            "-an", str(dest),
        ],
        check=True,
    )


def _trim_clip(ffmpeg: str, source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-i", str(source),
            "-frames:v", "97", "-r", "24",
            "-vf", "scale=960:544:force_original_aspect_ratio=increase,crop=960:544",
            "-an", str(dest),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a local OpenFabric character adapter")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=800)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--image", action="append", default=[], type=Path)
    parser.add_argument("--clip", action="append", default=[], type=Path)
    args = parser.parse_args()
    if not args.image and not args.clip:
        return _fail("invalid_reference", "Add photos or a short clip.", 2)
    steps = min(3000, max(100, args.steps))
    rank = min(64, max(8, args.rank))
    model = _snapshot_with_weights(args.model_cache, "ltx23")
    gemma = _snapshot_with_weights(args.model_cache, "gemma3")
    if model is None or gemma is None:
        return _fail(
            "model_not_installed",
            "The local LTX-2.3 and Gemma files are not in the video model cache. "
            "Install them with setup_video.sh --download-models. This trainer does not download weights.",
            3,
        )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output.parent / "work"
    videos = work / "videos"
    captions = work / "captions"
    precomputed = work / "precomputed"
    train_out = work / "train_out"
    videos.mkdir(parents=True, exist_ok=True)
    captions.mkdir(parents=True, exist_ok=True)
    from app.video_media import VideoMediaError, tool

    try:
        ffmpeg = tool("ffmpeg")
    except VideoMediaError:
        return _fail("ffmpeg_missing", "ffmpeg was not found.", 4)
    caption = " ".join(args.name.split())
    index = 0
    try:
        for image in args.image:
            dest = videos / f"photo_{index:02d}.mp4"
            _still_to_clip(ffmpeg, image, dest)
            (captions / f"{dest.stem}.txt").write_text(caption + "\n", encoding="utf-8")
            index += 1
        for clip in args.clip:
            dest = videos / f"clip_{index:02d}.mp4"
            _trim_clip(ffmpeg, clip, dest)
            (captions / f"{dest.stem}.txt").write_text(caption + "\n", encoding="utf-8")
            index += 1
    except (OSError, subprocess.CalledProcessError) as exc:
        return _fail("invalid_reference", f"Could not prepare training clips: {exc}", 4)
    transformer = "transformer-dev.safetensors"
    config: dict[str, object] = {
        "model": {
            "model_path": str(model),
            "text_encoder_path": str(gemma),
            "training_mode": "lora",
            **({"transformer_file": transformer} if (model / transformer).is_file() else {}),
        },
        "lora": {
            "rank": rank,
            "alpha": rank,
            "dropout": 0.0,
            "target_modules": ["to_k", "to_q", "to_v", "to_out.0"],
        },
        "training_strategy": {"name": "text_to_video", "generate_audio": False},
        "optimization": {
            "learning_rate": 0.0002,
            "steps": steps,
            "batch_size": 1,
            "gradient_accumulation_steps": 1,
            "max_grad_norm": 1.0,
            "optimizer_type": "adamw",
            "scheduler_type": "cosine",
            "enable_gradient_checkpointing": True,
        },
        "data": {"preprocessed_data_root": str(precomputed)},
        "validation": {
            "prompts": [],
            "interval": None,
            "generate_audio": False,
            "skip_initial_validation": True,
        },
        "checkpoints": {"interval": None, "keep_last_n": 1},
        "hub": {"push_to_hub": False},
        "wandb": {"enabled": False},
        "seed": 42,
        "output_dir": str(train_out),
    }
    config_path = work / "character.yaml"
    config_path.write_text(_yaml(config) + "\n", encoding="utf-8")
    runner = Path(__file__).resolve().parent / "run_video.py"
    env = os.environ.copy()
    env.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    })
    base = [sys.executable, str(runner), "--engine-dir", str(args.engine_dir.resolve()), "--"]
    preprocess = [
        *base, "preprocess", "--videos", str(videos), "--output", str(precomputed),
        "--model", str(model), "--gemma", str(gemma),
        "--height", "544", "--width", "960", "--max-frames", "97",
        "--captions", str(captions), "--frame-rate", "24",
    ]
    train = [*base, "train", "--config", str(config_path), "--low-ram"]
    for argv in (preprocess, train):
        completed = subprocess.run(argv, env=env)
        if completed.returncode != 0:
            return _fail("trainer_failed", "The local video trainer stopped before writing an adapter.", completed.returncode or 1)
    saved = sorted((train_out / "checkpoints").glob("lora_weights_step_*.safetensors"))
    if not saved:
        return _fail("trainer_no_adapter", "Training finished without a LoRA file.", 1)
    target = output / "adapter.safetensors"
    shutil.copyfile(saved[-1], target)
    if not target.is_file() or target.stat().st_size < 1024:
        return _fail("trainer_no_adapter", "The LoRA file was empty.", 1)
    print(f"adapter: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
