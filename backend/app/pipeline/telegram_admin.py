"""Public-channel administration with a Telethon user session.

The original collector was deliberately read-only. Investigation work needs to
*find* and *join* public channels too, so this module wraps the two extra
Telethon calls:

  contacts.SearchRequest   - discover public channels by keyword
  channels.JoinChannelRequest / LeaveChannelRequest - join or leave

Scope rules are unchanged: only public broadcast channels with a username.
Invite links, private channels and public groups are rejected before any
Telegram call is made (see ``core/scope.py``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.scope import check_public_target, is_public_channel_entity
from app.pipeline.telegram import _client, _session_path, live_available

log = logging.getLogger("spectra.telegram_admin")

JOIN_SLEEP = 1.2  # seconds between joins; keep well below flood limits
SEARCH_SLEEP = 0.8


@dataclass
class ChannelInfo:
    username: str
    title: str = ""
    about: str = ""
    subscribers: int | None = None
    is_megagroup: bool = False
    verified: bool = False
    joined: bool = False
    url: str = ""
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "title": self.title,
            "about": self.about,
            "subscribers": self.subscribers,
            "verified": self.verified,
            "joined": self.joined,
            "url": self.url,
            "error": self.error,
        }


def is_live() -> bool:
    return live_available()


def _info(entity: object, *, joined: bool = False) -> ChannelInfo:
    username = getattr(entity, "username", "") or ""
    return ChannelInfo(
        username=username,
        title=getattr(entity, "title", "") or username,
        subscribers=getattr(entity, "participants_count", None),
        is_megagroup=bool(getattr(entity, "megagroup", False)),
        verified=bool(getattr(entity, "verified", False)),
        joined=joined,
        url=f"https://t.me/{username}" if username else "",
    )


async def _about(client, entity) -> str:
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest

        full = await client(GetFullChannelRequest(entity))
        return getattr(full.full_chat, "about", "") or ""
    except Exception:
        return ""


async def _resolve(client, target: str):
    """Resolve a target through get_entity, then validate public-channel scope."""
    entity = await client.get_entity(target)
    if not is_public_channel_entity(entity):
        raise ValueError("Target is not a public broadcast channel (private or group)")
    return entity


async def inspect_channel(target: str) -> ChannelInfo:
    """Read title/description/subscribers for a public channel without joining."""
    decision = check_public_target(target)
    if not decision.allowed or not decision.channel:
        return ChannelInfo(username="", error=decision.reason)
    if not is_live():
        return ChannelInfo(username=decision.channel, error="no-telegram-session")
    client = await _client()
    try:
        entity = await _resolve(client, decision.channel)
        info = _info(entity, joined=False)
        info.about = await _about(client, entity)
        return info
    except Exception as exc:
        return ChannelInfo(username=decision.channel, error=str(exc)[:200])
    finally:
        await client.disconnect()


async def join_channel(target: str) -> ChannelInfo:
    """Join a public broadcast channel. Returns its info on success."""
    decision = check_public_target(target)
    if not decision.allowed or not decision.channel:
        return ChannelInfo(username="", error=decision.reason)
    if not is_live():
        return ChannelInfo(username=decision.channel, error="no-telegram-session")
    from telethon.tl.functions.channels import JoinChannelRequest

    client = await _client()
    try:
        entity = await _resolve(client, decision.channel)
        already = not bool(getattr(entity, "left", True))
        if not already:
            await client(JoinChannelRequest(entity))
            await asyncio.sleep(JOIN_SLEEP)
        info = _info(entity, joined=True)
        info.about = await _about(client, entity)
        return info
    except Exception as exc:
        log.warning("join %s failed: %s", decision.channel, exc)
        return ChannelInfo(username=decision.channel, error=str(exc)[:200])
    finally:
        await client.disconnect()


async def leave_channel(target: str) -> dict:
    decision = check_public_target(target)
    if not decision.allowed or not decision.channel:
        return {"ok": False, "error": decision.reason}
    if not is_live():
        return {"ok": False, "error": "no-telegram-session"}
    from telethon.tl.functions.channels import LeaveChannelRequest

    client = await _client()
    try:
        entity = await _resolve(client, decision.channel)
        await client(LeaveChannelRequest(entity))
        return {"ok": True, "channel": decision.channel}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        await client.disconnect()


async def search_public(query: str, *, limit: int = 20) -> list[ChannelInfo]:
    """Global Telegram search restricted to public broadcast channels."""
    query = (query or "").strip()
    if not query or not is_live():
        return []
    from telethon.tl.functions.contacts import SearchRequest
    from telethon.tl.types import Channel as TgChannel

    client = await _client()
    out: list[ChannelInfo] = []
    seen: set[str] = set()
    try:
        result = await client(SearchRequest(q=query, limit=max(1, min(limit, 50))))
        for chat in getattr(result, "chats", []) or []:
            if not isinstance(chat, TgChannel) or getattr(chat, "megagroup", False):
                continue
            username = getattr(chat, "username", None)
            if not username or username.lower() in seen:
                continue
            seen.add(username.lower())
            out.append(_info(chat))
        await asyncio.sleep(SEARCH_SLEEP)
    except Exception as exc:
        log.warning("search %s failed: %s", query, exc)
    finally:
        await client.disconnect()
    return out


async def discover(
    keywords: list[str], *, per_keyword: int = 4, join: bool = False
) -> dict:
    """Search several keywords; optionally join the discovered channels."""
    if not is_live():
        return {"ok": False, "error": "no-telegram-session", "results": [], "joined": []}
    results: list[dict] = []
    joined: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        channels = await search_public(kw, limit=max(6, per_keyword * 3))
        picked = [c for c in channels if c.username.lower() not in seen][:per_keyword]
        for c in picked:
            seen.add(c.username.lower())
            row = c.as_dict()
            row["keyword"] = kw
            results.append(row)
        if join:
            for c in picked:
                info = await join_channel(c.username)
                if info.error:
                    row = next((r for r in results if r["username"] == c.username), None)
                    if row:
                        row["join_error"] = info.error
                else:
                    joined.append(c.username)
                    row = next((r for r in results if r["username"] == c.username), None)
                    if row:
                        row["joined"] = True
    return {"ok": True, "results": results, "joined": joined}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
