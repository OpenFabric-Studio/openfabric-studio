#!/usr/bin/env bash
# Wan 2.2 TI2V-5B via mlx-video. Apple Silicon. No weights. Not 14B, S2V, or Animate.
# Catalog-controlled optional setup. This wrapper does not download model weights.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" wan22 "$@"
