#!/usr/bin/env bash
# HeartMuLa community MLX source/dependencies only; local weights are explicit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" heartmula "$@"
