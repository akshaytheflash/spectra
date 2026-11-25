"""Telegram bot for analyst notifications and quick commands.

Uses the plain Bot API over HTTPS (no extra dependency). The bot never reads
channels itself - collection and joining are done by the Telethon user session.
The bot is the analyst's pager: it pushes high-risk alerts and accepts a small
set of commands (status, latest alerts, join, scan).

Polling starts with the API if ``TELEGRAM_BOT_TOKEN`` is set. When no chat is
configured, analysts subscribe by sending ``/start`` to the bot.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import threading
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import Alert, Channel, Post, utcnow
from app.db.session import session_scope

log = logging.getLogger("spectra.bot")
API = "https://api.telegram.org"
SUBS_PATH = settings.DATA_DIR / "bot_subscribers.json"


# --------------------------------------------------------------------------- helpers
def _token() -> str:
    return settings.TELEGRAM_BOT_TOKEN.strip()


def _subscribers() -> list[str]:
    chats: list[str] = []
    if settings.TELEGRAM_BOT_CHAT_ID.strip():
        chats.append(settings.TELEGRAM_BOT_CHAT_ID.strip())
    try:
        if SUBS_PATH.exists():
            data = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                chats.extend(str(c) for c in data)
    except Exception:
        pass
    return list(dict.fromkeys(chats))


def _save_subscribers(chats: list[str]) -> None:
    try:
        SUBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUBS_PATH.write_text(json.dumps(sorted(set(chats))), encoding="utf-8")
    except Exception as exc:
        log.warning("could not persist subscribers: %s", exc)


def _sync_call(method: str, payload: dict, timeout: float = 15.0) -> dict:
    if not _token():
        return {"ok": False, "description": "no bot token"}
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.post(f"{API}/bot{_token()}/{method}", json=payload)
        return res.json()
    except Exception as exc:
        log.warning("bot %s failed: %s", method, exc)
        return {"ok": False, "description": str(exc)}


def send_message(text: str, chat_id: str | None = None) -> dict:
    chats = [chat_id] if chat_id else _subscribers()
    out: dict = {"ok": False, "sent": 0}
    for chat in chats:
        result = _sync_call(
            "sendMessage",
            {"chat_id": chat, "text": text[:3900], "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        if result.get("ok"):
            out["sent"] = int(out.get("sent", 0)) + 1
        else:
            out["error"] = result.get("description")
    return out


def _risk_icon(risk: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get((risk or "").upper(), "⚪")


def notify_alert(alert: Alert, post: Post | None = None) -> dict:
    """Push one alert to subscribed analysts. Safe to call from worker threads."""
    if not _token():
        return {"ok": False, "sent": 0}
    post = post or alert.post
    emojis = " ".join(str(e.get("emoji", "")) for e in (alert.emoji or [])[:6])
    idents = (alert.identifier_summary or {}).get("values") or []
    lines = [
        f"{_risk_icon(alert.risk)} <b>{html.escape(alert.category.replace('_', ' ').title())}</b> · {alert.risk} · {int(alert.confidence * 100)}%",
        f"@{post.channel if post else 'unknown'} · {alert.flag_source}",
        "",
        html.escape((post.text if post else "")[:600]),
    ]
    if emojis:
        lines.append(f"\nSignal emojis: {emojis}")
    if idents:
        lines.append("Identifiers: " + ", ".join(f"<code>{i}</code>" for i in idents[:5]))
    if post and post.permalink:
        lines.append(f"\n<a href=\"{post.permalink}\">Open in Telegram</a>")
    lines.append(f"Alert ID: <code>{alert.id}</code>")
    return send_message("\n".join(lines))


def notify_text(text: str) -> dict:
    return send_message(text)


# --------------------------------------------------------------------------- commands
def _status_text() -> str:
    with session_scope() as db:
        posts = db.execute(select(func.count(Post.id))).scalar_one()
        alerts = db.execute(select(func.count(Alert.id))).scalar_one()
        high = db.execute(select(func.count(Alert.id)).where(Alert.risk == "HIGH")).scalar_one()
        new = db.execute(select(func.count(Alert.id)).where(Alert.status == "new")).scalar_one()
        channels = db.execute(select(func.count(Channel.id)).where(Channel.is_monitored.is_(True))).scalar_one()
    return (
        f"<b>SPECTRA status</b>\n"
        f"Monitored channels: {channels}\n"
        f"Messages scanned: {posts}\n"
        f"Alerts: {alerts} ({new} new)\n"
        f"High risk: {high}"
    )


def _alerts_text(limit: int = 6) -> str:
    with session_scope() as db:
        rows = db.execute(
            select(Alert).order_by(Alert.created_at.desc()).limit(limit)
        ).scalars().all()
    if not rows:
        return "No alerts yet."
    lines = ["<b>Latest alerts</b>"]
    for a in rows:
        post = a.post
        text = html.escape((post.text if post else "").replace("\n", " ")[:110])
        lines.append(
            f"{_risk_icon(a.risk)} <b>{a.category}</b> @{post.channel if post else '?'} · {int(a.confidence*100)}%\n{text}"
        )
    return "\n\n".join(lines)


def _channels_text() -> str:
    with session_scope() as db:
        rows = db.execute(
            select(Channel).where(Channel.is_monitored.is_(True)).order_by(Channel.last_seen.desc()).limit(20)
        ).scalars().all()
    if not rows:
        return "No monitored channels. Use /join @channel or the dashboard."
    return "<b>Monitored channels</b>\n" + "\n".join(
        f"• @{c.username} · {c.join_status} · {c.subscriber_count or '?'} subs" for c in rows
    )


def _upsert_channel(username: str, title: str = "", subscribers: int | None = None,
                    joined: bool = True, added_by: str = "bot") -> None:
    username = username.lstrip("@")
    with session_scope() as db:
        ch = db.execute(select(Channel).where(Channel.username == username)).scalar_one_or_none()
        if ch is None:
            ch = Channel(username=username, title=title or username, added_by=added_by)
            db.add(ch)
        ch.is_monitored = True
        ch.join_status = "joined" if joined else ch.join_status
        if joined:
            ch.joined_at = ch.joined_at or utcnow()
        ch.title = title or ch.title
        if subscribers is not None:
            ch.subscriber_count = subscribers


async def _handle_scan(chat_id: str, target: str) -> None:
    from app.core.scope import check_public_target
    from app.pipeline import ingest
    from app.pipeline.telegram import collect
    from app.pipeline.telegram_admin import inspect_channel

    decision = check_public_target(target)
    if not decision.allowed:
        send_message(f"Rejected: {decision.reason}", chat_id)
        return
    send_message(f"Scanning @{decision.channel} (last 30 messages)…", chat_id)
    info = await inspect_channel(decision.channel or target)
    if info.error and info.error != "no-telegram-session":
        send_message(f"Could not resolve @{decision.channel}: {info.error}", chat_id)
        return
    msgs, reports, mode = await collect(
        [decision.channel or target], limit=30, offset_id=None,
        media_dir=settings.UPLOAD_DIR / "telegram" / "bot",
    )
    created = flagged = 0
    for m in msgs:
        with session_scope() as db:
            post = ingest.create_post(
                db, text=m.text, channel=m.channel, timestamp=m.timestamp,
                external_id=m.external_id, permalink=m.permalink, views=m.views,
            )
            res = ingest.process_post(db, post, do_ocr=False)
            created += 1
            if res.alert_id:
                flagged += 1
                alert = db.get(Alert, res.alert_id)
                if alert is not None:
                    notify_alert(alert, post)
    _upsert_channel(decision.channel or target, info.title, info.subscribers)
    err = "; ".join(r.error for r in reports if r.error)
    send_message(
        f"Scan of @{decision.channel} complete ({mode}): {created} messages, {flagged} alerts."
        + (f"\nNote: {err}" if err else ""),
        chat_id,
    )


def _handle_join(chat_id: str, target: str) -> None:
    from app.pipeline.telegram_admin import join_channel

    send_message(f"Joining {target}…", chat_id)
    try:
        info = asyncio.run(join_channel(target))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            info = loop.run_until_complete(join_channel(target))
        finally:
            loop.close()
    if info.error:
        send_message(f"Join failed: {info.error}", chat_id)
        return
    _upsert_channel(info.username, info.title, info.subscribers)
    send_message(
        f"Joined @{info.username} ({info.subscribers or '?'} subscribers) and added it to monitoring.",
        chat_id,
    )


def _dispatch(chat_id: str, text: str) -> None:
    parts = (text or "").strip().split(maxsplit=1)
    command = parts[0].split("@")[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    if command in ("/start", "/subscribe"):
        chats = _subscribers()
        if chat_id not in chats:
            chats.append(chat_id)
            _save_subscribers(chats)
        send_message(
            "<b>SPECTRA</b> — Rajasthan Police OSINT console\n"
            "You are subscribed to high-risk alerts.\n\n"
            "/status · /alerts · /channels\n"
            "/join @channel · /scan @channel\n"
            "/stop to unsubscribe",
            chat_id,
        )
    elif command == "/stop":
        _save_subscribers([c for c in _subscribers() if c != chat_id])
        send_message("Unsubscribed.", chat_id)
    elif command == "/status":
        send_message(_status_text(), chat_id)
    elif command == "/alerts":
        send_message(_alerts_text(), chat_id)
    elif command == "/channels":
        send_message(_channels_text(), chat_id)
    elif command == "/join" and arg:
        _handle_join(chat_id, arg)
    elif command == "/scan" and arg:
        _handle_scan_sync(chat_id, arg)
    else:
        send_message("Unknown command. Try /status, /alerts, /join @channel, /scan @channel.", chat_id)


def _handle_scan_sync(chat_id: str, target: str) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_handle_scan(chat_id, target))
    except Exception as exc:
        send_message(f"Scan failed: {exc}", chat_id)
    finally:
        loop.close()


# --------------------------------------------------------------------------- polling
class BotPoller:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if not _token() or self.running:
            return
        self._thread = threading.Thread(target=self._run, name="spectra-bot", daemon=True)
        self._thread.start()
        log.info("bot poller started")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        offset = 0
        while not self._stop.is_set():
            try:
                with httpx.Client(timeout=40) as client:
                    res = client.get(
                        f"{API}/bot{_token()}/getUpdates",
                        params={"offset": offset, "timeout": 25, "allowed_updates": '["message"]'},
                    )
                    body = res.json()
                if not body.get("ok"):
                    time.sleep(3)
                    continue
                for update in body.get("result", []):
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message") or {}
                    chat_id = str((message.get("chat") or {}).get("id", ""))
                    text = message.get("text") or ""
                    if chat_id and text.startswith("/"):
                        try:
                            _dispatch(chat_id, text)
                        except Exception as exc:
                            log.warning("command failed: %s", exc)
            except Exception as exc:
                log.debug("poll error: %s", exc)
                time.sleep(3)


_poller = BotPoller()


def start_bot() -> None:
    _poller.start()


def stop_bot() -> None:
    _poller.stop()


def bot_status() -> dict:
    return {
        "enabled": bool(_token()),
        "running": _poller.running,
        "subscribers": len(_subscribers()),
    }
