#!/usr/bin/env bash
# Local singing-voice engine for Voice Clone. Not part of the API virtualenv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DIR="$ROOT/external/seed-vc"
if [[ ! -f "$DIR/train.py" ]]; then
  git clone --depth 1 https://github.com/Plachtaa/seed-vc.git "$DIR"
fi
uv venv --python 3.12 "$DIR/.venv"
uv pip install --python "$DIR/.venv/bin/python" \
  torch torchaudio 'transformers==4.46.3' 'numpy<2' 'matplotlib>=3.8,<3.10' \
  descript-audio-codec \
  scipy librosa pyyaml munch einops huggingface_hub soundfile tqdm 'pydantic>=2.13.5,<3'
"$DIR/.venv/bin/python" "$ROOT/backend/app/seed_vc_compat.py" "$DIR"
echo "Voice engine ready: $DIR/.venv"
