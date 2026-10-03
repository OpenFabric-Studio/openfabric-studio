#!/usr/bin/env bash
# Local video engine for the Video page. Not part of the API virtualenv.
# Engine/dependencies are pinned. Weights require an explicit --download-models.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" video "$@"
