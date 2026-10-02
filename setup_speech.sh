#!/usr/bin/env bash
# Optional GPT-SoVITS checkout for talking / audiobook speech clone (MIT).
# Clones the engine and creates a Python 3.11 venv. Does NOT download pretrained
# model weights (multi-GB) — follow the printed manual steps for those.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DIR="$ROOT/external/gpt-sovits"
PY311="${OPENFABRIC_GPT_SOVITS_PYTHON:-}"

if [[ -z "$PY311" ]]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PY311="$(command -v python3.11)"
  elif [[ -x /opt/homebrew/opt/python@3.11/bin/python3.11 ]]; then
    PY311=/opt/homebrew/opt/python@3.11/bin/python3.11
  else
    echo "Need Python 3.11 (Homebrew: brew install python@3.11). Homebrew python3 3.14 is too new." >&2
    exit 1
  fi
fi

if [[ ! -f "$DIR/api.py" ]]; then
  git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git "$DIR"
fi

"$PY311" -m venv "$DIR/.venv"
# shellcheck disable=SC1091
source "$DIR/.venv/bin/activate"
python -m pip install -U pip wheel setuptools

# Apple Silicon: default PyPI torch includes MPS. Do not use the CPU-only index
# that upstream install.sh uses for --device MPS (that forces CPU wheels).
python -m pip install torch torchaudio
python -m pip install -r "$DIR/extra-req.txt" --no-deps || true

# Native C-extension packages need a working Xcode CLT + accepted license on macOS
# (pyopenjtalk, jieba_fast, opencc). Install the rest first so the venv is usable
# for documenting/start attempts; full upstream install still needs those three.
grep -vE '^(pyopenjtalk|jieba_fast|opencc)([=<>]|$)' "$DIR/requirements.txt" > /tmp/gpt-sovits-req-soft.txt
set +e
python -m pip install -r /tmp/gpt-sovits-req-soft.txt
SOFT_RC=$?
python -m pip install -r "$DIR/requirements.txt"
REQ_RC=$?
set -e
if [[ $REQ_RC -ne 0 ]]; then
  echo ""
  echo "NOTE: full requirements install incomplete (often pyopenjtalk / jieba_fast / opencc)."
  echo "Soft deps exit=$SOFT_RC; full exit=$REQ_RC."
  echo "  1) sudo xcodebuild -license   # accept once in Terminal"
  echo "  2) xcode-select --install     # if Command Line Tools missing"
  echo "  3) re-run: $DIR/.venv/bin/pip install pyopenjtalk jieba_fast opencc"
  echo "     or:     $DIR/.venv/bin/pip install -r $DIR/requirements.txt"
  echo "Continuing without claiming a complete engine install."
fi

cat <<EOF

GPT-SoVITS checkout: $DIR
Venv: $DIR/.venv

OpenFabric env (add to backend/.env):
  OPENFABRIC_GPT_SOVITS_DIR=$DIR
  OPENFABRIC_GPT_SOVITS_API_URL=http://127.0.0.1:9880

=== Manual weight download (NOT done by this script) ===
From upstream README https://github.com/RVC-Boss/GPT-SoVITS :

1) Pretrained models → GPT_SoVITS/pretrained_models
   https://huggingface.co/lj1995/GPT-SoVITS
   (or run upstream install.sh which fetches a zip — multi-GB)

2) Optional Chinese G2PW → GPT_SoVITS/text/G2PWModel
3) Optional UVR5 / ASR extras — only if you use those tools

=== Start the local API (after weights exist) ===
  cd $DIR
  source .venv/bin/activate
  python api.py -a 127.0.0.1 -p 9880 -d cpu
  # api.py is the pinned OpenFabric entrypoint (POST / with refer_wav_path, …).

Then restart OpenFabric Studio backend and try Voice → speech trial.

EOF
