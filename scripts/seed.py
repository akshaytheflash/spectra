"""Seed script: loads the sample dataset / synthetic bundle into the database.

Usage:
    python scripts/seed.py                 # synthetic bundle + sample dataset
    python scripts/seed.py --skip-sample   # synthetic bundle only
    python scripts/seed.py --quick         # no OCR/STT for images/audio (fast)

Order matters: analysis is run *before* OCR so that media ingestion is bounded
and the demo stays inside the 3-minute `make run` budget (see README).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import uuid
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Alert,
    AuditLog,
    Campaign,
    Channel,
    Evidence,
    Identifier,
    IngestJob,
    LexiconTerm,
    Post,
)
from app.db.session import init_db, session_scope  # noqa: E402
from app.ml.engine import engine as get_engine  # noqa: E402
from app.pipeline import ingest  # noqa: E402
from app.services import load_posts_with_identifiers, refresh_campaigns  # noqa: E402

SAMPLE_DIR = Path(settings.DATA_DIR) / "sample"
GENERATED_DIR = Path(settings.DATA_DIR) / "generated"
_GENERATED_POSTS: list[dict] = []


def _load_generated_pool() -> None:
    path = GENERATED_DIR / "posts.json"
    if path.exists():
        _GENERATED_POSTS.extend(json.loads(path.read_text(encoding="utf-8")))


def reset_tables(force: bool = False) -> None:
    """Wipe seed data. Refuses to destroy a populated DB unless ``--force``."""
    with session_scope() as db:
        existing = db.execute(select(func.count(Post.id))).scalar_one()
        if existing and not force:
            raise SystemExit(
                f"refusing to reset a database with {existing} posts; "
                "re-run with --force-reset if that is really intended"
            )
        for model in (Evidence, AuditLog, Alert, Identifier, Post, Channel, Campaign, IngestJob, LexiconTerm):
            db.execute(delete(model))


def load_sample_dataset(media_root: Path | None = None) -> list[dict]:
    """Read the organizers' dataset, adapting to the real column names."""
    if not SAMPLE_DIR.exists():
        return []
    files = list(SAMPLE_DIR.glob("*.csv")) + list(SAMPLE_DIR.glob("*.json")) + list(SAMPLE_DIR.glob("*.jsonl"))
    if not files:
        return []
    rows: list[dict] = []
    for f in files:
        if f.suffix.lower() in (".json", ".jsonl"):
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("posts") or data.get("data") or [data]
            rows.extend(data)
        else:
            with open(f, encoding="utf-8-sig") as fh:
                rows.extend(list(csv.DictReader(fh)))
        media_root = media_root or f.parent
    return rows


def _stable_id(*parts: object) -> str:
    """Deterministic post id (idempotent seeds, direct links to evidence)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "spectra:" + "|".join(str(p) for p in parts)))


def _pick(row: dict, *keys: str) -> str:
    for k in keys:
        for rk, rv in row.items():
            if rk and rk.strip().lower() == k:
                return str(rv or "").strip()
    return ""


def _attach_media_sample(p: dict, rng: random.Random) -> dict:
    """Give a text post a real synthetic image / voice note for the demo.

    The ad text stays as-is (so labels remain valid); a bundled poster or voice
    note from the same category is attached so the evidence view shows OCR
    boxes, a QR decode or a transcript exactly as in production.
    """
    out = dict(p)
    kind = p.get("label")
    if kind == "benign" or out.get("image_path") or out.get("audio_path"):
        return out
    want_audio = rng.random() < 0.34
    pool = [q for q in _GENERATED_POSTS if q.get("label") == kind
            and (q.get("audio_path") if want_audio else q.get("image_path"))]
    if not pool:
        return out
    pick = rng.choice(pool)
    out["image_path"] = pick.get("image_path")
    out["audio_path"] = pick.get("audio_path")
    return out


def seed_posts(posts: list[dict], *, do_media: bool, max_media: int, max_ocr_passes: int,
               run_ocr: bool = False, llm_mode: str = "candidates", llm_limit: int = 60) -> dict:
    eng = get_engine()
    flagged = 0
    media_done = 0
    t0 = time.time()
    rng = random.Random(23)
    # Batched text classification: one TF-IDF pass + one embedding pass for all
    # posts (posts that get media are re-classified with OCR text folded in).
    precomputed: dict[str, dict] = {}
    preds: list[dict] = []
    verdicts: list = []
    if eng.model is None:
        print("[seed] no trained model found - run `make model` first")
    else:
        t_pred = time.time()
        preds = eng.predict_many([p.get("text") or "" for p in posts])
        precomputed = {p["post_id"]: pr for p, pr in zip(posts, preds) if p.get("post_id")}
        print(f"[seed] batched classification of {len(posts)} posts in {time.time()-t_pred:.1f}s")
        if llm_mode != "off":
            from app.pipeline.llm_batch import scan_texts

            t_llm = time.time()
            verdicts = scan_texts(
                [p.get("text") or "" for p in posts], preds, context="seed", mode=llm_mode,
                limit=llm_limit,
            )
            print(f"[seed] LLM scanned {sum(1 for v in verdicts if v is not None)} posts "
                  f"in {time.time()-t_llm:.1f}s")
    with session_scope() as db:
        # choose which posts carry demonstration media (spread over categories)
        media_targets: list[tuple[str, str]] = []
        if do_media and max_media > 0:
            by_label: dict[str, list[dict]] = {}
            for p in posts:
                if not (p.get("image_path") or p.get("audio_path")):
                    by_label.setdefault(p.get("label") or "benign", []).append(p)
            labels = sorted(by_label)
            i = 0
            while len(media_targets) < max_media and labels:
                lab = labels[i % len(labels)]
                bucket = by_label[lab]
                if bucket:
                    media_targets.append((bucket.pop(0)["post_id"], lab))
                else:
                    labels.remove(lab)
                    continue
                i += 1
        media_ids = {pid for pid, _ in media_targets}
        for n, p in enumerate(posts):
            if p.get("post_id") in media_ids:
                p = _attach_media_sample(p, rng)
            text = p.get("text") or ""
            pid = _stable_id(p.get("post_id") or f"{p.get('channel')}|{p.get('timestamp')}|{text[:40]}")
            post = ingest.create_post(
                db, post_id=pid, text=text, channel=p.get("channel") or "imported",
                timestamp=p.get("timestamp"), external_id=p.get("post_id"), platform=p.get("platform") or "telegram",
                label=p.get("label"),
            )
            media = None
            if do_media and media_done < max_media:
                for key in ("image_path", "audio_path"):
                    if p.get(key):
                        cand = GENERATED_DIR / p[key]
                        if not cand.exists():
                            cand = Path(p[key])
                        if cand.exists():
                            media = cand
                            break
                if media is not None:
                    media_done += 1
            # Proof-of-concept demo media is processed WITHOUT OCR/STT so the
            # bootstrap stays fast; OCR/voice run on demand (Telegram ingest,
            # /analyze/image, /analyze/audio) and via `make seed --max-media N --ocr`.
            verdict = verdicts[n] if n < len(verdicts) else None
            res = ingest.process_post(
                db, post, media_path=media, do_ocr=run_ocr,
                prediction=None if media is not None else precomputed.get(p.get("post_id")),
                llm_verdict=verdict,
                do_llm=bool(media is not None and llm_mode != "off"),
                commit=False,
            )
            if (n + 1) % 250 == 0:  # batch commit keeps memory flat
                db.commit()
            if res.alert_id:
                flagged += 1
        print(f"[seed] posts={len(posts)} alerts={flagged} media_processed={media_done} "
              f"in {time.time()-t0:.1f}s")
    return {"posts": len(posts), "alerts": flagged, "media": media_done}


def simulate_workflow(rng: random.Random) -> None:
    """Move a slice of alerts through review -> confirmed/dismissed with notes."""
    with session_scope() as db:
        alerts = db.execute(select(Alert).order_by(Alert.created_at.asc())).scalars().all()
        if not alerts:
            return
        notes = ["Matched with FIR 2026/1158", "Same UPI as campaign X", "Verified with open-source profile",
                 "False positive: pharmacy listing", "Cross-checked @handle across 3 channels"]
        for i, a in enumerate(alerts):
            r = rng.random()
            if r < 0.55:
                continue
            steps = ["under_review"]
            final = "confirmed" if r < 0.85 else "dismissed"
            steps.append(final)
            old = a.status
            for s in steps:
                db.add(AuditLog(alert_id=a.id, actor=rng.choice(["analyst.a", "analyst.b", "inspector.k"]),
                                action="status_change", from_status=old, to_status=s,
                                notes=rng.choice(notes)))
                old = s
            a.status = final
            a.analyst_notes = [{"actor": "analyst", "notes": rng.choice(notes), "at": a.created_at.isoformat(),
                                "status": final}]


def derive(rng: random.Random, campaign_sample: int) -> None:
    """Workflow state + coordination clusters (second, cache-friendly phase)."""
    from app.db.models import Post as _Post

    simulate_workflow(rng)
    with session_scope() as db:
        total = db.execute(select(func.count(_Post.id))).scalar_one()
        # hash fast path: identical posted text collapses before clustering
        posts = load_posts_with_identifiers(db)
        posts.sort(key=lambda p: (bool(p["alert_id"]), p["image_hash"] is not None), reverse=True)
        selected = _coverselect(posts[:campaign_sample]) if total > campaign_sample else posts
        camps = refresh_campaigns(db, force=True, posts=selected)
    cross = [c for c in camps if len(c["channels"]) >= 3]
    print(f"[seed] {len(camps)} campaigns; {len(cross)} span 3+ channels")
    if cross:
        print(f"[seed] top: {cross[0]['title']} -> {cross[0]['shared_identifiers'][:3]}")


def _coverselect(posts: list[dict]) -> list[dict]:
    """Keep all media/clustered posts and spread the rest across channels."""
    by_channel: dict[str, list[dict]] = {}
    for p in posts:
        by_channel.setdefault(p["channel"], []).append(p)
    out: list[dict] = []
    idx = 0
    channels = sorted(by_channel)
    while len(out) < len(posts):
        added = False
        for ch in channels:
            bucket = by_channel[ch]
            if idx < len(bucket):
                out.append(bucket[idx])
                added = True
        if not added:
            break
        idx += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-sample", action="store_true")
    ap.add_argument("--quick", action="store_true", help="skip OCR/STT for media (fast seed)")
    ap.add_argument("--max-media", type=int, default=8, help="how many media posts get OCR/STT at seed")
    ap.add_argument("--ocr-passes", type=int, default=1)
    ap.add_argument("--ocr", action="store_true", help="run OCR/STT on seeded media (slower)")
    ap.add_argument("--llm", choices=["off", "candidates", "all"], default="candidates",
                    help="LLM scan coverage while seeding (default: borderline/flagged only)")
    ap.add_argument("--llm-limit", type=int, default=60,
                    help="max posts sent to the LLM while seeding (endpoint is slow)")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--force-reset", action="store_true", help="allow wiping a populated database")
    ap.add_argument("--phase", choices=["all", "import", "derive"], default="all",
                    help="import=posts+analysis, derive=workflow+campaigns, all=both")
    ap.add_argument("--campaign-sample", type=int, default=1200,
                    help="posts considered when clustering campaigns (bounded CPU)")
    args = ap.parse_args()
    rng = random.Random(11)

    settings.ensure_dirs()
    init_db()
    start = time.time()

    # The derive phase works on existing data and must never reset it.
    if args.phase == "derive":
        derive(rng, args.campaign_sample)
        print(f"[seed] derived in {time.time()-start:.1f}s")
        return
    if not args.no_reset:
        reset_tables(force=args.force_reset)

    sample_rows = [] if args.skip_sample else load_sample_dataset()
    if sample_rows:
        sample_posts = [
            {
                "post_id": _pick(r, "post_id", "id"),
                "platform": _pick(r, "platform") or "telegram",
                "channel": _pick(r, "channel", "channel_name", "username") or "sample",
                "timestamp": _pick(r, "timestamp", "date", "datetime"),
                "text": _pick(r, "text", "message", "content"),
                "image_path": _pick(r, "image_path"),
                "audio_path": _pick(r, "audio_path"),
                "label": _pick(r, "label", "ground_truth"),
            }
            for r in sample_rows
        ]
        seed_posts(sample_posts, do_media=True, max_media=args.max_media, max_ocr_passes=args.ocr_passes,
                   run_ocr=args.ocr, llm_mode=args.llm, llm_limit=args.llm_limit)
    else:
        print("[seed] no organizers dataset in data/sample - using synthetic bundle only")

    posts_path = GENERATED_DIR / "posts.json"
    if not posts_path.exists():
        raise SystemExit("synthetic bundle missing - run `make data` first")
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    _load_generated_pool()
    mode = "text-only (quick)" if args.quick else f"with {args.max_media} media posts"
    print(f"[seed] synthetic bundle: {len(posts)} posts, {mode}")
    seed_posts(posts, do_media=not args.quick, max_media=args.max_media, max_ocr_passes=args.ocr_passes,
               run_ocr=args.ocr, llm_mode=args.llm, llm_limit=args.llm_limit)

    # Mark seeded channels as part of the monitoring watchlist (demo status).
    with session_scope() as db:
        for ch in db.execute(select(Channel)).scalars().all():
            ch.is_monitored = True
            if ch.join_status in ("unlisted", "pending", None, ""):
                ch.join_status = "demo"
            if ch.platform == "telegram" and ch.subscriber_count is None:
                ch.subscriber_count = 1200 + (hash(ch.username) % 90000)
    if args.phase == "import":
        print(f"[seed] imported in {time.time()-start:.1f}s")
        return
    derive(rng, args.campaign_sample)

    with session_scope() as db:
        camps = refresh_campaigns(db, force=True)
        stats = {
            "posts": db.execute(select(func.count(Post.id))).scalar_one(),
            "alerts": db.execute(select(func.count(Alert.id))).scalar_one(),
            "identifiers": db.execute(select(func.count(Identifier.id))).scalar_one(),
            "channels": db.execute(select(func.count(Channel.id))).scalar_one(),
            "campaigns": len(camps),
            "engine": get_engine().model.version if get_engine().model else "lexicon-fallback",
        }
    print(f"[seed] done in {time.time()-start:.1f}s -> {stats}")
    cross = [c for c in camps if len(c["channels"]) >= 3]
    if cross:
        print(f"[seed] {len(cross)} campaigns span 3+ channels; top: {cross[0]['title']}")


if __name__ == "__main__":
    main()
