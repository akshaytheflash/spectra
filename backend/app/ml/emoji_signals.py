"""Explicit emoji-signal detection for illicit-trade ads.

The ML classifier already sees emoji names after normalisation, but an analyst
needs a readable, auditable answer to "which emojis triggered this?". This
module scans the *original* text (positions preserved) and returns typed
signals such as ``🍁 -> cannabis (drugs)`` and ``🔫 -> firearm (arms)``.

It also provides a small post-hoc boost for emoji-led ads, which are common in
Telegram listings ("fresh stock 🍁🔌💨") and easy for a text model to under-read.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Iterable

import emoji as emoji_lib
import yaml

from app.core.config import ROOT

SIGNALS_PATH = ROOT / "data" / "emoji_signals.yaml"

_VARIATION = "\ufe0f"
_EMOJI_ONLY_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(slots=True)
class EmojiSignal:
    emoji: str
    category: str
    meaning: str
    weight: float
    start: int
    end: int
    ambiguous: bool = False
    repeated: int = 1

    def as_dict(self) -> dict:
        return {
            "emoji": self.emoji,
            "category": self.category,
            "meaning": self.meaning,
            "weight": round(self.weight, 3),
            "start": self.start,
            "end": self.end,
            "ambiguous": self.ambiguous,
            "repeated": self.repeated,
        }


def _key(value: str) -> str:
    """Normalise variation selectors so ❄ and ❄️ match the same entry."""
    return value.replace(_VARIATION, "")


@functools.lru_cache(maxsize=1)
def _catalogue() -> dict[str, dict]:
    if not SIGNALS_PATH.exists():
        return {}
    raw = yaml.safe_load(SIGNALS_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for item in raw.get("signals", []):
        em = str(item.get("emoji", "")).strip()
        if not em:
            continue
        out[_key(em)] = {
            "emoji": em,
            "category": str(item.get("category", "other_illegal")),
            "meaning": str(item.get("meaning", "")),
            "weight": float(item.get("weight", 0.5)),
            "ambiguous": bool(item.get("ambiguous", False)),
        }
    return out


def emoji_only(text: str) -> bool:
    """True when a message is essentially only emojis/punctuation."""
    if not (text or "").strip():
        return False
    stripped = emoji_lib.replace_emoji(text, replace="")
    return not _EMOJI_ONLY_STRIP.sub("", stripped)


def scan(text: str) -> list[EmojiSignal]:
    """Return every curated emoji signal found in ``text``, in order."""
    text = text or ""
    if not text.strip():
        return []
    catalogue = _catalogue()
    if not catalogue:
        return []
    only = emoji_only(text)
    found: list[EmojiSignal] = []
    for item in emoji_lib.emoji_list(text):
        match = str(item.get("emoji") or item.get("match") or "")
        info = catalogue.get(_key(match))
        if info is None:
            continue
        weight = info["weight"]
        # Ambiguous emojis (🔫 in a game chat, 💊 in a pharmacy post) only carry
        # real weight when the message is emoji-led; otherwise halve them.
        weight = weight * (1.15 if only else 0.55) if info["ambiguous"] else weight
        found.append(
            EmojiSignal(
                emoji=info["emoji"],
                category=info["category"],
                meaning=info["meaning"],
                weight=min(0.98, weight),
                start=int(item.get("match_start", item.get("start", 0))),
                end=int(item.get("match_end", item.get("end", 0))),
                ambiguous=info["ambiguous"],
            )
        )
    # mark repeats: 🍁🍁🍁 is a stronger listing signal than one leaf
    counts: dict[str, int] = {}
    for s in found:
        counts[_key(s.emoji)] = counts.get(_key(s.emoji), 0) + 1
    for s in found:
        s.repeated = counts.get(_key(s.emoji), 1)
        if s.repeated >= 3:
            s.weight = min(0.98, s.weight + 0.1)
    return found


def category_scores(signals: Iterable[EmojiSignal]) -> dict[str, float]:
    """Aggregate signal weight per category (capped at 1.0)."""
    scores: dict[str, float] = {}
    for s in signals:
        scores[s.category] = scores.get(s.category, 0.0) + s.weight
    return {k: round(min(1.0, v), 3) for k, v in sorted(scores.items(), key=lambda kv: -kv[1])}


def boost(text: str, signals: list[EmojiSignal] | None = None) -> dict[str, float]:
    """Category -> probability floor for emoji-led ads (empty when no signal).

    Only fires when emojis are the message, or when a category has enough
    emoji weight to be meaningful on its own. A single ambiguous 🔫 in a
    paragraph never triggers a flag.
    """
    signals = signals if signals is not None else scan(text)
    if not signals:
        return {}
    scores = category_scores(signals)
    only = emoji_only(text)
    out: dict[str, float] = {}
    for category, score in scores.items():
        if only and score >= 0.45:
            out[category] = min(0.82, 0.5 + score * 0.35)
        elif score >= 0.95:
            out[category] = min(0.7, 0.45 + score * 0.2)
    return out


def summary(signals: Iterable[EmojiSignal], limit: int = 8) -> list[dict]:
    """Compact list for alerts / dashboard chips."""
    out = []
    for s in list(signals)[:limit]:
        out.append({"emoji": s.emoji, "category": s.category, "meaning": s.meaning})
    return out
