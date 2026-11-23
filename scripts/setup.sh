#!/usr/bin/env bash
# Build everything needed by the demo. The first invocation downloads the
# Python/npm dependencies and the ML models (a few minutes); afterwards it is
# a no-op. `make run` uses this.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
GOMP="$(ls "$VENV"/lib/python3.11/site-packages/scikit_learn.libs/libgomp-*.so* 2>/dev/null | head -1 || true)"
export LD_PRELOAD="${LD_PRELOAD:-$GOMP}"
export HF_HOME="${HF_HOME:-$ROOT/.models/hf}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
mkdir -p data

step() { echo; echo "== $1 =="; }

step "1/8 python environment"
if [ ! -x "$PY" ]; then
  python3.11 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV"
fi
"$PY" -c "import fastapi, sklearn, sentence_transformers" 2>/dev/null || {
  echo "installing backend dependencies (first run only)…"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q --extra-index-url https://download.pytorch.org/whl/cpu torch==2.4.1 torchvision==0.19.1
  "$PY" -m pip install -q -r backend/requirements.txt
}

step "2/8 frontend dependencies"
if [ ! -d frontend/node_modules ]; then (cd frontend && npm install --silent); fi

step "3/8 synthetic dataset"
if [ ! -f data/generated/posts.json ] || [ "${FORCE:-0}" = "1" ]; then
  "$PY" scripts/bootstrap_assets.py
  "$PY" scripts/generate_synthetic.py --n 2000
fi

step "4/8 hybrid model"
if [ ! -f backend/artifacts/models/meta.pkl ] || [ "${FORCE:-0}" = "1" ]; then
  "$PY" backend/app/ml/train.py
fi

step "5/8 database + pipeline"
COUNT="$("$PY" - <<'EOF'
from sqlalchemy import func, select
import sys
sys.path.insert(0, "backend")
from app.db.models import Post
from app.db.session import init_db, session_scope
init_db()
with session_scope() as db:
    print(db.execute(select(func.count(Post.id))).scalar_one())
EOF
)"
if [ "${FORCE:-0}" = "1" ] || [ "$COUNT" -lt 1000 ]; then
  # Text analysis runs over every post; OCR/voice is exercised on a few media
  # posts so the demo stays within the 3-minute budget (see README).
  "$PY" -u scripts/seed.py --max-media "${SEED_MEDIA:-1}" --ocr-passes 1
  "$PY" -u scripts/seed.py --phase derive
else
  echo "database already seeded ($COUNT posts)"
fi

step "6/8 dashboard build"
(cd frontend && npm run build >/dev/null 2>&1) && echo "frontend built (served by the API at :8000/)"
rm -rf spectra-app && cp -r frontend/dist spectra-app && echo "prebuilt bundle refreshed in spectra-app/"

step "7/8 media OCR demo (one poster + one voice note)"
if [ "${FORCE:-0}" = "1" ] || [ ! -f data/demo_media.json ]; then
  "$PY" scripts/demo_media.py --poster --voice || echo "(media demo skipped)"
fi

step "8/8 semantic search index (warm-up)"
if [ "${FORCE:-0}" = "1" ] || [ ! -f backend/artifacts/embed_cache.npz ]; then
  # Pre-compute post embeddings so free-text search is instant on first use.
  "$PY" scripts/build_search_index.py || echo "(search index warm-up skipped)"
fi
echo
echo "bootstrap complete. API docs: http://localhost:8000/docs   Dashboard: http://localhost:8000/ or :5173 (vite)"
