# SPECTRA - one-command local demo (macOS / Linux; Windows uses run.ps1)
#
#   make run      -> bootstrap (if needed) + serve API & dashboard on :8000
#   make dev      -> same + Vite dev server on :5173 (hot reload)
#   make test     -> unit + API + vision/speech tests
#
# All heavy lifting lives in scripts/setup.py and scripts/run_dev.py so the same
# commands work on every OS, including Windows (see setup.ps1 / run.ps1).
SHELL := /bin/bash
VENV   := .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
export HF_HOME := $(CURDIR)/.models/hf
export OMP_NUM_THREADS ?= 2
# Only Linux needs the libgomp preload (empty on macOS, harmless there).
ifeq ($(shell uname -s),Linux)
GOMP   := $(firstword $(wildcard $(VENV)/lib/python*/site-packages/scikit_learn.libs/libgomp-*.so*))
export LD_PRELOAD := $(GOMP)
endif

.PHONY: help setup env data model seed seed-quick api web run dev test lint migrate predict telegram-login clean

help:
	@echo "SPECTRA targets (macOS / Linux):"
	@echo "  make run       setup if needed, then serve API+dashboard on http://localhost:8000"
	@echo "  make dev       same, plus the Vite dev server on http://localhost:5173"
	@echo "  make setup     install deps + generate data + train + seed + build dashboard"
	@echo "  make data      generate the synthetic dataset (2000 posts, posters, QR, voices)"
	@echo "  make model     train the hybrid engine and write metrics"
	@echo "  make seed      load generated data (2 media posts with real OCR/STT)"
	@echo "  make seed-quick  text-only seed (fastest)"
	@echo "  make api       FastAPI only on :8000 (Swagger at /docs)"
	@echo "  make web       Vite dev server on :5173"
	@echo "  make test      run the test suite"
	@echo "  make migrate   apply backend/migrations/001_init.sql to \$$DATABASE_URL (Supabase)"
	@echo "  make predict   re-classify stored posts (after lexicon edits)"
	@echo ""
	@echo "  Windows: use .\\setup.ps1 then .\\run.ps1 (see README)."

env:
	@$(PY) -c "import fastapi, sklearn, sentence_transformers" 2>/dev/null || \
	  $(PY) scripts/setup.py --deps-only

setup:
	@$(PY) scripts/setup.py

run: setup
	@echo "== starting SPECTRA (single process group; Ctrl-C stops everything) =="
	$(PY) scripts/run_dev.py --host 0.0.0.0 --port 8000

dev: setup
	$(PY) scripts/run_dev.py --port 8000 --dev

data:
	$(PY) scripts/bootstrap_assets.py
	$(PY) scripts/generate_synthetic.py --n 2000

model:
	$(PY) backend/app/ml/train.py

seed:
	$(PY) -u scripts/seed.py --max-media 2 --ocr
	$(PY) -u scripts/seed.py --phase derive

seed-quick:
	$(PY) -u scripts/seed.py --quick
	$(PY) -u scripts/seed.py --phase derive

api:
	cd backend && HOST=0.0.0.0 PORT=8000 ./run.sh

web:
	cd frontend && npm run dev

predict:
	$(PY) -u scripts/reanalyze.py

test:
	OMP_NUM_THREADS=1 $(PY) -m pytest -q backend/tests

lint:
	$(PY) -m compileall -q backend/app scripts

migrate:
	@test -n "$(DATABASE_URL)" || (echo "set DATABASE_URL (Supabase Postgres URI) first"; exit 1)
	psql "$(DATABASE_URL)" -v ON_ERROR_STOP=1 -f backend/migrations/001_init.sql

telegram-login:
	$(PY) scripts/telegram_login.py

clean:
	rm -rf data/generated data/uploads data/reports data/spectra.db* backend/artifacts/models spectra-app
