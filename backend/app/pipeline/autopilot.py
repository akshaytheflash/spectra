"""Autonomous monitoring loop.

Every ``AUTO_PILOT_INTERVAL`` seconds, sweep each monitored channel for new
messages, run the same pipeline as a manual scan, and push high-risk alerts to
the bot. Optionally discover and join new public channels by keyword.

The loop is fail-soft: one channel error never stops the sweep. It only starts
when a live Telethon session is configured, so the offline demo is unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Alert, Channel, Post
from app.db.session import session_scope
from app.pipeline import ingest
from app.pipeline import telegram as tg
from app.pipeline.llm_batch import scan_texts

log = logging.getLogger("spectra.autopilot")

_task: asyncio.Task | None = None
_discovery_task: asyncio.Task | None = None


def _monitored() -> list[dict]:
    with session_scope() as db:
        rows = db.execute(
            select(Channel).where(Channel.is_monitored.is_(True)).order_by(Channel.last_scraped.asc().nullsfirst())
        ).scalars().all()
        return [
            {
                "username": c.username,
                "meta": dict(c.meta or {}),
                "title": c.title,
            }
            for c in rows
        ]


def _already_seen(channel: str, external_id: str) -> bool:
    with session_scope() as db:
        return db.execute(
            select(Post.id).where(Post.channel == channel, Post.external_id == external_id).limit(1)
        ).scalar_one_or_none() is not None


def _store_channel_meta(username: str, *, offset_id: int | None, scanned: int) -> None:
    with session_scope() as db:
        ch = db.execute(select(Channel).where(Channel.username == username)).scalar_one_or_none()
        if ch is None:
            return
        meta = dict(ch.meta or {})
        if offset_id:
            meta["last_id"] = int(offset_id)
        meta["last_sweep_messages"] = scanned
        ch.meta = meta
        ch.last_scraped = datetime.now(timezone.utc)
        ch.last_seen = ch.last_scraped
        ch.join_status = "joined"


async def sweep_once(*, notify: bool = True) -> dict:
    """One monitoring sweep. Returns a small summary for the API/logs."""
    if not tg.live_available():
        return {"ok": False, "reason": "no-telegram-session", "channels": 0, "alerts": 0}
    channels = _monitored()
    total_msgs = total_alerts = 0
    errors: list[str] = []
    for row in channels:
        username = row["username"]
        last_id = int((row.get("meta") or {}).get("last_id") or 0)
        try:
            msgs, reports, mode = await tg.collect_channel(
                username,
                limit=settings.AUTO_PILOT_BATCH,
                min_id=last_id,
                media_dir=settings.UPLOAD_DIR / "telegram" / "autopilot",
            )
        except Exception as exc:
            errors.append(f"{username}: {exc}")
            continue
        msgs = [m for m in msgs if not _already_seen(m.channel, m.external_id)]
        if not msgs:
            continue
        from app.ml.engine import engine

        texts = [m.text or "" for m in msgs]
        preds = engine().predict_many(texts)
        verdicts = scan_texts(texts, preds, context=username, mode="candidates")
        for m, pred, verdict in zip(msgs, preds, verdicts):
            with session_scope() as db:
                try:
                    post = ingest.create_post(
                        db,
                        text=m.text,
                        channel=m.channel,
                        timestamp=m.timestamp,
                        external_id=m.external_id,
                        permalink=m.permalink,
                        views=m.views,
                    )
                    res = ingest.process_post(
                        db,
                        post,
                        media_path=None,
                        prediction=pred,
                        llm_verdict=verdict,
                        do_llm=False,
                        do_ocr=False,
                    )
                    total_msgs += 1
                    if res.alert_id:
                        total_alerts += 1
                        if notify:
                            alert = db.get(Alert, res.alert_id)
                            if alert is not None and alert.risk in ("HIGH", "MEDIUM"):
                                from app.bot.service import notify_alert

                                notify_alert(alert, post)
                except Exception as exc:
                    errors.append(f"{username}/{m.external_id}: {exc}")
        _store_channel_meta(
            username,
            offset_id=max(int(m.external_id) for m in msgs if str(m.external_id).isdigit()) if msgs else None,
            scanned=len(msgs),
        )
        await asyncio.sleep(1.0)
    summary = {
        "ok": True,
        "channels": len(channels),
        "messages": total_msgs,
        "alerts": total_alerts,
        "errors": errors[:10],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("sweep: %s channels, %s messages, %s alerts", len(channels), total_msgs, total_alerts)
    return summary


async def auto_discover_once(*, max_joins: int = 3) -> dict:
    """Search curated keywords and join a few new public channels."""
    if not (settings.AUTO_JOIN_ENABLED and tg.live_available()):
        return {"ok": False, "reason": "disabled-or-offline", "joined": []}
    from app.pipeline.telegram_admin import discover

    with session_scope() as db:
        known = {c.username.lower() for c in db.execute(select(Channel)).scalars().all()}
    keywords = settings.auto_join_keywords[:6]
    joined: list[str] = []
    for kw in keywords:
        if len(joined) >= max_joins:
            break
        try:
            res = await discover([kw], per_keyword=2, join=False)
        except Exception as exc:
            log.warning("discover %s failed: %s", kw, exc)
            continue
        for row in res.get("results", []):
            if len(joined) >= max_joins:
                break
            username = row.get("username")
            if not username or username.lower() in known:
                continue
            # require the keyword to appear in title/about, not just search rank
            blob = f"{row.get('title','')} {row.get('about','')}".lower()
            if kw.lower() not in blob:
                continue
            from app.pipeline.telegram_admin import join_channel

            info = await join_channel(username)
            if info.error:
                continue
            known.add(username.lower())
            joined.append(username)
            with session_scope() as db:
                ch = db.execute(select(Channel).where(Channel.username == username)).scalar_one_or_none()
                if ch is None:
                    ch = Channel(username=username, title=info.title, added_by="autopilot")
                    db.add(ch)
                ch.is_monitored = True
                ch.join_status = "joined"
                ch.joined_at = datetime.now(timezone.utc)
                ch.subscriber_count = info.subscribers
                ch.description = info.about or ch.description
    return {"ok": True, "joined": joined}


async def _loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("sweep failed: %s", exc)
        await asyncio.sleep(max(120, int(settings.AUTO_PILOT_INTERVAL)))


async def _discovery_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await auto_discover_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("auto-discover failed: %s", exc)
        await asyncio.sleep(max(1800, int(settings.AUTO_PILOT_INTERVAL) * 4))


def start() -> None:
    global _task, _discovery_task
    if not settings.AUTO_PILOT_ENABLED:
        return
    if not tg.live_available():
        log.info("autopilot idle: no Telethon session")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="spectra-autopilot")
    if settings.AUTO_JOIN_ENABLED:
        _discovery_task = asyncio.create_task(_discovery_loop(), name="spectra-autodiscover")


async def stop() -> None:
    global _task, _discovery_task
    for task in (_task, _discovery_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
    _task = None
    _discovery_task = None


def status() -> dict:
    return {
        "enabled": settings.AUTO_PILOT_ENABLED,
        "running": bool(_task and not _task.done()),
        "discovery": bool(_discovery_task and not _discovery_task.done()),
        "interval_seconds": settings.AUTO_PILOT_INTERVAL,
        "batch": settings.AUTO_PILOT_BATCH,
    }
