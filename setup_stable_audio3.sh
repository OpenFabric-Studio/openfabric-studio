#!/usr/bin/env bash
# Official Stable Audio 3 MLX source/dependencies only; no weight bootstrap.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" stable_audio3 "$@"
