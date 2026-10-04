#!/usr/bin/env bash
# RVC timbre conversion. Mac packages are CPU. No pretrained weights. Does not replace Seed-VC.
# Catalog-controlled optional setup. This wrapper does not download model weights.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/setup-feature.js" rvc "$@"
