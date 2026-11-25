"""Derived analytics services: campaigns, entity registry, search, stats.

Everything here is computed from persisted posts/identifiers and the cached
campaign list, so the API stays thin and the logic is testable.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import numpy as np
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Alert, Campaign, Channel, Identifier, Post, PostStatus
from app.ml import extractors
from app.ml.normalize import normalize
from app.pipeline import graph as graph_mod

_cache_lock = threading.Lock()
_campaign_cache: dict[str, Any] = {"key": None, "campaigns": []}


def _post_dict(p: Post, alert: Alert | None = None, identifiers: list[dict] | None = None) -> dict:
    return {
        "id": p.id,
        "channel": p.channel,
        "timestamp": p.timestamp.isoformat() if p.timestamp else "",
        "text": p.text,
        "category": (alert.category if alert else None) or ("benign" if p.status == PostStatus.ok.value else None),
        "alert_id": alert.id if alert else None,
        "identifiers": identifiers or [],
        "image_hash": p.image_hash,
        "audio_hash": p.audio_hash,
        "media_type": p.media_type,
    }


def load_posts_with_identifiers(db: Session, limit: int = 6000) -> list[dict]:
    """Load posts + alert categories + identifiers for graph/campaign work."""
    rows = db.execute(
        select(Post, Alert)
        .outerjoin(Alert, Alert.post_id == Post.id)
        .order_by(Post.timestamp.desc())
        .limit(limit)
    ).all()
    ids = db.execute(select(Identifier)).scalars().all()
    by_post: dict[str, list[dict]] = defaultdict(list)
    for i in ids:
        by_post[i.post_id].append({"type": i.type, "value": i.value, "source": i.source})
    out = []
    for post, alert in rows:
        out.append(_post_dict(post, alert, by_post.get(post.id, [])))
    return out


# --------------------------------------------------------------------------- campaigns
def refresh_campaigns(db: Session, *, min_size: int = 3, force: bool = False,
                      posts: list[dict] | None = None) -> list[dict]:
    """Recompute coordination clusters and persist them.

    ``posts`` lets the seed script cluster a bounded, channel-balanced sample
    (CPU friendly). Seeding caches the result so page loads stay fast; the
    ingest pipeline calls this with ``force=True`` after new data arrives.
    """
    key = (
        db.execute(select(func.count(Post.id))).scalar_one(),
        db.execute(select(func.max(Post.created_at))).scalar_one(),
    )
    with _cache_lock:
        if not force and _campaign_cache["key"] == key:
            return _campaign_cache["campaigns"]
    posts = posts if posts is not None else load_posts_with_identifiers(db)
    camps = graph_mod.detect_campaigns(posts, min_size=min_size)
    by_id = {p["id"]: p for p in posts}

    existing = {c.signature: c for c in db.execute(select(Campaign)).scalars().all()}
    out: list[dict] = []
    for c in camps:
        members = [by_id[pid] for pid in c.post_ids if pid in by_id]
        alerted = [m for m in members if m.get("alert_id")]
        if not alerted:
            continue  # benign reposts (news, price lists) are not campaigns of interest
        label = {
            "drugs": "Narcotics supply ring", "arms": "Arms deal cluster", "fake_docs": "Fake-document racket",
            "mule_fin": "Mule-account network", "benign": "Uncategorised cluster",
            "atm_card": "ATM-card ring", "sim_card": "SIM-card ring", "net_banking_kit": "Net-banking kit ring",
            "mule_account": "Mule-account network",
        }.get(c.category, "Mixed-signal campaign")
        snippet = re.sub(r"\s+", " ", (alerted[0]["text"] or "")).strip()[:60]
        title = f"{label} · {len(c.channels)} channels · {len(c.post_ids)} ads"
        row = existing.get(c.signature)
        if row is None:
            row = Campaign(title=title, category=c.category, signature=c.signature)
            db.add(row)
        row.title = title
        row.category = c.category
        row.post_ids = c.post_ids
        row.channels = c.channels
        row.shared_identifiers = c.shared_identifiers
        row.time_span = {"start": c.start, "end": c.end}
        row.posting_frequency = c.posting_frequency
        row.similarity = c.similarity
        db.flush()
        for p in members:
            alert = db.execute(select(Alert).where(Alert.post_id == p["id"])).scalar_one_or_none()
            if alert is not None:
                alert.cluster_id = row.id
        out.append(
            {
                "id": row.id,
                "category": c.category,
                "post_count": len(c.post_ids),
                "channels": c.channels,
                "shared_identifiers": c.shared_identifiers,
                "time_span": {"start": c.start, "end": c.end},
                "posting_frequency": round(c.posting_frequency, 3),
                "similarity": round(c.similarity, 3),
                "category_mix": c.category_mix,
                "title": title,
                "snippet": snippet,
                "alert_count": len(alerted),
                "members": [
                    {
                        "post_id": m["id"], "channel": m["channel"], "timestamp": m["timestamp"],
                        "text": m["text"][:400], "category": m["category"], "media_type": m["media_type"],
                        "alert_id": m["alert_id"],
                    }
                    for m in sorted(members, key=lambda x: x["timestamp"])
                ],
            }
        )
    db.commit()
    with _cache_lock:
        _campaign_cache["key"] = key
        _campaign_cache["campaigns"] = out
    return out


def campaigns_for_post_alerts(db: Session) -> dict[str, str]:
    rows = db.execute(select(Alert.id, Alert.cluster_id).where(Alert.cluster_id.is_not(None))).all()
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- identifiers
def identifier_registry(db: Session, *, q: str | None = None, type_filter: str | None = None,
                        channel: str | None = None, limit: int = 2000) -> list[dict]:
    stmt = select(Identifier)
    if type_filter:
        stmt = stmt.where(Identifier.type == type_filter)
    if channel:
        stmt = stmt.where(Identifier.channel == channel.lstrip("@"))
    rows = db.execute(stmt).scalars().all()
    if q:
        qn = q.lower().strip()
        rows = [r for r in rows if qn in r.value.lower() or fuzz.partial_ratio(qn, r.value.lower()) >= 85]
    alerts = {a.post_id: a for a in db.execute(select(Alert)).scalars().all()}
    clusters = {a.post_id: a.cluster_id for a in alerts.values()}
    grouped: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.type, r.value)
        g = grouped.get(key)
        if g is None:
            g = grouped[key] = {
                "type": r.type, "value": r.value, "post_ids": set(), "channels": set(),
                "categories": set(), "first_seen": r.timestamp, "last_seen": r.timestamp,
                "alert_post_ids": set(), "clusters": set(), "sources": set(),
            }
        g["post_ids"].add(r.post_id)
        g["channels"].add(r.channel)
        g["sources"].add(r.source)
        if r.timestamp is not None:
            if g["first_seen"] is None or r.timestamp < g["first_seen"]:
                g["first_seen"] = r.timestamp
            if g["last_seen"] is None or r.timestamp > g["last_seen"]:
                g["last_seen"] = r.timestamp
        a = alerts.get(r.post_id)
        if a is not None:
            g["categories"].add(a.category)
            g["alert_post_ids"].add(a.id)
            if clusters.get(r.post_id):
                g["clusters"].add(clusters[r.post_id])
    out = []
    for g in grouped.values():
        posts_for_risk = [
            {"category": alerts[pid].category} for pid in g["post_ids"] if pid in alerts
        ]
        out.append(
            {
                "type": g["type"], "value": g["value"],
                "post_count": len(g["post_ids"]),
                "channel_count": len(g["channels"]),
                "channels": sorted(c for c in g["channels"] if c),
                "categories": sorted(g["categories"]),
                "first_seen": g["first_seen"].isoformat() if g["first_seen"] else None,
                "last_seen": g["last_seen"].isoformat() if g["last_seen"] else None,
                "risk_score": graph_mod.identifier_risk(posts_for_risk, g["channels"]),
                "alert_count": len(g["alert_post_ids"]),
                "campaign_count": len(g["clusters"]),
                "sources": sorted(g["sources"]),
            }
        )
    out.sort(key=lambda r: (-r["risk_score"], -r["channel_count"], -r["post_count"]))
    return out[:limit]


# --------------------------------------------------------------------------- search
_IDENT_QUERY = re.compile(r"^(\+?\d[\d\s\-]{6,}|\S+@\S+|@\w+|t\.me/\S+)$", re.I)


def search_posts(
    db: Session,
    q: str,
    *,
    channel: str | None = None,
    category: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str = "auto",
    limit: int = 100,
) -> dict:
    """Fuzzy + semantic + identifier search across collected posts."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "mode": "empty", "total": 0, "hits": []}
    detected_mode = mode
    if mode == "auto":
        detected_mode = "identifier" if _IDENT_QUERY.match(q) else "hybrid"

    # --- identifier-first path
    hits: dict[str, dict] = {}
    if detected_mode in ("identifier", "hybrid"):
        ids = extractors.extract_all(q)
        values = {(i.type, i.value) for i in ids} | {(None, q.lower())}
        if q.lower().startswith("+91") or re.fullmatch(r"\d{10}", q):
            phone = extractors.normalize_phone(q)
            if phone:
                values.add(("phone", phone))
        idrows = db.execute(select(Identifier)).scalars().all()
        matched_ids = [
            r for r in idrows
            if (r.type, r.value) in values or (r.value.lower() == q.lower().lstrip("@")) or q.lower() in r.value.lower()
        ]
        post_ids = {r.post_id: r.type for r in matched_ids}
        if post_ids:
            for p in db.execute(select(Post).where(Post.id.in_(post_ids))).scalars().all():
                hits[p.id] = {"post": p, "score": 1.0, "matched": [post_ids[p.id]]}

    # --- text path: lexical (rapidfuzz) + optional semantic
    if len(hits) < limit and detected_mode in ("hybrid", "semantic", "text"):
        qn = normalize(q)
        stmt = select(Post, Alert).outerjoin(Alert, Alert.post_id == Post.id)
        if channel:
            stmt = stmt.where(Post.channel == channel.lstrip("@"))
        if date_from:
            stmt = stmt.where(Post.timestamp >= _dt(date_from))
        if date_to:
            stmt = stmt.where(Post.timestamp <= _dt(date_to))
        rows = db.execute(stmt.order_by(Post.timestamp.desc()).limit(4000)).all()
        scored: list[tuple[float, Post, Alert, list[str]]] = []
        for post, alert in rows:
            if category and (alert.category if alert else "benign") != category:
                continue
            text = post.text or ""
            if qn in normalize(text):
                scored.append((1.0, post, alert, [q]))
                continue
            score = fuzz.partial_ratio(qn, normalize(text)) / 100.0
            if score >= 0.82:
                scored.append((score, post, alert, []))
        if detected_mode == "semantic" or not scored:
            scored.extend(_semantic_search(db, q, rows, category))
        for score, post, alert, matched in sorted(scored, key=lambda x: -x[0])[: limit * 2]:
            if post.id in hits and hits[post.id]["score"] >= score:
                continue
            hits[post.id] = {"post": post, "score": round(float(score), 3), "matched": matched, "alert": alert}

    out = []
    for h in sorted(hits.values(), key=lambda x: -x["score"])[:limit]:
        p: Post = h["post"]
        alert = h.get("alert") or db.execute(select(Alert).where(Alert.post_id == p.id)).scalar_one_or_none()
        out.append(
            {
                "post_id": p.id, "alert_id": alert.id if alert else None, "channel": p.channel,
                "timestamp": p.timestamp, "text": p.text[:600],
                "category": alert.category if alert else ("benign" if p.status == PostStatus.ok.value else None),
                "label": p.label, "score": h["score"], "matched": h["matched"],
            }
        )
    if source:
        keep = []
        for o in out:
            srcs = {i.source for i in db.execute(select(Identifier).where(Identifier.post_id == o["post_id"])).scalars()}
            if source in srcs or (source == "text" and o["post_id"] in hits):
                keep.append(o)
        out = keep
    return {"query": q, "mode": detected_mode, "total": len(out), "hits": out}


def _semantic_search(db: Session, q: str, rows: Iterable[tuple[Post, Alert]], category: str | None):
    """Cosine similarity in MiniLM space, with a process cache for post vectors."""
    try:
        from app.ml import embed_cache
        from app.ml.train import embed

        posts = [(p, a) for p, a in rows if (p.text or "").strip()]
        if not posts:
            return []
        embed_cache.ensure([(str(p.id), normalize(p.text)) for p, _ in posts], embed)
        mat = embed_cache.matrix([(str(p.id), normalize(p.text)) for p, _ in posts])
        if mat is None:
            return []
        qv = embed([normalize(q)])[0]
        qv = qv / max(float(np.linalg.norm(qv)), 1e-8)
        sims = mat @ qv.astype(np.float32)
        out = []
        for (p, a), s in zip(posts, sims):
            if category and (a.category if a else "benign") != category:
                continue
            if s >= 0.42:
                out.append((float(s), p, a, ["semantic"]))
        return out
    except Exception:
        return []


def _dt(value: str) -> datetime:
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(days=3650)


# --------------------------------------------------------------------------- stats
def overview_stats(db: Session) -> dict:
    posts_scanned = db.execute(select(func.count(Post.id))).scalar_one()
    alerts = db.execute(select(Alert)).scalars().all()
    high = sum(1 for a in alerts if a.risk == "HIGH")
    uniq = db.execute(select(func.count(func.distinct(Identifier.value)))).scalar_one()
    camps = db.execute(select(func.count(Campaign.id))).scalar_one()
    by_cat: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    for a in alerts:
        by_cat[a.category] += 1
        by_status[a.status] += 1
    # alerts by category over time (use the message date so seeded history plots)
    post_dates = {pid: ts for pid, ts in db.execute(select(Post.id, Post.timestamp)).all()}
    series: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in alerts:
        ts = post_dates.get(a.post_id) or a.created_at
        day = ts.date().isoformat() if ts else "unknown"
        series[day][a.category] += 1
    top_channels = db.execute(
        select(Post.channel, func.count(Post.id).label("n"), func.sum(Post.views).label("v"))
        .group_by(Post.channel).order_by(func.count(Post.id).desc()).limit(10)
    ).all()
    channel_alerts: dict[str, int] = defaultdict(int)
    for a in alerts:
        post = db.get(Post, a.post_id)
        if post:
            channel_alerts[post.channel] += 1
    src_rows = db.execute(select(Identifier.source, func.count(Identifier.id)).group_by(Identifier.source)).all()
    sources = {s: n for s, n in src_rows}
    media_rows = db.execute(select(Post.media_type, func.count(Post.id)).group_by(Post.media_type)).all()
    media = {str(s or "text"): n for s, n in media_rows}
    total_media = sum(v for k, v in media.items() if k != "None")
    flag_sources: dict[str, int] = defaultdict(int)
    for a in alerts:
        flag_sources[a.flag_source or "engine"] += 1
    llm_scanned = sum(
        1 for v in db.execute(select(Post.llm)).scalars()
        if isinstance(v, dict) and v.get("available")
    )
    llm_flagged = sum(1 for a in alerts if (a.llm or {}).get("flag"))
    llm_scanned = max(llm_scanned, llm_flagged)
    llm_flagged = sum(1 for a in alerts if (a.llm or {}).get("flag"))
    emoji_counts: dict[str, int] = defaultdict(int)
    for a in alerts:
        for item in (a.emoji or []):
            emoji_counts[str(item.get("emoji") or "")] += 1
    emoji_top = [
        {"emoji": emo, "count": n}
        for emo, n in sorted(emoji_counts.items(), key=lambda kv: -kv[1])[:8]
        if emo
    ]
    monitored = db.execute(
        select(func.count(Channel.id)).where(Channel.is_monitored.is_(True))
    ).scalar_one()
    return {
        "posts_scanned": posts_scanned,
        "alerts": len(alerts),
        "high_risk": high,
        "unique_identifiers": uniq,
        "active_campaigns": camps,
        "channels_monitored": db.execute(select(func.count(Channel.id))).scalar_one(),
        "channels_joined": monitored,
        "alerts_by_category": dict(by_cat),
        "alerts_by_status": dict(by_status),
        "alerts_over_time": [{"date": d, **v} for d, v in sorted(series.items())],
        "top_channels": [
            {"channel": ch, "posts": int(n), "alerts": channel_alerts.get(ch, 0), "views": int(v or 0)}
            for ch, n, v in top_channels
        ],
        "source_breakdown": {
            "text": sources.get("text", 0),
            "image_ocr": sources.get("image_ocr", 0),
            "qr": sources.get("qr", 0),
            "voice": sources.get("voice", 0),
        },
        "media_breakdown": {"images": int(media.get("image", 0)), "audio": int(media.get("audio", 0)),
                            "text_only": posts_scanned - total_media},
        "risk_breakdown": {
            "HIGH": high,
            "MEDIUM": sum(1 for a in alerts if a.risk == "MEDIUM"),
            "LOW": sum(1 for a in alerts if a.risk == "LOW"),
        },
        "flag_source_breakdown": dict(flag_sources),
        "llm": {
            "configured": settings.llm_ready,
            "scanned": int(llm_scanned),
            "flagged": llm_flagged,
            "vetoed": flag_sources.get("llm-veto", 0),
        },
        "emoji_top": emoji_top,
    }


def map_points(db: Session, limit: int = 800) -> list[dict]:
    """Extracted locations with coordinates for the Leaflet map."""
    rows = db.execute(select(Identifier).where(Identifier.type == "location")).scalars().all()
    alerts = {a.post_id: a for a in db.execute(select(Alert)).scalars().all()}
    agg: dict[str, dict] = {}
    for r in rows:
        lat = (r.extra or {}).get("lat")
        lng = (r.extra or {}).get("lng")
        if lat is None or lng is None:
            continue
        key = r.value
        g = agg.setdefault(key, {"name": key, "lat": lat, "lng": lng, "posts": set(), "categories": set()})
        g["posts"].add(r.post_id)
        a = alerts.get(r.post_id)
        if a:
            g["categories"].add(a.category)
    out = [
        {"name": g["name"], "lat": g["lat"], "lng": g["lng"], "count": len(g["posts"]),
         "categories": sorted(g["categories"])}
        for g in agg.values()
    ]
    out.sort(key=lambda x: -x["count"])
    return out[:limit]
