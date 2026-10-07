#!/usr/bin/env bash
# Build the operator console web UI into harness_ui/static (served by `python -m harness_ui serve`).
# Reproducible: exact dependency versions come from frontend/package-lock.json (npm ci).
#   scripts/build_ui.sh            install + typecheck + lint + unit tests + build
#   scripts/build_ui.sh --quick    install + build only
set -euo pipefail
cd "$(dirname "$0")/../frontend"
command -v node >/dev/null || { echo "Node.js >= 20 is required to build the UI (https://nodejs.org)"; exit 1; }
npm ci --no-audit --no-fund
if [[ "${1:-}" != "--quick" ]]; then
  npm run typecheck
  npm run lint
  npm test
fi
npm run build
echo "UI built -> harness_ui/static. Start the console with: python -m harness_ui serve"
