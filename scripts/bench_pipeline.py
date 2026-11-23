"""Synthetic traffic benchmark for the full ingest pipeline.

Simulates the collector feeding posts into the pipeline (the same
``create_post`` + ``process_post`` path used by the API) and reports
throughput and a ground-truth confusion matrix, so performance claims on the
`/metrics` page are measured, not asserted.

Usage:
    python scripts/bench_pipeline.py                 # 600 posts from the bundle
    python scripts/bench_pipeline.py --n 2000 --media 8
Writes backend/artifacts/pipeline_bench.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sklearn.metrics import confusion_matrix, f1_score  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Alert, Evidence, Identifier, Post  # noqa: E402
from app.db.session import SessionLocal, init_db, session_scope  # noqa: E402
from app.ml.engine import engine  # noqa: E402
from app.pipeline import ingest  # noqa: E402

LABELS = ["drugs", "arms", "fake_docs", "mule_fin", "benign"]
CAT_TO_LABEL = {
    "drugs": "drugs", "arms": "arms", "fake_docs": "fake_docs", "mule_account": "mule_fin",
    "atm_card": "mule_fin", "sim_card": "mule_fin", "net_banking_kit": "mule_fin",
    "bank_account": "mule_fin", "atm_debit_card": "mule_fin", "credit_card": "mule_fin",
    "sim": "mule_fin",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--media", type=int, default=0, help="posts that include an image/audio file")
    args = ap.parse_args()

    generated = settings.DATA_DIR / "generated"
    posts = json.loads((generated / "posts.json").read_text(encoding="utf-8"))
    posts = posts[: args.n]
    init_db()
    # isolated channel namespace so the benchmark never mixes with the demo data
    with SessionLocal() as db:
        ids = [p["id"] for p in db.query(Post.id).filter(Post.channel.like("bench_%")).all()]
        if ids:
            db.execute(delete(Evidence).where(Evidence.alert_id.in_(
                db.query(Alert.id).filter(Alert.post_id.in_(ids))
            )))
            db.execute(delete(Alert).where(Alert.post_id.in_(ids)))
            db.execute(delete(Identifier).where(Identifier.post_id.in_(ids)))
            db.execute(delete(Post).where(Post.id.in_(ids)))
            db.commit()

    eng = engine()
    t0 = time.time()
    truth, pred, media_done = [], [], 0
    with session_scope() as db:
        for p in posts:
            media = None
            if media_done < args.media and (p.get("image_path") or p.get("audio_path")):
                cand = generated / (p.get("image_path") or p.get("audio_path"))
                media = cand if cand.exists() else None
                media_done += 1
            post = ingest.create_post(db, text=p["text"], channel=f"bench_{p['channel'].lstrip('@')}",
                                      timestamp=p["timestamp"], external_id=p["post_id"],
                                      platform="benchmark")
            res = ingest.process_post(db, post, media_path=media)
            flagged_cat = None
            if res.alert_id:
                alert = db.get(Alert, res.alert_id)
                flagged_cat = CAT_TO_LABEL.get(alert.category, "benign") if alert else None
            truth.append(p["label"])
            pred.append(flagged_cat or "benign")
    elapsed = time.time() - t0
    cm = confusion_matrix(truth, pred, labels=LABELS).tolist()
    result = {
        "posts": len(posts),
        "seconds": round(elapsed, 2),
        "posts_per_second": round(len(posts) / elapsed, 1),
        "media_processed": media_done,
        "macro_f1_pipeline": round(float(f1_score(truth, pred, average="macro")), 4),
        "labels": LABELS,
        "confusion_matrix": cm,
        "false_positives": sum(1 for t, q in zip(truth, pred) if t == "benign" and q != "benign"),
        "llm_calls": 0,
        "engine_version": eng.model.version if eng.model else "lexicon-fallback",
    }
    out = settings.ARTIFACT_DIR / "pipeline_bench.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
