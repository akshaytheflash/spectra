"""Scope guard rails -- enforced in code, not just policy.

SPECTRA only collects from PUBLIC Telegram channels and public t.me links.
This module rejects anything that could imply joining a private group,
impersonating someone, or acting on a private account, and refuses to call the
Telegram API without a read-only user session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PUBLIC_LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?(?P<slug>[A-Za-z0-9_]{4,32})(?:/(?P<msg>\d+))?/?$",
    re.I,
)
HANDLE_RE = re.compile(r"^@?(?P<slug>[A-Za-z][A-Za-z0-9_]{3,31})$")

FORBIDDEN_PATTERNS = [
    (re.compile(r"t\.me/(?:\+|joinchat/)", re.I), "invite links (t.me/+ or t.me/joinchat) are private-group links"),
    (re.compile(r"telegram\.(?:dog|me)/[a-z0-9_]*\?start", re.I), "start-payload links can route to private chats"),
    (re.compile(r"\bwhatsapp\.com/channel\b|\bwa\.me/\d", re.I), "WhatsApp private contact links are out of scope"),
    (re.compile(r"^@?c\d{6,}", re.I), "anonymous/private channel ids are not public channels"),
]


@dataclass
class ScopeDecision:
    allowed: bool
    channel: str | None = None
    message_id: int | None = None
    reason: str = ""
    public_url: str | None = None


def check_public_target(target: str) -> ScopeDecision:
    """Validate a user-supplied channel link or @username for public collection."""
    t = (target or "").strip()
    for rx, reason in FORBIDDEN_PATTERNS:
        if rx.search(t):
            return ScopeDecision(False, reason=f"Rejected: {reason}")
    m = PUBLIC_LINK_RE.match(t)
    if m:
        slug = m.group("slug")
        msg = int(m.group("msg")) if m.group("msg") else None
        return ScopeDecision(True, channel=slug, message_id=msg, public_url=f"https://t.me/{slug}")
    h = HANDLE_RE.match(t)
    if h:
        slug = h.group("slug")
        return ScopeDecision(True, channel=slug, public_url=f"https://t.me/{slug}")
    return ScopeDecision(False, reason="Rejected: not a public t.me channel/post link or @username")


def is_public_channel_entity(entity: object) -> bool:
    """Telethon entity check: channels must be broadcast public channels.

    Public groups (megagroups) and private channels without a username are out
    of scope; private channels are detectable by the absence of a username.
    """
    try:
        from telethon.tl.types import Channel  # type: ignore

        if not isinstance(entity, Channel):
            return False
        if getattr(entity, "megagroup", False):
            return False  # public groups are out of scope too
        return bool(getattr(entity, "username", None))
    except Exception:
        return False
