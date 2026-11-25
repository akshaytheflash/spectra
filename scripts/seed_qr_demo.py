"""Attach real QR posters to a few seeded posts and run OCR/QR extraction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Alert, Post  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.pipeline import ingest  # noqa: E402

GEN = settings.DATA_DIR / "generated"


def main() -> None:
    init_db()
    posts_meta = json.loads((GEN / "posts.json").read_text(encoding="utf-8"))
    qr_meta = [p for p in posts_meta if p.get("image_kind") == "qr" and p.get("image_path")][:3]
    done = 0
    for meta in qr_meta:
        with session_scope() as db:
            post = db.execute(select(Post).where(Post.external_id == meta["post_id"])).scalar_one_or_none()
            if post is None or (post.qr or {}).get("codes"):
                continue
            path = GEN / meta["image_path"]
            if not path.exists():
                continue
            print(f"[qr] processing {meta['post_id']} {path}")
            ingest.process_post(db, post, media_path=path, do_ocr=True, max_ocr_passes=1, do_llm=False)
            codes = (post.qr or {}).get("codes") or []
            print(f"[qr] {meta['post_id']} codes={[c.get('data','')[:70] for c in codes]}")
            if codes:
                done += 1
                alert = db.execute(select(Alert).where(Alert.post_id == post.id)).scalar_one_or_none()
                if alert is not None:
                    alert.explanation = {**(alert.explanation or {}), "qr": post.qr}
                    upi = [i.value for i in post.identifiers if i.type == "upi"]
                    summary = dict(alert.identifier_summary or {})
                    values = list(summary.get("values") or [])
                    for v in upi:
                        if f"upi:{v}" not in values:
                            values.append(f"upi:{v}")
                    summary["values"] = values[:12]
                    summary["types"] = sorted(set(summary.get("types") or []) | ({"upi"} if upi else set()))
                    alert.identifier_summary = summary
    print(f"[qr] posts with decoded QR: {done}")


if __name__ == "__main__":
    main()
