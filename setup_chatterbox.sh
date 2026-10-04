#!/usr/bin/env bash
# Chatterbox voice clone. No weights. Turbo is not a Mac path.
# Catalog-controlled optional setup. This wrapper does not download model weights.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" chatterbox "$@"
