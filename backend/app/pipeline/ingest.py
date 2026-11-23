"""Ingest + analysis pipeline: turns raw posts (text/image/QR/voice) into
classified, identifier-linked, evidence-backed alerts.

Stages for every post:
  1. media detection (image -> OCR + QR; audio -> transcription)
  2. text assembly (post text + OCR text + transcript [+ QR payee/VPA])
  3. identifier extraction with source tags (text|image_ocr|qr|voice)
  4. hybrid classification + explanation + financial sub-classification
  5. hashes (text, pHash, audio SHA-256) for dedupe/correlation
  6. alert + immutable evidence snapshot for flagged posts
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import storage
from app.db.models import Alert, AlertStatus, Channel, Evidence, Identifier, Post, PostStatus, utcnow
from app.ml import extractors
from app.ml.engine import engine
from app.pipeline import evidence as evidence_mod
from app.pipeline import speech, vision

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".webm"}

_LANG_RE = {
    "hi": re.compile(r"[\u0900-\u097F]"),
    "en": re.compile(r"[A-Za-z]{3,}"),
}


@dataclass
class ProcessResult:
    post_id: str
    status: str
    category: str
    risk: str
    confidence: float
    alert_id: str | None = None
    identifier_count: int = 0
    media: dict = field(default_factory=dict)
    error: str | None = None


def _parse_ts(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return utcnow()
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return utcnow()


def detect_language(text: str) -> str:
    if _LANG_RE["hi"].search(text or ""):
        return "hi"
    if re.search(r"\b(hai|chahiye|kiraye|maal|kar|karo|nahi|bhai|paisa|dega)\b", (text or "").lower()):
        return "hinglish"
    return "en"


def get_or_create_channel(db: Session, username: str, platform: str = "telegram", **extra) -> Channel:
    username = (username or "unknown").lstrip("@")
    ch = db.execute(select(Channel).where(Channel.username == username, Channel.platform == platform)).scalar_one_or_none()
    if ch is None:
        ch = Channel(platform=platform, username=username, title=extra.get("title") or username,
                     is_public=True, meta={k: v for k, v in extra.items() if k != "title"})
        db.add(ch)
        db.flush()
    else:
        if extra.get("title"):
            ch.title = extra["title"]
        for k, v in extra.items():
            if k != "title" and v is not None:
                if hasattr(ch, k):
                    setattr(ch, k, v)
    ch.last_seen = utcnow()
    return ch


# --------------------------------------------------------------------------- media
def _image_from_path(path: Path) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(path.read_bytes())) as im:
            return im.convert("RGB")
    except Exception:
        return None


def process_media(db: Session, post: Post, media_path: Path, *, do_ocr: bool = True,
                  max_ocr_passes: int | None = None) -> dict:
    """Run OCR/QR or speech on a post's media and persist the artifacts."""
    out: dict = {"type": None, "text": "", "ocr": {}, "qr": {}, "transcript": {}}
    suffix = media_path.suffix.lower()
    if suffix in IMAGE_EXT:
        out["type"] = "image"
        stored = storage.copy_into(media_path, f"posts/{post.id}{suffix}")
        res = vision.analyze_image(
            stored.local_path, save_dir=settings.UPLOAD_DIR / "preprocessed" / post.id,
            do_ocr=do_ocr, max_ocr_passes=max_ocr_passes,
        )
        out["ocr"] = res.as_dict()
        out["qr"] = {"codes": [q.as_dict() for q in res.qr_codes]}
        post.ocr = out["ocr"]
        post.qr = out["qr"]
        post.media_sha256 = res.image_sha256
        img = _image_from_path(stored.local_path)
        if img is not None:
            try:
                post.image_hash = str(imagehash.phash(img))
            except Exception:
                post.image_hash = None
        out["text"] = res.text or ""
        out["stored_path"] = str(stored.local_path)
        out["public_url"] = stored.public_url
    elif suffix in AUDIO_EXT:
        out["type"] = "audio"
        stored = storage.copy_into(media_path, f"posts/{post.id}{suffix}")
        try:
            tr = speech.transcribe_file(stored.local_path, workdir=settings.UPLOAD_DIR / "tmp")
            post.transcript = tr.as_dict()
            post.audio_hash = tr.audio_sha256
            post.media_sha256 = tr.audio_sha256
            out["transcript"] = tr.as_dict()
            out["text"] = tr.text
        except Exception as exc:  # keep the pipeline resilient
            out["transcript"] = {"error": str(exc)}
            post.transcript = {"error": str(exc)}
        out["stored_path"] = str(stored.local_path)
        out["public_url"] = stored.public_url
    return out


# --------------------------------------------------------------------------- post
def process_post(
    db: Session,
    post: Post,
    *,
    media_path: Path | None = None,
    do_ocr: bool = True,
    max_ocr_passes: int | None = None,
    commit: bool = True,
    prediction: dict | None = None,
) -> ProcessResult:
    """Classify a post, extract identifiers, create alert + evidence if flagged.

    ``prediction`` lets bulk callers pass a pre-computed :meth:`HybridEngine.predict`
    result (batched inference over thousands of posts).
    """
    eng = engine()
    media: dict = {}
    if media_path is not None and media_path.exists():
        media = process_media(db, post, media_path, do_ocr=do_ocr, max_ocr_passes=max_ocr_passes)
        if media.get("type"):
            post.media_type = media["type"]
            post.media_path = str(media.get("stored_path") or media_path)

    source_map: list[tuple[str, str]] = [("text", post.text or "")]
    if media.get("ocr", {}).get("text"):
        source_map.append(("image_ocr", media["ocr"]["text"]))
    if media.get("qr", {}).get("codes"):
        for c in media["qr"]["codes"]:
            if c.get("kind") == "upi":
                source_map.append(("qr", f"{c['parsed'].get('vpa','')} payee {c['parsed'].get('payee_name','')}"))
            elif c.get("kind") == "wallet":
                source_map.append(("qr", c["parsed"].get("address", "")))
            elif c.get("kind") == "url":
                source_map.append(("qr", c["parsed"].get("url", "")))
    if media.get("transcript", {}).get("text"):
        source_map.append(("voice", media["transcript"]["text"]))
    full_text = "\n".join(p for _, p in source_map if p)

    # --- identifiers (per source) -----------------------------------------
    found_all: list[extractors.Found] = []
    for source, chunk in source_map:
        if not chunk:
            continue
        found_all.extend(extractors.extract_all(chunk, source=source))
    # the QR VPA is the strongest identifier from a QR
    for c in media.get("qr", {}).get("codes", []):
        if c.get("kind") == "upi" and c["parsed"].get("vpa"):
            found_all.append(extractors.Found("upi", c["parsed"]["vpa"].lower(), c["data"], 0, 0, 0.98,
                                              source="qr", extra={"payee": c["parsed"].get("payee_name")}))
        if c.get("kind") == "wallet" and c["parsed"].get("address"):
            found_all.append(extractors.Found("wallet", c["parsed"]["address"], c["data"], 0, 0, 0.95,
                                              source="qr", extra={"chain": c["parsed"].get("chain")}))
    # identifiers that come from a decoded QR are tagged qr (drop weaker duplicates)
    qr_upis = {c["parsed"].get("vpa", "").lower() for c in media.get("qr", {}).get("codes", []) if c.get("kind") == "upi"}
    qr_wallets = {c["parsed"].get("address", "") for c in media.get("qr", {}).get("codes", []) if c.get("kind") == "wallet"}
    found_all = [
        f for f in extractors.merge_found(found_all)
        if not (f.type == "upi" and f.source == "image_ocr" and f.value.lower() in qr_upis)
        and not (f.type == "wallet" and f.source == "image_ocr" and f.value in qr_wallets)
    ]

    existing = {(i.type, i.value, i.source) for i in post.identifiers}
    for f in found_all:
        if not f.value:
            continue
        if (f.type, f.value, f.source) in existing:
            continue
        if f.type == "link" and f.value.startswith("https://t.me/"):
            # keep only meaningful links; skip the post's own channel link
            pass
        db.add(Identifier(post_id=post.id, type=f.type, value=f.value, raw=f.raw, source=f.source,
                          confidence=f.confidence, channel=post.channel, timestamp=post.timestamp,
                          extra=f.extra or {}))
        existing.add((f.type, f.value, f.source))

    # --- classification ----------------------------------------------------
    result = prediction if prediction is not None else eng.predict(full_text)
    post.language = (media.get("transcript") or {}).get("language") or detect_language(full_text)
    post.text_hash = hashlib.sha256((post.text or "").strip().lower().encode()).hexdigest()

    identifiers = [
        {"type": f.type, "value": f.value, "source": f.source, "raw": f.raw, "confidence": f.confidence}
        for f in found_all
    ]
    flagged = result["is_illicit"]
    post.status = PostStatus.flagged.value if flagged else PostStatus.ok.value

    alert_id = None
    if flagged and post.alert is None:
        alert = Alert(
            post_id=post.id,
            category=result["category"],
            subcategories=result["explanation"]["financial"],
            risk=result["risk"],
            confidence=result["confidence"],
            explanation={**result["explanation"], "scores": result["scores"]},
            identifier_summary={
                "types": sorted({i["type"] for i in identifiers}),
                "count": len(identifiers),
                "values": [f"{i['type']}:{i['value']}" for i in identifiers][:12],
            },
            status=AlertStatus.new.value,
        )
        db.add(alert)
        db.flush()
        snap = evidence_mod.build_snapshot(
            post_id=post.id, channel=post.channel, text=post.text or "",
            timestamp=post.timestamp.isoformat() if post.timestamp else "",
            permalink=post.permalink, media_path=post.media_path,
            media_sha256=post.media_sha256 or post.audio_hash, identifiers=identifiers,
            category=result["category"], risk=result["risk"], confidence=result["confidence"],
            explanation=result["explanation"],
            extra={"ocr_text": (media.get("ocr") or {}).get("text", ""),
                   "transcript": (media.get("transcript") or {}).get("text", ""),
                   "qr": [c.get("data") for c in (media.get("qr") or {}).get("codes", [])]},
        )
        db.add(Evidence(alert_id=alert.id, payload=snap["payload"], sha256=snap["sha256"],
                        captured_at=utcnow()))
        alert_id = alert.id

    if commit:
        db.commit()
    return ProcessResult(
        post_id=post.id, status=post.status, category=result["category"], risk=result["risk"],
        confidence=result["confidence"], alert_id=alert_id, identifier_count=len(found_all), media=media,
    )


def create_post(
    db: Session,
    *,
    post_id: str | None = None,
    text: str,
    channel: str,
    timestamp: object = None,
    external_id: str | None = None,
    platform: str = "telegram",
    permalink: str | None = None,
    label: str | None = None,
    views: int | None = None,
) -> Post:
    ch = get_or_create_channel(db, channel, platform)
    post = Post(
        **({"id": post_id} if post_id else {}),
        external_id=external_id, platform=platform, channel_id=ch.id, channel=ch.username,
        timestamp=_parse_ts(timestamp), text=text or "", permalink=permalink or _permalink(ch.username, external_id),
        label=label, views=views, text_hash=hashlib.sha256((text or "").strip().lower().encode()).hexdigest(),
    )
    db.add(post)
    db.flush()
    return post


def _permalink(channel: str, external_id: str | None) -> str | None:
    if not external_id:
        return f"https://t.me/{channel}" if channel else None
    return f"https://t.me/{channel}/{external_id}"


def iter_media_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in (IMAGE_EXT | AUDIO_EXT):
            yield p
