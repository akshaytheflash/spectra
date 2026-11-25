"""Cross-platform environment bootstrap for SPECTRA (macOS / Linux / Windows).

Used by ``make setup``, ``scripts/setup.sh`` and the Windows ``setup.ps1``.
Everything here is pure Python so it behaves the same on every OS:

* picks the interpreter inside ``.venv`` (``bin/python`` or ``Scripts/python.exe``),
* installs backend (CPU torch) + frontend dependencies if missing,
* sets the per-platform environment quirk fixes
  (libgomp preload on Linux only, ``KMP_DUPLICATE_LIB_OK`` on macOS),
* generates data, trains, seeds, builds the dashboard, warms search.

Usage:
    python scripts/setup.py            # full, idempotent
    python scripts/setup.py --force    # regenerate everything
    python scripts/setup.py --skip-seed
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
UVICORN = VENV / ("Scripts/uvicorn.exe" if IS_WINDOWS else "bin/uvicorn")


# --------------------------------------------------------------------------- env
def venv_env() -> dict:
    """Environment for every child process (ML caches, thread caps, TLS fixes)."""
    env = dict(os.environ)
    env.setdefault("HF_HOME", str(ROOT / ".models" / "hf"))
    env.setdefault("EASYOCR_MODULE_PATH", str(ROOT / ".models" / "easyocr"))
    env.setdefault("OMP_NUM_THREADS", "2")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if IS_WINDOWS:
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")  # torch + sklearn OpenMP
        env.setdefault("PYTHONUTF8", "1")
    elif sys.platform == "darwin":
        # macOS arm64: Intel OpenMP + libomp can load twice; allow it.
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    elif sys.platform.startswith("linux"):
        # Some glibc builds cannot dlopen statically-linked libgomp (torch +
        # scikit-learn); preloading the bundled copy fixes it. Linux only.
        if "LD_PRELOAD" not in env:
            libs = list((VENV / "lib").glob("python*/site-packages/scikit_learn.libs/libgomp-*.so*"))
            if libs:
                env["LD_PRELOAD"] = str(libs[0])
    return env


# --------------------------------------------------------------------------- steps
def run(cmd: list[str], label: str, cwd: Path | None = None) -> float:
    t0 = time.time()
    print(f"\n== {label} ==", flush=True)
    subprocess.run(cmd, cwd=str(cwd or ROOT), env=venv_env(), check=True)
    dt = time.time() - t0
    print(f"   done in {dt:.1f}s", flush=True)
    return dt


def exist(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ensure_venv() -> None:
    if PY.exists():
        return
    print(f"== creating virtualenv at {VENV} ==")
    candidates = ["python3.12", "python3.11", "python3", "python"] if not IS_WINDOWS else ["py", "python"]
    created = False
    for c in candidates:
        if not exist(c):
            continue
        args = [c, "-3.11", "-m", "venv", str(VENV)] if c == "py" else [c, "-m", "venv", str(VENV)]
        try:
            subprocess.run(args, check=True)
            created = True
            break
        except subprocess.CalledProcessError:
            continue
    if not created:
        raise SystemExit(
            "Could not create a Python virtualenv. Install Python 3.11 or 3.12 "
            "(python.org) and re-run."
        )


def python_ok() -> bool:
    if not PY.exists():
        return False
    r = subprocess.run([str(PY), "-c", "import fastapi, sklearn, sentence_transformers, easyocr"],
                       env=venv_env(), capture_output=True)
    return r.returncode == 0


def ensure_deps(force: bool) -> None:
    ensure_venv()
    if python_ok() and not force:
        print("[deps] backend packages present")
    else:
        print("[deps] installing backend packages (first run, several minutes)…", flush=True)
        run([str(PY), "-m", "pip", "install", "-q", "--upgrade", "pip"], "pip upgrade")
        # CPU-only torch wheels: much smaller than the default CUDA/MPS bundles.
        run([str(PY), "-m", "pip", "install", "-q", "--extra-index-url",
             "https://download.pytorch.org/whl/cpu", "torch==2.4.1"], "install torch (CPU)")
        run([str(PY), "-m", "pip", "install", "-q", "-r", "backend/requirements.txt"], "install requirements")

    if not exist("npm"):
        raise SystemExit(
            "npm not found. Install Node.js 20 LTS (includes npm) from https://nodejs.org "
            "or your package manager, then re-run."
        )
    if not (ROOT / "frontend" / "node_modules").exists():
        run(["npm", "install", "--silent"], "install frontend packages", cwd=ROOT / "frontend")


def has_generated() -> bool:
    return (ROOT / "data" / "generated" / "posts.json").exists()


def has_model() -> bool:
    return (ROOT / "backend" / "artifacts" / "models" / "meta.pkl").exists()


def db_counts() -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import func, select

    from app.db.models import Alert, Post
    from app.db.session import init_db, session_scope

    init_db()
    with session_scope() as db:
        return {
            "posts": db.execute(select(func.count(Post.id))).scalar_one(),
            "alerts": db.execute(select(func.count(Alert.id))).scalar_one(),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate data/model/DB")
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--deps-only", action="store_true", help="install dependencies and stop")
    ap.add_argument("--seed-media", type=int, default=1)
    args = ap.parse_args()
    force = args.force or os.environ.get("FORCE") == "1"
    t_all = time.time()

    print(f"SPECTRA setup · {platform.system()} {platform.machine()} · Python {sys.version.split()[0]}")
    ensure_deps(force)
    if args.deps_only:
        print("\ndependencies ready.")
        return

    if force or not has_generated():
        run([str(PY), "scripts/bootstrap_assets.py"], "TTS voices (Piper)")
        run([str(PY), "scripts/generate_synthetic.py", "--n", "2000"], "synthetic dataset")
    else:
        print("\n== synthetic dataset ==\n   present (skip)")

    if force or not has_model():
        run([str(PY), "backend/app/ml/train.py"], "train hybrid engine")
    else:
        print("\n== model ==\n   present (skip)")

    if not args.skip_seed:
        counts = db_counts()
        if force or counts["posts"] < 1000:
            run([str(PY), "-u", "scripts/seed.py", "--max-media", str(args.seed_media)],
                "seed database + pipeline")
            run([str(PY), "-u", "scripts/seed.py", "--phase", "derive"], "campaigns + workflow")
        else:
            print(f"\n== database ==\n   already seeded ({counts['posts']} posts, {counts['alerts']} alerts)")

    run(["npm", "run", "build"], "build dashboard")
    shutil.rmtree(ROOT / "spectra-app", ignore_errors=True)
    shutil.copytree(ROOT / "frontend" / "dist", ROOT / "spectra-app")
    print("   prebuilt bundle refreshed in spectra-app/")

    index = ROOT / "backend" / "artifacts" / "embed_cache.npz"
    if not index.exists() or force:
        run([str(PY), "scripts/build_search_index.py"], "semantic search index")
    else:
        print("\n== search index ==\n   present (skip)")

    print(f"\nsetup complete in {time.time() - t_all:.0f}s")
    print("next:  make run      (or  python scripts/run_dev.py  on Windows)")


if __name__ == "__main__":
    main()
