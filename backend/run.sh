#!/usr/bin/env bash
# Start the SPECTRA API with the local libgomp preload (needed on some Linux
# hosts where torch/scikit-learn static TLS collides).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
GOMP="$(ls "$ROOT"/.venv/lib/python3.11/site-packages/scikit_learn.libs/libgomp-*.so* 2>/dev/null | head -1 || true)"
if [ -n "${GOMP:-}" ] && [ -z "${LD_PRELOAD:-}" ]; then export LD_PRELOAD="$GOMP"; fi
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
exec "$ROOT/.venv/bin/uvicorn" app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" "$@"
