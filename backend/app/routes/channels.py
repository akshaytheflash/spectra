"""Channel operations: discovery, joining, monitoring and on-demand scans."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.scope import check_public_target
from app.db.models import Alert, Channel, IngestJob, Post
from app.db.session import get_db, session_scope
from app.jobs import JobContext, create_job, job_dict, run_in_background
from app.pipeline import autopilot, ingest
from app.pipeline import telegram as tg
from app.pipeline import telegram_admin as admin
from app.pipeline.llm_batch import scan_texts
from app.schemas import ChannelDiscoverIn, ChannelJoinIn, ChannelMonitorIn, JobOut

router = APIRouter(tags=["channels"])


def _serialize(db: Session, ch: Channel) -> dict:
    posts = db.execute(select(func.count(Post.id)).where(Post.channel == ch.username)).scalar_one()
    alerts = db.execute(
        select(func.count(Alert.id)).join(Post, Alert.post_id == Post.id).where(Post.channel == ch.username)
    ).scalar_one()
    return {
        "id": ch.id,
        "username": ch.username,
        "title": ch.title,
        "description": ch.description,
        "is_public": ch.is_public,
        "is_monitored": ch.is_monitored,
        "join_status": ch.join_status,
        "subscriber_count": ch.subscriber_count,
        "last_scraped": ch.last_scraped,
        "posts": posts,
        "alerts": alerts,
        "added_by": ch.added_by,
    }


def _upsert(
    db: Session,
    username: str,
    *,
    title: str | None = None,
    description: str | None = None,
    subscribers: int | None = None,
    monitored: bool = True,
    join_status: str = "joined",
    added_by: str = "analyst",
) -> Channel:
    username = username.lstrip("@").strip()
    ch = db.execute(select(Channel).where(Channel.username == username)).scalar_one_or_none()
    if ch is None:
        ch = Channel(username=username, title=title or username, added_by=added_by)
        db.add(ch)
    if title:
        ch.title = title
    if description:
        ch.description = description
    if subscribers is not None:
        ch.subscriber_count = subscribers
    if monitored:
        ch.is_monitored = True
    ch.join_status = join_status
    if join_status == "joined":
        ch.joined_at = ch.joined_at or datetime.now(timezone.utc)
    ch.last_seen = datetime.now(timezone.utc)
    db.flush()
    return ch


@router.get("/channels")
def list_channels(
    db: Session = Depends(get_db),
    monitored: bool | None = None,
    q: str | None = None,
    limit: int = 200,
) -> dict:
    query = select(Channel).order_by(Channel.is_monitored.desc(), Channel.last_seen.desc()).limit(min(limit, 500))
    if monitored is not None:
        query = query.where(Channel.is_monitored.is_(monitored))
    if q:
        like = f"%{q.strip()}%"
        query = query.where((Channel.username.ilike(like)) | (Channel.title.ilike(like)))
    rows = db.execute(query).scalars().all()
    return {"items": [_serialize(db, c) for c in rows]}


@router.get("/channels/status")
def channel_status(db: Session = Depends(get_db)) -> dict:
    total = db.execute(select(func.count(Channel.id))).scalar_one()
    monitored = db.execute(
        select(func.count(Channel.id)).where(Channel.is_monitored.is_(True))
    ).scalar_one()
    from app.bot.service import bot_status

    return {
        "telegram_session": tg.live_available(),
        "telegram_bot": bot_status(),
        "autopilot": autopilot.status(),
        "auto_join_enabled": settings.AUTO_JOIN_ENABLED,
        "channels_total": total,
        "channels_monitored": monitored,
        "keywords": settings.auto_join_keywords,
    }


@router.get("/channels/discover")
async def discover_channels(q: str, limit: int = 12, db: Session = Depends(get_db)) -> dict:
    """Search public Telegram channels by keyword (no join)."""
    results = await admin.search_public(q, limit=limit)
    if not results:
        return {
            "ok": tg.live_available(),
            "reason": None if tg.live_available() else "no-telegram-session",
            "items": [],
        }
    known = {c.username.lower() for c in db.execute(select(Channel)).scalars().all()}
    items = []
    for r in results:
        row = r.as_dict()
        row["known"] = r.username.lower() in known
        items.append(row)
    return {"ok": True, "items": items}


@router.post("/channels/discover")
async def discover_and_join(req: ChannelDiscoverIn, db: Session = Depends(get_db)) -> dict:
    keywords = [k for k in (req.keywords or settings.auto_join_keywords) if k.strip()][:8]
    if not keywords:
        raise HTTPException(400, "no keywords")
    if not tg.live_available():
        raise HTTPException(503, "Telegram session not configured. Add TELEGRAM_API_ID/HASH and run the login script.")
    result = await admin.discover(keywords, per_keyword=req.per_keyword, join=req.join)
    for row in result.get("results", []):
        _upsert(
            db,
            row["username"],
            title=row.get("title"),
            description=row.get("about"),
            subscribers=row.get("subscribers"),
            monitored=bool(row.get("joined")),
            join_status="joined" if row.get("joined") else "discovered",
            added_by="discovery",
        )
    db.commit()
    return result


@router.post("/channels/join")
async def join_channel(req: ChannelJoinIn, db: Session = Depends(get_db)) -> dict:
    decision = check_public_target(req.target)
    if not decision.allowed or not decision.channel:
        raise HTTPException(400, decision.reason or "invalid public channel target")
    if not tg.live_available():
        # Queue it anyway so the demo/dashboard flow works without a session.
        ch = _upsert(
            db, decision.channel, monitored=req.monitor, join_status="pending",
            added_by="manual",
        )
        db.commit()
        return {
            "ok": False,
            "queued": True,
            "reason": "Telegram session not configured; channel saved and queued for joining.",
            "channel": _serialize(db, ch),
        }
    info = await admin.join_channel(decision.channel)
    if info.error:
        raise HTTPException(502, f"join failed: {info.error}")
    ch = _upsert(
        db, info.username, title=info.title, description=info.about,
        subscribers=info.subscribers, monitored=req.monitor, join_status="joined",
        added_by="manual",
    )
    db.commit()
    return {"ok": True, "channel": _serialize(db, ch)}


@router.post("/channels/{channel_id}/monitor")
def set_monitor(channel_id: str, req: ChannelMonitorIn, db: Session = Depends(get_db)) -> dict:
    ch = db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    ch.is_monitored = req.enabled
    db.commit()
    return {"ok": True, "channel": _serialize(db, ch)}


@router.delete("/channels/{channel_id}/join")
async def leave_channel(channel_id: str, db: Session = Depends(get_db)) -> dict:
    ch = db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    if tg.live_available() and ch.join_status == "joined":
        result = await admin.leave_channel(ch.username)
        if not result.get("ok"):
            raise HTTPException(502, result.get("error") or "leave failed")
    ch.join_status = "left"
    ch.is_monitored = False
    db.commit()
    return {"ok": True, "channel": _serialize(db, ch)}


def _scan_job(ctx: JobContext, username: str, limit: int) -> dict:
    async def _collect():
        return await tg.collect_channel(
            username, limit=limit, media_dir=settings.UPLOAD_DIR / "telegram" / "manual"
        )

    msgs, reports = asyncio.run(_collect())
    mode = "live"
    msgs = msgs[:limit]
    ctx.set_total(len(msgs))
    from app.ml.engine import engine

    texts = [m.text or "" for m in msgs]
    preds = engine().predict_many(texts) if texts else []
    verdicts = scan_texts(texts, preds, context=username, mode="candidates")
    created = flagged = 0
    errors: list[str] = []
    for m, pred, verdict in zip(msgs, preds, verdicts):
        with session_scope() as db:
            try:
                post = ingest.create_post(
                    db, text=m.text, channel=m.channel, timestamp=m.timestamp,
                    external_id=m.external_id, permalink=m.permalink, views=m.views,
                )
                res = ingest.process_post(
                    db, post, media_path=None, prediction=pred, llm_verdict=verdict,
                    do_llm=False, do_ocr=False,
                )
                created += 1
                if res.alert_id:
                    flagged += 1
                    alert = db.get(Alert, res.alert_id)
                    if alert is not None and alert.risk in ("HIGH", "MEDIUM"):
                        from app.bot.service import notify_alert

                        notify_alert(alert, post)
            except Exception as exc:
                errors.append(f"{m.external_id}: {exc}")
        ctx.step(f"{created}/{len(msgs)} analysed")
    with session_scope() as db:
        ch = db.execute(select(Channel).where(Channel.username == username)).scalar_one_or_none()
        if ch is not None:
            ch.last_scraped = datetime.now(timezone.utc)
            if msgs:
                meta = dict(ch.meta or {})
                ids = [int(m.external_id) for m in msgs if str(m.external_id).isdigit()]
                if ids:
                    meta["last_id"] = max(ids)
                ch.meta = meta
    return {
        "mode": mode,
        "channel": username,
        "posts": created,
        "alerts": flagged,
        "errors": errors[:10],
        "reports": [r.__dict__ for r in reports],
    }


@router.post("/channels/{channel_id}/scan", response_model=JobOut)
def scan_channel(
    channel_id: str,
    background: BackgroundTasks,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    ch = db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    if not tg.live_available():
        raise HTTPException(
            503,
            "Telegram session not configured. Add TELEGRAM_API_ID/HASH and run scripts/telegram_login.py.",
        )
    job_id = create_job("channel_scan", {"channel": ch.username, "limit": limit}, total=limit)
    background.add_task(run_in_background, lambda ctx: _scan_job(ctx, ch.username, limit), job_id, limit)
    with session_scope() as sdb:
        return job_dict(sdb.get(IngestJob, job_id))


@router.post("/ops/sweep")
async def run_sweep() -> dict:
    """Trigger one monitoring sweep now (also runs on the autopilot timer)."""
    return await autopilot.sweep_once()


@router.post("/ops/discover-join")
async def run_discovery(max_joins: int = 3) -> dict:
    return await autopilot.auto_discover_once(max_joins=max_joins)
