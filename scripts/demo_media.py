"""Produce one OCR'd poster alert and one transcribed voice-note alert.

Runs the real media pipeline (multi-pass OCR + QR, faster-whisper) end-to-end
and prints what was recovered, so the evidence view can be inspected right
after `make run`. Re-runs are no-ops unless ``--force`` is given.

Usage:
    python scripts/demo_media.py                 # poster + voice (first run only)
    python scripts/demo_media.py --poster --voice --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Alert, Post  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.pipeline import ingest  # noqa: E402

MARKER = settings.DATA_DIR / "demo_media.json"
CHANNEL = "demo_media_lab"


def _pick(image: bool) -> dict | None:
    posts = json.loads((settings.DATA_DIR / "generated" / "posts.json").read_text(encoding="utf-8"))
    for p in posts:
        path = p.get("image_path") if image else p.get("audio_path")
        if path and p.get("label") != "benign":
            if image and (p.get("image_kind") != "poster" or p.get("media_variant") == "harsh"):
                continue
            return p
    return None


def run_one(kind: str, force: bool) -> str:
    src = _pick(image=(kind == "poster"))
    if src is None:
        return f"{kind}: no suitable synthetic media found"
    media = settings.DATA_DIR / "generated" / (src["image_path"] if kind == "poster" else src["audio_path"])
    with session_scope() as db:
        existing = db.execute(select(Post).where(Post.external_id == f"demo_{kind}")).scalar_one_or_none()
        if existing is not None and not force:
            return f"{kind}: already ingested (post {existing.id[:8]})"
        if existing is not None and force:
            db.delete(existing)
            db.flush()
        post = ingest.create_post(
            db, text=src["text"], channel=CHANNEL, timestamp=src["timestamp"],
            external_id=f"demo_{kind}", platform="demo",
        )
        t0 = time.time()
        res = ingest.process_post(db, post, media_path=media, max_ocr_passes=2)
        alert = db.execute(select(Alert).where(Alert.post_id == post.id)).scalar_one_or_none()
        summary = {
            "kind": kind,
            "media": str(media.relative_to(settings.DATA_DIR)),
            "post_id": post.id,
            "alert_id": alert.id if alert else None,
            "category": res.category,
            "risk": res.risk,
            "seconds": round(time.time() - t0, 1),
        }
        if kind == "voice":
            summary["transcript"] = (post.transcript or {}).get("text", "")[:220]
        else:
            summary["ocr_text"] = (post.ocr or {}).get("text", "")[:220]
            summary["qr"] = [c.get("data") for c in (post.qr or {}).get("codes", [])]
    return json.dumps(summary)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poster", action="store_true")
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    kinds = []
    if args.poster:
        kinds.append("poster")
    if args.voice:
        kinds.append("voice")
    if not kinds:
        kinds = ["poster", "voice"]
    init_db()
    results = []
    for kind in kinds:
        line = run_one(kind, args.force)
        print(f"[demo-media] {line}", flush=True)
        results.append(line)
    MARKER.write_text(json.dumps({"runs": results, "at": time.time()}, indent=1))


if __name__ == "__main__":
    main()
