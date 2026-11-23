"""Relational schema for SPECTRA.

The same metadata is used for PostgreSQL/Supabase (production) and SQLite
(local demo).  PostgreSQL-only features -- ``pg_trgm`` indexes, JSONB columns,
Row Level Security -- are applied by ``backend/migrations/*.sql`` and are
documented in ``docs/SCHEMA.md``.

JSON payloads are declared with ``JSON().with_variant(JSONB, "postgresql")``
so that evidence/explanation structures are real ``jsonb`` on Supabase while the
SQLite demo stays dependency free.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:  # pragma: no cover - optional on SQLite-only installs
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
except Exception:  # pragma: no cover
    JSONB = None  # type: ignore[assignment]
    PGUUID = None  # type: ignore[assignment]

JSONType = JSON().with_variant(JSONB, "postgresql") if JSONB is not None else JSON


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base."""


class PostStatus(str, enum.Enum):
    ok = "ok"
    flagged = "flagged"


class AlertStatus(str, enum.Enum):
    new = "new"
    under_review = "under_review"
    confirmed = "confirmed"
    dismissed = "dismissed"


class Channel(Base):
    """A monitored public Telegram channel or an imported social account."""

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(32), default="telegram")
    username: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    subscriber_count: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    posts: Mapped[list["Post"]] = relationship(back_populates="channel_obj")


class Post(Base):
    """A collected message (text, image, QR or voice note)."""

    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="telegram")
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"), index=True)
    channel: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    media_path: Mapped[str | None] = mapped_column(String(512))
    media_type: Mapped[str | None] = mapped_column(String(32))  # image|audio|qr|video
    media_sha256: Mapped[str | None] = mapped_column(String(64))
    permalink: Mapped[str | None] = mapped_column(String(512))
    views: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(32))  # ground truth (sample data)
    status: Mapped[str] = mapped_column(String(16), default=PostStatus.ok.value)
    ocr: Mapped[dict] = mapped_column(JSONType, default=dict)
    transcript: Mapped[dict] = mapped_column(JSONType, default=dict)
    qr: Mapped[dict] = mapped_column(JSONType, default=dict)
    text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    image_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    audio_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel_obj: Mapped[Channel | None] = relationship(back_populates="posts")
    identifiers: Mapped[list["Identifier"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    alert: Mapped["Alert | None"] = relationship(back_populates="post", uselist=False)


class Identifier(Base):
    """A normalized seller identifier extracted from a post."""

    __tablename__ = "identifiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[str] = mapped_column(String(256), index=True)
    raw: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="text")  # text|image_ocr|qr|voice
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    channel: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    extra: Mapped[dict] = mapped_column(JSONType, default=dict)

    post: Mapped[Post] = relationship(back_populates="identifiers")

    __table_args__ = (
        Index("ix_identifier_value_type", "value", "type"),
        UniqueConstraint("post_id", "type", "value", "source", name="uq_identifier_post"),
    )


class Alert(Base):
    """A flagged post plus workflow state."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), unique=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    subcategories: Mapped[dict] = mapped_column(JSONType, default=dict)
    risk: Mapped[str] = mapped_column(String(16), index=True)  # HIGH|MEDIUM|LOW
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[dict] = mapped_column(JSONType, default=dict)
    identifier_summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    cluster_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.new.value, index=True)
    analyst_notes: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[Post] = relationship(back_populates="alert")
    evidence: Mapped["Evidence | None"] = relationship(back_populates="alert", uselist=False)
    audit: Mapped[list["AuditLog"]] = relationship(back_populates="alert")


class Evidence(Base):
    """Immutable evidence snapshot (tamper-evident via SHA-256)."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), unique=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    alert: Mapped[Alert] = relationship(back_populates="evidence")


class AuditLog(Base):
    """Append-only audit trail of analyst actions."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="analyst")
    action: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    alert: Mapped[Alert | None] = relationship(back_populates="audit")


class Campaign(Base):
    """A cluster of coordinated (near-duplicate) ads."""

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(32), index=True)
    post_ids: Mapped[list] = mapped_column(JSONType, default=list)
    channels: Mapped[list] = mapped_column(JSONType, default=list)
    shared_identifiers: Mapped[list] = mapped_column(JSONType, default=list)
    time_span: Mapped[dict] = mapped_column(JSONType, default=dict)
    posting_frequency: Mapped[float] = mapped_column(Float, default=0.0)  # posts/day
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    signature: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestJob(Base):
    """Progress tracking for long running ingest/pipeline jobs."""

    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32))  # telegram|csv|media|pipeline
    status: Mapped[str] = mapped_column(String(16), default="running")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LexiconTerm(Base):
    """Analyst-editable lexicon overlay (lexicon editor UI)."""

    __tablename__ = "lexicon_terms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    category: Mapped[str] = mapped_column(String(48), index=True)
    term: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(24), default="terms")  # terms|slang|emojis|intent
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    language: Mapped[str] = mapped_column(String(16), default="any")
    created_by: Mapped[str] = mapped_column(String(128), default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("category", "term", "kind", name="uq_lexicon_term"),)


class ModelRun(Base):
    """Persisted evaluation metrics shown on the /metrics page."""

    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(64))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    confusion: Mapped[list] = mapped_column(JSONType, default=list)
    labels: Mapped[list] = mapped_column(JSONType, default=list)
    train_size: Mapped[int] = mapped_column(Integer, default=0)
    test_size: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
