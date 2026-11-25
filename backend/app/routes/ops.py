"""Operations: integration status, LLM connectivity, emoji catalogue, test pings."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot.service import bot_status, notify_text
from app.core.config import settings
from app.db.models import Alert, Channel, Post
from app.db.session import get_db
from app.ml.llm import scanner
from app.pipeline import autopilot
from app.pipeline import telegram as tg

router = APIRouter(tags=["ops"])


@router.get("/ops/status")
def ops_status(db: Session = Depends(get_db)) -> dict:
    return {
        "llm": {
            "configured": settings.llm_ready,
            "model": settings.BHARATCODE_MODEL,
            "base_url": settings.BHARATCODE_BASE_URL,
            "scan_mode": "candidates",
        },
        "telegram": {
            "user_session": tg.live_available(),
            "bot": bot_status(),
            "auto_join": settings.AUTO_JOIN_ENABLED,
        },
        "autopilot": autopilot.status(),
        "counts": {
            "channels": db.execute(select(func.count(Channel.id))).scalar_one(),
            "monitored": db.execute(
                select(func.count(Channel.id)).where(Channel.is_monitored.is_(True))
            ).scalar_one(),
            "posts": db.execute(select(func.count(Post.id))).scalar_one(),
            "alerts": db.execute(select(func.count(Alert.id))).scalar_one(),
        },
    }


@router.post("/ops/llm/test")
def test_llm() -> dict:
    return scanner().ping()


@router.post("/ops/notify-test")
def test_notification() -> dict:
    return notify_text(
        "<b>SPECTRA</b> connected.\nThis is a test notification from the dashboard."
    )


@router.get("/ops/emoji")
def emoji_catalogue() -> dict:
    from app.ml.emoji_signals import _catalogue

    items = list(_catalogue().values())
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item["category"], []).append(item)
    return {"total": len(items), "categories": grouped}
