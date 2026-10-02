#!/usr/bin/env bash
# Pinned MSST inference engine plus the upstream-listed Kimberley vocal model.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DIR="$ROOT/external/Music-Source-Separation-Training"
COMMIT="84b1eac0887756b4f1a9d7a1ff49105939749ed2"
if [[ ! -f "$DIR/inference.py" ]]; then
  git clone --no-checkout https://github.com/ZFTurbo/Music-Source-Separation-Training.git "$DIR"
  git -C "$DIR" checkout --detach "$COMMIT"
fi
if [[ "$(git -C "$DIR" rev-parse HEAD)" != "$COMMIT" ]]; then
  echo "Existing MSST checkout differs from the tested commit; preserve it and use a separate installation." >&2
  exit 1
fi
if ! git -C "$DIR" diff --quiet HEAD --; then
  echo "Existing MSST source has local edits; preserve it and use a separate installation." >&2
  exit 1
fi
if [[ ! -f "$DIR/.venv/bin/python" ]]; then
  uv venv --python 3.12 "$DIR/.venv"
fi
uv pip install --python "$DIR/.venv/bin/python" -r "$ROOT/backend/requirements-roformer.txt"
"$DIR/.venv/bin/python" "$ROOT/backend/scripts/setup_roformer.py" --engine "$DIR" --dotenv "$ROOT/backend/.env"
