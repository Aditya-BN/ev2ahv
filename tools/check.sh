#!/bin/bash
# Run the same checks as CI (.github/workflows/ci.yml) on the bastion, which has no pytest or make.
# Usage: tools/check.sh [python]   (default: .venv/bin/python if present, else python3)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${1:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
"$PY" -B -m unittest discover -s tests
"$PY" -B tools/gen_openapi.py --check
if [ -x web/node_modules/.bin/tsc ]; then (cd web && ./node_modules/.bin/tsc --noEmit -p .) && echo "web: tsc ok"
else echo "web: skipped (run npm ci in web/ first)"; fi
