# Deployment & operations notes

## Local (demo)

```bash
make run          # bootstrap (idempotent) + serve dashboard & API on :8000
make dev          # same, plus Vite dev server on :5173 with hot reload
make test         # unit + API + vision/speech tests
```

Data, model and audit trail live under `data/` and `backend/artifacts/`; delete
them (or `make clean`) to start over.

## Supabase (managed PostgreSQL)

1. Create a project, then in *Project settings → Database* copy the connection
   string into `DATABASE_URL` (session pooler URI works with `psycopg2`).
2. Put `SUPABASE_URL`, `SUPABASE_KEY` (anon, used by the browser for Realtime)
   and `SUPABASE_SERVICE_KEY` (server-only, Storage uploads) in `.env`.
3. Apply the schema (tables, `pg_trgm` indexes, JSONB columns, RLS, Realtime):

   ```bash
   make migrate
   ```

4. Create analyst users in *Authentication → Users* and set
   `app_metadata: {"role": "analyst"}` (or `admin`).
5. Switch `AUTH_MODE=jwt` (verifies Supabase JWTs with `SUPABASE_JWT_SECRET`).
   Read endpoints stay public for dashboards; all writes require an analyst.
6. Set `STORAGE_MODE=supabase` to store evidence images/audio/QR originals in
   the `spectra-evidence` bucket (content-addressed by SHA-256).

RLS summary: analysts can read everything, update alert workflow, append audit
rows and edit the lexicon. `evidence` and `audit_log` are append-only. The
collector writes through the service role (server side).

## Telegram collector

```bash
# .env: TELEGRAM_API_ID, TELEGRAM_API_HASH
make telegram-login     # one-time interactive login -> .telegram_session/spectra.session
```

The collector is read-only: public broadcast channels only, bounded message
history, 0.35 s between messages, resumable via `offset_id`. Invite links,
private channels and public groups are rejected by `backend/app/core/scope.py`.
Without a session the dashboard runs the same pipeline in demo mode on the
bundled sample channel.

## Re-training / active learning

* Analyst confirmations/dismissals are appended to
  `data/generated/analyst_feedback.jsonl` (`POST /feedback`).
* `make model` retrains; feedback rows are folded in automatically.
* `make predict` re-classifies stored posts after lexicon or model changes.
* Lexicon additions from the UI are stored in `lexicon_terms` and applied
  immediately (no redeploy).

## Benchmarks

```bash
python scripts/bench_ocr.py --limit 24        # OCR identifier recall by image variant
python scripts/bench_pipeline.py --n 600      # end-to-end pipeline accuracy + throughput
```
