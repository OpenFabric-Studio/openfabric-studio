#!/usr/bin/env bash
# Stable Audio 3 MLX setup; --download-models includes the pinned Small Music bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" stable_audio3 "$@"
