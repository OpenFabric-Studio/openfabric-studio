#!/usr/bin/env bash
# Kokoro preset narration. No weights. Does not replace GPT-SoVITS.
# Catalog-controlled optional setup. This wrapper does not download model weights.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" kokoro "$@"
