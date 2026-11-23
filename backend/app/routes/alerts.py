"""Alert, evidence, campaign and identifier endpoints."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, AuditLog, Campaign, Evidence, Identifier, Post, utcnow
from app.db.session import get_db
from app.pipeline import evidence as evidence_mod
from app.schemas import StatusUpdate
from app.services import identifier_registry, refresh_campaigns

router = APIRouter(tags=["alerts"])

VALID_TRANSITIONS = {
    "new": {"under_review", "confirmed", "dismissed"},
    "under_review": {"confirmed", "dismissed", "new"},
    "confirmed": {"dismissed", "under_review"},
    "dismissed": {"new", "under_review"},
}


def _alert_row(a: Alert, p: Post, idents: list[Identifier]) -> dict:
    return {
        "id": a.id, "post_id": a.post_id, "category": a.category, "risk": a.risk,
        "confidence": round(a.confidence, 4), "status": a.status, "channel": p.channel,
        "timestamp": p.timestamp, "text": (p.text or "")[:300],
        "source_types": sorted({i.source for i in idents}),
        "identifier_types": sorted({i.type for i in idents}),
        "cluster_id": a.cluster_id, "media_type": p.media_type, "has_evidence": True,
    }


@router.get("/alerts")
def list_alerts(
    db: Session = Depends(get_db),
    category: str | None = None,
    risk: str | None = None,
    status: str | None = None,
    channel: str | None = None,
    identifier_type: str | None = None,
    source: str | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    page: int = 1,
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    """Filterable, sortable alert table."""
    stmt = select(Alert, Post).join(Post, Post.id == Alert.post_id)
    if category:
        stmt = stmt.where(Alert.category == category)
    if risk:
        stmt = stmt.where(Alert.risk == risk.upper())
    if status:
        stmt = stmt.where(Alert.status == status)
    if channel:
        stmt = stmt.where(Post.channel == channel.lstrip("@"))
    if q:
        stmt = stmt.where(Post.text.ilike(f"%{q}%"))
    if date_from:
        stmt = stmt.where(Post.timestamp >= datetime.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Post.timestamp <= datetime.fromisoformat(date_to))
    rows = db.execute(stmt).all()
    idents_by_post: dict[str, list[Identifier]] = {}
    for i in db.execute(select(Identifier)).scalars():
        idents_by_post.setdefault(i.post_id, []).append(i)
    if identifier_type:
        rows = [(a, p) for a, p in rows if any(i.type == identifier_type for i in idents_by_post.get(p.id, []))]
    if source:
        rows = [(a, p) for a, p in rows if any(i.source == source for i in idents_by_post.get(p.id, []))]
    keyfuncs = {
        "timestamp": lambda r: r[1].timestamp,
        "risk": lambda r: {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(r[0].risk, 0),
        "confidence": lambda r: r[0].confidence,
        "category": lambda r: r[0].category,
        "status": lambda r: r[0].status,
        "channel": lambda r: r[1].channel,
    }
    kf = keyfuncs.get(sort, keyfuncs["timestamp"])
    rows.sort(key=lambda r: (kf(r) is None, kf(r)), reverse=(order == "desc"))
    total = len(rows)
    start = (page - 1) * page_size
    items = [_alert_row(a, p, idents_by_post.get(p.id, [])) for a, p in rows[start : start + page_size]]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, db: Session = Depends(get_db)) -> dict:
    """Full alert detail: post, explanation, evidence, identifiers, audit, related."""
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    p = db.get(Post, a.post_id)
    ev = db.execute(select(Evidence).where(Evidence.alert_id == a.id)).scalar_one_or_none()
    ident_rows = db.execute(select(Identifier).where(Identifier.post_id == p.id)).scalars().all()
    audit = db.execute(select(AuditLog).where(AuditLog.alert_id == a.id).order_by(AuditLog.at.asc())).scalars().all()
    # cross-channel context for the post's identifiers
    related_campaign = None
    if a.cluster_id:
        camp = db.get(Campaign, a.cluster_id)
        if camp:
            related_campaign = {"id": camp.id, "title": camp.title, "channels": camp.channels,
                                "post_count": len(camp.post_ids)}
    return {
        "id": a.id,
        "post": {
            "id": p.id, "channel": p.channel, "timestamp": p.timestamp, "text": p.text,
            "permalink": p.permalink, "media_path": p.media_path, "media_type": p.media_type,
            "media_sha256": p.media_sha256, "image_hash": p.image_hash, "audio_hash": p.audio_hash,
            "language": p.language, "views": p.views, "ocr": p.ocr, "qr": p.qr, "transcript": p.transcript,
            "external_id": p.external_id, "platform": p.platform,
        },
        "category": a.category,
        "risk": a.risk,
        "confidence": a.confidence,
        "status": a.status,
        "explanation": a.explanation,
        "subcategories": a.subcategories,
        "identifiers": [i.as_dict() if hasattr(i, "as_dict") else {
            "type": i.type, "value": i.value, "raw": i.raw, "source": i.source,
            "confidence": i.confidence, "extra": i.extra,
        } for i in ident_rows],
        "evidence": {
            "id": ev.id if ev else None,
            "payload": ev.payload if ev else None,
            "sha256": ev.sha256 if ev else None,
            "captured_at": ev.captured_at if ev else None,
        },
        "audit": [
            {"id": x.id, "actor": x.actor, "action": x.action, "from_status": x.from_status,
             "to_status": x.to_status, "notes": x.notes, "at": x.at}
            for x in audit
        ],
        "cluster_id": a.cluster_id,
        "analyst_notes": a.analyst_notes or [],
        "related": {"campaign": related_campaign},
    }


@router.patch("/alerts/{alert_id}/status")
def update_status(alert_id: str, body: StatusUpdate, db: Session = Depends(get_db)) -> dict:
    """New -> Under review -> Confirmed -> Dismissed with notes + audit log."""
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    if body.status == a.status:
        return {"ok": True, "status": a.status}
    if body.status not in VALID_TRANSITIONS.get(a.status, set()):
        raise HTTPException(409, f"illegal transition {a.status} -> {body.status}")
    old = a.status
    a.status = body.status
    a.updated_at = utcnow()
    if body.notes:
        a.analyst_notes = (a.analyst_notes or []) + [
            {"actor": body.actor, "notes": body.notes, "at": utcnow().isoformat(), "status": body.status}
        ]
    db.add(AuditLog(alert_id=a.id, actor=body.actor, action="status_change", from_status=old,
                    to_status=body.status, notes=body.notes))
    db.commit()
    return {"ok": True, "status": a.status, "audit_logged": True}


@router.get("/alerts/{alert_id}/verify")
def verify_alert(alert_id: str, db: Session = Depends(get_db)) -> dict:
    """Recompute the SHA-256 of the evidence snapshot and compare to the stored hash."""
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    ev = db.execute(select(Evidence).where(Evidence.alert_id == a.id)).scalar_one_or_none()
    if ev is None:
        raise HTTPException(404, "no evidence snapshot")
    result = evidence_mod.verify(ev.payload, ev.sha256)
    db.add(AuditLog(alert_id=a.id, actor="system", action="evidence_verify",
                    notes=f"integrity={'OK' if result['ok'] else 'FAILED'}"))
    if not result["ok"]:
        a.explanation = {**(a.explanation or {}), "integrity": {
            "ok": False, "checked_at": result["verified_at"], "recomputed": result["recomputed_sha256"]}}
    db.commit()
    return result


# --------------------------------------------------------------------------- campaigns
@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), refresh: bool = False) -> dict:
    camps = refresh_campaigns(db, force=refresh)
    return {"total": len(camps), "items": camps}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    camps = refresh_campaigns(db)
    for c in camps:
        if c["id"] == campaign_id:
            return c
    raise HTTPException(404, "campaign not found")


# --------------------------------------------------------------------------- identifiers
@router.get("/identifiers")
def list_identifiers(
    db: Session = Depends(get_db),
    q: str | None = None,
    type: str | None = None,  # noqa: A002 - public API param name
    channel: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    rows = identifier_registry(db, q=q, type_filter=type, channel=channel, limit=limit)
    return {"total": len(rows), "items": rows}


@router.get("/identifiers/export.csv")
def export_identifiers(db: Session = Depends(get_db)) -> StreamingResponse:
    rows = identifier_registry(db, limit=10000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["type", "value", "post_count", "channel_count", "channels", "categories",
                "first_seen", "last_seen", "risk_score", "alert_count", "campaign_count"])
    for r in rows:
        w.writerow([r["type"], r["value"], r["post_count"], r["channel_count"], "|".join(r["channels"]),
                    "|".join(r["categories"]), r["first_seen"], r["last_seen"], r["risk_score"],
                    r["alert_count"], r["campaign_count"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=spectra_identifiers.csv"},
    )
