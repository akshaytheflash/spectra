"""SPECTRA API entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import AuthMiddleware
from app.db.session import init_db
from app.pipeline import autopilot
from app.routes import alerts, analysis, analytics, channels, identity, ingest, lexicon, ops, reports


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # warm the engine so the first request is fast
    from app.ml.engine import engine

    engine()
    # analyst pager + autonomous monitoring (no-ops without credentials)
    from app.bot.service import start_bot, stop_bot

    start_bot()
    autopilot.start()
    try:
        yield
    finally:
        stop_bot()
        await autopilot.stop()


app = FastAPI(
    title="SPECTRA API",
    description="Social Platform Entity Correlation & Threat Recognition Analytics",
    version="1.0.0",
    lifespan=lifespan,
)

_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
# Browsers send `Origin: null` for pages opened from file:// (the static demo
# bundle). It is allowed explicitly so the dashboard works from disk too.
if "null" not in _origins:
    _origins.append("null")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_origin_regex=r"https://([a-z0-9-]+\.)*(vercel\.app|trycloudflare\.com|loca\.lt|ngrok-free\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

# Notifications first: static `/alerts/recent` must resolve before `/alerts/{alert_id}`.
app.include_router(reports.notify_router)
for r in (analysis, ingest, reports, alerts, analytics, lexicon, channels, identity, ops):
    app.include_router(r.router)

# Evidence media (originals) served read-only for the evidence view.
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(settings.UPLOAD_DIR)), name="media")


@app.get("/health")
def health() -> dict:
    from app.bot.service import bot_status
    from app.ml.engine import get_model
    from app.pipeline import autopilot as pilot
    from app.pipeline import telegram as tg

    model = get_model()
    return {
        "status": "ok",
        "database": "postgresql" if settings.is_postgres else "sqlite",
        "storage": "supabase" if settings.use_supabase_storage else "local",
        "model": model.version if model else "not-trained",
        "auth": settings.AUTH_MODE,
        "llm": {"configured": settings.llm_ready, "model": settings.BHARATCODE_MODEL},
        "telegram": {"session": tg.live_available(), "bot": bot_status()},
        "autopilot": pilot.status(),
    }


# Serve the built frontend from the API origin (docker / single-process demo).
# Mounted last so all /api routes above win; unknown paths fall back to the SPA.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    from fastapi.responses import FileResponse as _FileResponse

    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> _FileResponse:
        return _FileResponse(_dist / "index.html")

    @app.get("/{spa_path:path}", include_in_schema=False)
    def spa(spa_path: str) -> _FileResponse:
        candidate = _dist / spa_path
        if candidate.is_file():
            return _FileResponse(candidate)
        return _FileResponse(_dist / "index.html")
