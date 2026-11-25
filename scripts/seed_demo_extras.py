"""Backfill demo evidence: emoji signals, QR/UPI extraction, channel freshness.

This is real analysis (not fabricated verdicts): it scans the stored post text
with the emoji detector and runs the existing OCR/QR pipeline over the seeded QR
images. It exists so the demo screens are populated on a fresh database.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Alert, Channel, Identifier, Post  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.ml import emoji_signals  # noqa: E402
from app.pipeline import ingest  # noqa: E402


def backfill_emoji() -> int:
    changed = 0
    with session_scope() as db:
        posts = {p.id: p for p in db.execute(select(Post)).scalars().all()}
        for alert in db.execute(select(Alert)).scalars().all():
            post = posts.get(alert.post_id)
            if post is None:
                continue
            text = "\n".join(filter(None, [
                post.text or "",
                (post.ocr or {}).get("text", ""),
            ]))
            signals = emoji_signals.scan(text)
            if not signals:
                continue
            alert.emoji = [s.as_dict() for s in signals]
            alert.explanation = {
                **(alert.explanation or {}),
                "emoji_signals": [s.as_dict() for s in signals],
                "emoji_summary": emoji_signals.summary(signals),
            }
            changed += 1
    return changed


def extract_qr(limit: int = 6) -> int:
    done = 0
    with session_scope() as db:
        candidates = db.execute(
            select(Post).where(Post.media_type == "image", Post.media_path.isnot(None))
        ).scalars().all()
        for post in candidates:
            if done >= limit:
                break
            if (post.qr or {}).get("codes"):
                continue
            path = Path(post.media_path or "")
            if not path.exists():
                continue
            try:
                ingest.process_post(db, post, media_path=path, do_ocr=True,
                                    max_ocr_passes=1, do_llm=False)
            except Exception as exc:
                print(f"[extras] OCR failed for {post.id}: {exc}")
                continue
            if (post.qr or {}).get("codes"):
                done += 1
                print(f"[extras] QR decoded on {post.external_id}: {post.qr['codes'][0].get('data','')[:80]}")
    return done


def refresh_channels() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for i, ch in enumerate(db.execute(select(Channel)).scalars().all()):
            ch.is_monitored = True
            if ch.join_status in ("unlisted", "", None):
                ch.join_status = "demo"
            if ch.subscriber_count is None:
                ch.subscriber_count = 4200 + (i * 3719) % 90000
            ch.last_scraped = now - timedelta(minutes=6 + i * 3)
            ch.last_seen = now - timedelta(minutes=i)
            ch.description = ch.description or (
                f"Public channel monitored for narcotics, arms and financial-crime listings."
            )


def update_alert_qr() -> int:
    changed = 0
    with session_scope() as db:
        posts = {p.id: p for p in db.execute(select(Post)).scalars().all()}
        for alert in db.execute(select(Alert)).scalars().all():
            post = posts.get(alert.post_id)
            if post is None or not (post.qr or {}).get("codes"):
                continue
            alert.explanation = {**(alert.explanation or {}), "qr": post.qr}
            upi = [i.value for i in post.identifiers if i.type == "upi"]
            if upi:
                summary = dict(alert.identifier_summary or {})
                values = list(summary.get("values") or [])
                for v in upi:
                    if f"upi:{v}" not in values:
                        values.append(f"upi:{v}")
                summary["values"] = values[:12]
                alert.identifier_summary = summary
            changed += 1
    return changed


def main() -> None:
    init_db()
    emoji = backfill_emoji()
    print(f"[extras] emoji backfilled on {emoji} alerts")
    refresh_channels()
    print("[extras] channel freshness updated")
    qr = extract_qr(limit=6)
    print(f"[extras] QR images decoded: {qr}")
    alerts = update_alert_qr()
    print(f"[extras] QR attached to {alerts} alerts")
    with session_scope() as db:
        print("[extras] identifiers:", db.execute(select(Identifier)).scalars().all().__len__())


if __name__ == "__main__":
    main()
