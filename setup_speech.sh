#!/usr/bin/env bash
# Catalog-controlled optional setup; weights require explicit --download-models.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" speech "$@"
