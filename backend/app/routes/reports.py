"""Misc endpoints: PDF reports, Supabase Realtime notification feed, health."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Alert, Post
from app.db.session import get_db
from app.reports import build_alert_report, build_campaign_report, report_id_for
from app.routes.alerts import get_alert
from app.services import refresh_campaigns

router = APIRouter(tags=["reports"])
# `/alerts/recent` must be registered before `/alerts/{alert_id}`; main.py
# includes this router first so the static path wins.
notify_router = APIRouter(tags=["alerts"])


@router.get("/report/{obj_id}.pdf")
def report(obj_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """PDF evidence report for an alert id, or a campaign id (auto-detected)."""
    alert = db.get(Alert, obj_id)
    if alert is not None:
        detail = get_alert(obj_id, db)
        h = hashlib.sha1(json.dumps(detail.get("evidence"), sort_keys=True, default=str).encode()).hexdigest()
        name = report_id_for("alert", obj_id, h[:10])
        out = settings.REPORT_DIR / name
        if not out.exists():
            build_alert_report(detail, out)
        return FileResponse(out, media_type="application/pdf", filename=f"spectra_{name}")
    camps = refresh_campaigns(db)
    for c in camps:
        if c["id"] == obj_id:
            # enrich members with media paths
            for m in c["members"]:
                post = db.get(Post, m["post_id"])
                m["media_path"] = post.media_path if post else None
            h = hashlib.sha1(json.dumps(c, sort_keys=True, default=str).encode()).hexdigest()
            name = report_id_for("campaign", obj_id, h[:10])
            out = settings.REPORT_DIR / name
            if not out.exists():
                build_campaign_report(c, out)
            return FileResponse(out, media_type="application/pdf", filename=f"spectra_{name}")
    raise HTTPException(404, "no alert or campaign with that id")


@router.get("/realtime/config")
def realtime_config(db: Session = Depends(get_db)) -> dict:
    """Supabase Realtime config for the UI (anon key only) + fallback poll token."""
    latest = db.execute(select(Alert).order_by(Alert.created_at.desc()).limit(1)).scalar_one_or_none()
    return {
        "enabled": bool(settings.SUPABASE_URL and settings.SUPABASE_KEY),
        "url": settings.SUPABASE_URL,
        "anon_key": settings.SUPABASE_KEY,
        "table": "alerts",
        "channel": "spectra-alerts",
        "poll_cursor": latest.created_at.isoformat() if latest else None,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@notify_router.get("/alerts/recent")
def recent_alerts(db: Session = Depends(get_db), after: str | None = None) -> dict:
    """Lightweight poll for the notification bell when Realtime is unavailable."""
    q = select(Alert, Post).join(Post, Post.id == Alert.post_id).order_by(Alert.created_at.desc()).limit(15)
    rows = db.execute(q).all()
    items = [
        {"id": a.id, "category": a.category, "risk": a.risk, "channel": p.channel,
         "text": (p.text or "")[:140], "created_at": a.created_at.isoformat(), "status": a.status}
        for a, p in rows
    ]
    if after:
        items = [i for i in items if i["created_at"] > after]
    return {"items": items, "count": len(items)}
