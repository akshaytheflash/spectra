"""Shared pytest fixtures (isolated SQLite DB per test session)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'test_spectra.db'}")
os.environ.setdefault("OMP_NUM_THREADS", "1")  # keep test-time ML single threaded
os.environ.setdefault("AUTH_MODE", "open")
sys.path.insert(0, str(ROOT / "backend"))

# libgomp preload for scikit-learn (Linux only; macOS/Windows load it fine).
if sys.platform.startswith("linux"):
    try:
        libs = list((ROOT / ".venv" / "lib").glob("python*/site-packages/scikit_learn.libs/libgomp-*.so*"))
        if libs and not os.environ.get("LD_PRELOAD"):
            os.environ["LD_PRELOAD"] = str(libs[0])
    except Exception:
        pass
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT
