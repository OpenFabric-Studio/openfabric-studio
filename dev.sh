#!/usr/bin/env bash
# Cross-platform source launch; optional engines are installed from Settings.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${NODE_BIN:-node}" "$ROOT/desktop/scripts/launch-source.js" --dev
