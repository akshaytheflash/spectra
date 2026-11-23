"""Re-run the hybrid engine over every ingested post (after lexicon edits or a
model retrain) and refresh campaigns.

Usage: python scripts/reanalyze.py [--rechunk]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models import Post  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.ml.engine import reset_engine  # noqa: E402
from app.ml.lexicon import set_overlay  # noqa: E402
from app.pipeline import ingest  # noqa: E402
from app.services import refresh_campaigns  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rechunk", action="store_true", help="also refresh campaign clusters")
    args = ap.parse_args()
    from app.db.models import LexiconTerm

    with session_scope() as db:
        overlay = [
            {"category": t.category, "term": t.term, "kind": t.kind, "weight": t.weight}
            for t in db.execute(select(LexiconTerm)).scalars()
        ]
    set_overlay(overlay)
    reset_engine()
    t0 = time.time()
    flagged = kept = 0
    with session_scope() as db:
        posts = db.execute(select(Post)).scalars().all()
        for p in posts:
            res = ingest.process_post(db, p)
            if res.alert_id:
                flagged += 1
            elif p.alert is not None:
                kept += 1
    if args.rechunk:
        with session_scope() as db:
            refresh_campaigns(db, force=True)
    print(f"[reanalyze] {len(posts)} posts in {time.time()-t0:.1f}s · {flagged} alerts · {kept} existing alerts kept")


if __name__ == "__main__":
    main()
