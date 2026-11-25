"""Identity resolution: everything known about one phone / UPI / wallet / handle.

This is deliberately evidence-bound: the profile is assembled from identifiers
that actually appeared in collected posts, plus the channels and alerts they
touch. It does not query banks or external people-search services.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Alert, Identifier, Post
from app.db.session import get_db
from app.services import identifier_registry

router = APIRouter(tags=["identity"])

_TYPE_ORDER = ["upi", "phone", "wallet", "username", "handle", "bank", "ifsc", "email", "link", "location"]


@router.get("/identity")
def search_identities(
    q: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
    limit: int = 25,
) -> dict:
    """Typeahead over known identifier values."""
    rows = identifier_registry(db, q=q, limit=limit)
    return {"query": q, "items": rows}


@router.get("/identity/{value:path}")
def identity_profile(value: str, db: Session = Depends(get_db)) -> dict:
    """Full cross-channel profile for one identifier value."""
    needle = (value or "").strip()
    if not needle:
        raise HTTPException(400, "empty identifier")
    matches = db.execute(
        select(Identifier).where(func.lower(Identifier.value) == needle.lower())
    ).scalars().all()
    if not matches:
        # tolerate a phone typed without +91 / spaces
        digits = "".join(ch for ch in needle if ch.isdigit())
        if len(digits) >= 8:
            matches = [
                i for i in db.execute(select(Identifier).where(Identifier.type == "phone")).scalars()
                if digits[-10:] in "".join(ch for ch in i.value if ch.isdigit())
            ]
    if not matches:
        raise HTTPException(404, "identifier not found")

    post_ids = sorted({m.post_id for m in matches})
    posts = db.execute(
        select(Post).where(Post.id.in_(post_ids)).order_by(Post.timestamp.desc()).limit(100)
    ).scalars().all()
    alerts = db.execute(
        select(Alert).where(Alert.post_id.in_(post_ids)).order_by(Alert.created_at.desc())
    ).scalars().all()
    linked = db.execute(
        select(Identifier).where(Identifier.post_id.in_(post_ids))
    ).scalars().all()

    def _bucket(kind: str) -> list[str]:
        return sorted({i.value for i in linked if i.type == kind})

    channels = sorted({p.channel for p in posts})
    categories = sorted({a.category for a in alerts})
    payees = sorted(
        {
            str((i.extra or {}).get("payee"))
            for i in linked
            if (i.extra or {}).get("payee")
        }
    )
    first = min((p.timestamp for p in posts), default=None)
    last = max((p.timestamp for p in posts), default=None)
    high = sum(1 for a in alerts if a.risk == "HIGH")
    risk = min(100.0, len(alerts) * 8 + len(channels) * 6 + high * 10)

    post_cat = {a.post_id: a for a in alerts}
    return {
        "value": matches[0].value,
        "type": next((t for t in _TYPE_ORDER if t in {m.type for m in matches}), matches[0].type),
        "aliases": sorted({m.raw or m.value for m in matches if (m.raw or m.value) != matches[0].value})[:20],
        "channels": channels,
        "upis": _bucket("upi"),
        "phones": _bucket("phone"),
        "wallets": _bucket("wallet"),
        "handles": sorted(set(_bucket("username")) | set(_bucket("handle"))),
        "payee_names": payees[:20],
        "first_seen": first,
        "last_seen": last,
        "risk_score": round(risk, 1),
        "categories": categories,
        "posts": [
            {
                "id": p.id,
                "channel": p.channel,
                "timestamp": p.timestamp,
                "text": (p.text or "")[:400],
                "permalink": p.permalink,
                "media_type": p.media_type,
                "category": (post_cat.get(p.id).category if post_cat.get(p.id) else None),
                "risk": (post_cat.get(p.id).risk if post_cat.get(p.id) else None),
                "alert_id": (post_cat.get(p.id).id if post_cat.get(p.id) else None),
            }
            for p in posts
        ],
        "alerts": [
            {
                "id": a.id,
                "category": a.category,
                "risk": a.risk,
                "confidence": a.confidence,
                "status": a.status,
                "created_at": a.created_at,
            }
            for a in alerts
        ],
    }
