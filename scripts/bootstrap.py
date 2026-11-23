"""Bootstrap: train (if needed), generate data (if needed), seed (if empty).

Used by `make run` and the Docker entrypoint. Every step is idempotent so a
second run is fast; the first run is what the 3-minute demo budget covers.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
PY = sys.executable


def _env() -> dict:
    env = dict(os.environ)
    if "LD_PRELOAD" not in env:
        libs = list((ROOT / ".venv" / "lib").glob("python3.11/site-packages/scikit_learn.libs/libgomp-*.so*"))
        if libs:
            env["LD_PRELOAD"] = str(libs[0])
    env.setdefault("HF_HOME", str(ROOT / ".models" / "hf"))
    env.setdefault("OMP_NUM_THREADS", "2")
    return env


def run(cmd: list[str], label: str) -> None:
    t0 = time.time()
    print(f"\n== {label} ==", flush=True)
    subprocess.run(cmd, cwd=str(ROOT), env=_env(), check=True)
    print(f"   done in {time.time() - t0:.1f}s", flush=True)


def counts() -> dict:
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
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quick", action="store_true", help="skip media OCR/STT while seeding")
    ap.add_argument("--media", type=int, default=1, help="media posts analysed at seed time")
    args = ap.parse_args()

    from app.core.config import settings
    from app.db.models import Post
    from app.db.session import init_db, session_scope

    # Remove benchmark rows left in a shared database before checking the seed.
    # (A DB remove bypasses the ORM cascade that would null alerts.post_id.)
    from sqlalchemy import delete as _delete

    from app.db.models import Alert as _Alert, Evidence as _Evidence, Identifier as _Identifier

    init_db()
    with session_scope() as db:
        bench_ids = [r[0] for r in db.execute(
            __import__("sqlalchemy").select(Post.id).where(Post.channel.like("bench_%"))
        ).all()]
        if bench_ids:
            alert_ids = [r[0] for r in db.execute(
                __import__("sqlalchemy").select(_Alert.id).where(_Alert.post_id.in_(bench_ids))
            ).all()]
            db.execute(_delete(_Evidence).where(_Evidence.alert_id.in_(alert_ids)))
            db.execute(_delete(_Alert).where(_Alert.post_id.in_(bench_ids)))
            db.execute(_delete(_Identifier).where(_Identifier.post_id.in_(bench_ids)))
            db.execute(_delete(Post).where(Post.id.in_(bench_ids)))
            print(f"[bootstrap] removed {len(bench_ids)} benchmark posts")

    gen = settings.DATA_DIR / "generated" / "posts.json"
    if args.force or not gen.exists():
        run([PY, "scripts/bootstrap_assets.py"], "fetch TTS voices (Piper)")
        run([PY, "scripts/generate_synthetic.py", "--n", "2000"], "generate synthetic dataset")

    model = settings.MODEL_DIR / "meta.pkl"
    if args.force or not model.exists():
        run([PY, "backend/app/ml/train.py"], "train hybrid engine")

    if args.seed:
        c = counts()
        if args.force or c["posts"] < 1000:
            cmd = [PY, "scripts/seed.py", "--max-media", str(args.media)]
            if args.quick:
                cmd.append("--quick")
            run(cmd, "seed database + run pipeline")
            run([PY, "scripts/seed.py", "--phase", "derive"], "campaigns + workflow")
        else:
            print(f"[bootstrap] database already seeded ({c['posts']} posts, {c['alerts']} alerts)")


if __name__ == "__main__":
    main()
