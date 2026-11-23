"""Application configuration loaded from environment / .env.

All credentials come from the environment.  The prototype supports two
storage backends:

* ``DATABASE_URL`` pointing at PostgreSQL (Supabase) -- the production path.
* A local SQLite file (``SPECTRA_SQLITE_PATH``) used automatically when no
  PostgreSQL URL is configured, so the demo runs with zero infrastructure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # .../hackathon


def _env_files() -> List[Path]:
    return [ROOT / ".env", ROOT / "backend" / ".env"]


class Settings(BaseSettings):
    """Runtime settings for SPECTRA."""

    model_config = SettingsConfigDict(
        env_file=[str(p) for p in _env_files()],
        extra="ignore",
        case_sensitive=False,
    )

    # --- Supabase / database -------------------------------------------------
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    # Service-role key is only used server-side for Storage uploads.
    SUPABASE_SERVICE_KEY: str = ""
    DATABASE_URL: str = ""  # postgresql+psycopg2://... or sqlite:///...
    SUPABASE_JWT_SECRET: str = ""

    STORAGE_MODE: str = "local"  # "local" or "supabase"

    # --- paths ---------------------------------------------------------------
    DATA_DIR: Path = ROOT / "data"
    UPLOAD_DIR: Path = ROOT / "data" / "uploads"
    REPORT_DIR: Path = ROOT / "data" / "reports"
    ARTIFACT_DIR: Path = ROOT / "backend" / "artifacts"
    MODEL_DIR: Path = ROOT / "backend" / "artifacts" / "models"
    SQLITE_PATH: Path = ROOT / "data" / "spectra.db"

    # --- collector -----------------------------------------------------------
    TELEGRAM_API_ID: str = ""
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION: str = ""  # path to a .session created by `make telegram-login`
    TELEGRAM_BATCH_SIZE: int = 100
    TELEGRAM_MAX_MESSAGES: int = 500

    # --- ML ------------------------------------------------------------------
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    WHISPER_MODEL: str = "base"
    ENABLE_EMBEDDINGS: bool = True
    ENABLE_EASYOCR: bool = True
    OCR_LANGS: str = "en,hi"

    # --- security ------------------------------------------------------------
    AUTH_MODE: str = "open"  # "open" (demo) or "jwt" (Supabase Auth)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:4173"

    model_config = SettingsConfigDict(
        env_file=[str(p) for p in _env_files()],
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=("settings_",),
    )

    @property
    def database_url(self) -> str:
        """Resolved SQLAlchemy URL (PostgreSQL preferred, SQLite fallback)."""
        url = self.DATABASE_URL.strip()
        if not url:
            return f"sqlite:///{self.SQLITE_PATH}"
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://") :]
        return url

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def use_supabase_storage(self) -> bool:
        return self.STORAGE_MODE.lower() == "supabase" and bool(self.SUPABASE_URL and self.SUPABASE_SERVICE_KEY)

    def ensure_dirs(self) -> None:
        for p in (self.DATA_DIR, self.UPLOAD_DIR, self.REPORT_DIR, self.MODEL_DIR):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()

# Keep HuggingFace / easyocr caches inside the project so the demo is offline-friendly.
os.environ.setdefault("HF_HOME", str(ROOT / ".models" / "hf"))
os.environ.setdefault("TRANSFORMERS_OFFLINE", os.environ.get("TRANSFORMERS_OFFLINE", ""))
os.environ.setdefault("EASYOCR_MODULE_PATH", str(ROOT / ".models" / "easyocr"))
