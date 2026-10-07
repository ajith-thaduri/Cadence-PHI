#!/usr/bin/env bash
# Reproduce the first execution end to end.
set -euo pipefail
cd "$(dirname "$0")/.."
python3.12 -m venv .venv 2>/dev/null || uv venv -p 3.12 .venv
.venv/bin/python -m pip install -q -r requirements.txt || uv pip install -p .venv/bin/python -r requirements.txt
OFFLINE_MODE=false .venv/bin/python -m harness.download     # network: public model artifacts only
.venv/bin/python -m pytest -q
.venv/bin/python -m harness.run_all                         # offline
