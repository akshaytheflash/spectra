"""Read-only Telegram collector for PUBLIC channels (Telethon).

Scope guard rails (enforced here, not just documented):
  * only public broadcast channels that resolve to a username are accepted;
    invite links, private chats and public groups (megagroups) are rejected;
  * the client uses a read-only user session and never sends messages;
  * collection is rate-limit friendly: bounded per-call limits, sleeps between
    channels, and a resumable ``offset_id`` cursor per target.

Credentials come from ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH`` /
``TELEGRAM_SESSION`` (see ``make telegram-login``). Without a session the
endpoint returns a deterministic *demo mode* result that runs the same pipeline
over the bundled sample channel, so the dashboard flow can be demonstrated
offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from app.core.config import ROOT, settings
from app.core.scope import ScopeDecision, check_public_target, is_public_channel_entity

ProgressFn = Callable[[str], Awaitable[None] | None]


@dataclass
class CollectedMessage:
    external_id: str
    text: str
    timestamp: str
    channel: str
    permalink: str
    media_path: str | None = None
    media_kind: str | None = None  # image|audio
    views: int | None = None
    offset_id: int | None = None


@dataclass
class CollectReport:
    channel: str
    fetched: int = 0
    with_media: int = 0
    next_offset: int | None = None
    skipped: list[str] = field(default_factory=list)
    error: str | None = None
    mode: str = "live"


def _session_path() -> Path | None:
    candidates = []
    if settings.TELEGRAM_SESSION:
        candidates.append(Path(settings.TELEGRAM_SESSION))
    candidates.append(ROOT / ".telegram_session" / "spectra")
    for c in candidates:
        if c.suffix != ".session":
            c = c.with_suffix(".session")
        if c.exists():
            return c
    return None


def live_available() -> bool:
    return bool(settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH and _session_path())


async def _client():
    from telethon import TelegramClient  # type: ignore

    session = _session_path()
    if not session:
        raise RuntimeError("no Telethon session; run `make telegram-login`")
    client = TelegramClient(str(session.with_suffix("")), int(settings.TELEGRAM_API_ID), settings.TELEGRAM_API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session is not authorised")
    return client


async def collect_channel(
    target: str,
    *,
    limit: int = 50,
    offset_id: int | None = None,
    min_id: int | None = None,
    media_dir: Path,
    progress: ProgressFn | None = None,
) -> tuple[list[CollectedMessage], CollectReport]:
    """Fetch the last ``limit`` messages of a public channel (read-only).

    ``min_id`` fetches only messages newer than that id (incremental sweeps).
    """
    decision: ScopeDecision = check_public_target(target)
    if not decision.allowed:
        return [], CollectReport(channel=target, error=decision.reason)
    assert decision.channel
    if not live_available():
        return [], CollectReport(channel=decision.channel, mode="demo", error="no-telegram-session")

    from telethon.tl.types import Channel as TgChannel  # type: ignore
    from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto  # type: ignore

    client = await _client()
    out: list[CollectedMessage] = []
    report = CollectReport(channel=decision.channel)
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        entity = await client.get_entity(decision.channel)
        if not is_public_channel_entity(entity) or not isinstance(entity, TgChannel):
            report.error = "Rejected: target is not a public broadcast channel (private or group)"
            return [], report
        async for msg in client.iter_messages(
            entity, limit=limit, offset_id=offset_id or 0, min_id=min_id or 0
        ):
            if msg is None:
                continue
            report.fetched += 1
            if report.fetched > limit:
                break
            text = msg.message or ""
            media_path = None
            media_kind = None
            if isinstance(msg.media, MessageMediaPhoto):
                media_kind = "image"
            elif isinstance(msg.media, MessageMediaDocument):
                doc = msg.media.document
                mime = getattr(doc, "mime_type", "") or ""
                if mime.startswith("image/"):
                    media_kind = "image"
                elif mime.startswith("audio/") or mime.startswith("video/ogg"):
                    media_kind = "audio"
            if media_kind and msg.media is not None:
                suffix = ".ogg" if media_kind == "audio" else ".jpg"
                dest = media_dir / f"{decision.channel}_{msg.id}{suffix}"
                try:
                    await client.download_media(msg, file=str(dest))
                    media_path = str(dest)
                    report.with_media += 1
                except Exception as exc:  # pragma: no cover - network dependent
                    report.skipped.append(f"{msg.id}: {exc}")
            out.append(
                CollectedMessage(
                    external_id=str(msg.id),
                    text=text,
                    timestamp=(msg.date.isoformat() if msg.date else ""),
                    channel=decision.channel,
                    permalink=f"https://t.me/{decision.channel}/{msg.id}",
                    media_path=media_path,
                    media_kind=media_kind,
                    views=getattr(msg, "views", None),
                    offset_id=msg.id,
                )
            )
            if progress is not None:
                res = progress(f"fetched {report.fetched}/{limit} from @{decision.channel}")
                if asyncio.iscoroutine(res):
                    await res
            await asyncio.sleep(0.35)  # rate-limit friendly
        report.next_offset = out[-1].offset_id if out else offset_id
    except Exception as exc:  # pragma: no cover - network dependent
        report.error = f"collection failed: {exc}"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return out, report


def demo_messages(target: str, limit: int, generated_dir: Path) -> list[CollectedMessage]:
    """Offline stand-in: replays the bundled sample channel through the pipeline."""
    posts_path = generated_dir / "posts.json"
    if not posts_path.exists():
        return []
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    channel = check_public_target(target).channel or "demo"
    # Pick a real synthetic channel so results look coherent, and tag the target.
    sample = [p for p in posts if p.get("label") in ("drugs", "mule_fin", "arms", "fake_docs")]
    sample = [p for p in sample if p.get("image_path") or p.get("audio_path")]
    sample = sample[:limit]
    out = []
    for p in sample:
        media = p.get("image_path") or p.get("audio_path")
        out.append(
            CollectedMessage(
                external_id=p["post_id"],
                text=p["text"],
                timestamp=p["timestamp"],
                channel=channel,
                permalink=f"https://t.me/{channel}/{p['post_id']}",
                media_path=str(generated_dir / media) if media else None,
                media_kind="audio" if p.get("audio_path") else ("image" if p.get("image_path") else None),
                views=1000 + int(hashlib.sha1(p["post_id"].encode()).hexdigest()[:4], 16),
            )
        )
    return out


async def collect(
    targets: list[str], *, limit: int, offset_id: int | None, media_dir: Path,
    progress: ProgressFn | None = None,
) -> tuple[list[CollectedMessage], list[CollectReport], str]:
    """Collect from several public targets; returns (messages, reports, mode)."""
    all_msgs: list[CollectedMessage] = []
    reports: list[CollectReport] = []
    mode = "live" if live_available() else "demo"
    for t in targets:
        decision = check_public_target(t)
        if not decision.allowed:
            reports.append(CollectReport(channel=t, error=decision.reason))
            continue
        if progress:
            res = progress(f"collecting @{decision.channel}")
            if asyncio.iscoroutine(res):
                await res
        if mode == "demo":
            msgs = demo_messages(t, limit, settings.DATA_DIR / "generated")
            rep = CollectReport(channel=decision.channel or t, fetched=len(msgs),
                                with_media=sum(1 for m in msgs if m.media_path), mode="demo")
            if not msgs:
                rep.error = "demo bundle not found - run `make data`"
            reports.append(rep)
            all_msgs.extend(msgs)
            continue
        msgs, rep = await collect_channel(t, limit=limit, offset_id=offset_id, media_dir=media_dir, progress=progress)
        reports.append(rep)
        all_msgs.extend(msgs)
    return all_msgs, reports, mode
