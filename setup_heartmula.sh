#!/usr/bin/env bash
# HeartMuLa MLX setup; --download-models includes pinned weights and conversion.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" heartmula "$@"
