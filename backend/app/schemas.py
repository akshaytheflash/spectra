"""Pydantic request/response models for the SPECTRA API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- classify
class ClassifyRequest(BaseModel):
    text: str = Field(..., max_length=20000, description="Post text to classify")
    explain_markup: bool = True


class ScoreOut(BaseModel):
    label: str
    score: float


class ClassifyResponse(BaseModel):
    label: str
    category: str
    subcategory: str | None = None
    confidence: float
    risk: str
    is_illicit: bool
    scores: dict[str, float]
    explanation: dict[str, Any]
    identifiers: list[dict[str, Any]] = []


# --------------------------------------------------------------------------- ingest
class TelegramIngestRequest(BaseModel):
    targets: list[str] = Field(..., min_length=1, max_length=10,
                               description="Public t.me links, @usernames or t.me/channel/123 links")
    limit: int = Field(50, ge=1, le=500)
    offset_id: int | None = Field(None, description="Resume cursor (offset_id of the last processed message)")
    run_pipeline: bool = True
    do_ocr: bool = True
    ocr_passes: int = Field(2, ge=1, le=3, description="OCR pre-processing passes per image (2 = quality/speed balance)")


class PipelineRequest(BaseModel):
    post_ids: list[str] | None = None
    rechunk: bool = True


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    total: int
    done: int
    message: str = ""
    result: dict[str, Any] = {}
    started_at: datetime | None = None
    finished_at: datetime | None = None


class UploadResponse(BaseModel):
    job: JobOut
    files: list[str]


# --------------------------------------------------------------------------- alerts
class AlertIdentifier(BaseModel):
    type: str
    value: str
    source: str
    confidence: float = 0.0


class AlertListItem(BaseModel):
    id: str
    post_id: str
    category: str
    risk: str
    confidence: float
    status: str
    channel: str
    timestamp: datetime
    text: str
    source_types: list[str] = []
    identifier_types: list[str] = []
    cluster_id: str | None = None
    media_type: str | None = None
    has_evidence: bool = True


class StatusUpdate(BaseModel):
    status: Literal["new", "under_review", "confirmed", "dismissed"]
    notes: str | None = None
    actor: str = "analyst"


class AlertDetail(BaseModel):
    id: str
    post: dict[str, Any]
    category: str
    risk: str
    confidence: float
    status: str
    explanation: dict[str, Any]
    subcategories: dict[str, Any] = {}
    identifiers: list[dict[str, Any]] = []
    evidence: dict[str, Any]
    audit: list[dict[str, Any]] = []
    cluster_id: str | None = None
    analyst_notes: list[dict[str, Any]] = []
    related: dict[str, Any] = {}


# --------------------------------------------------------------------------- search / ids
class SearchHit(BaseModel):
    post_id: str
    alert_id: str | None = None
    channel: str
    timestamp: datetime
    text: str
    category: str | None = None
    label: str | None = None
    score: float = 0.0
    matched: list[str] = []


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    hits: list[SearchHit]


class IdentifierRow(BaseModel):
    type: str
    value: str
    post_count: int
    channel_count: int
    channels: list[str]
    categories: list[str]
    first_seen: datetime | None
    last_seen: datetime | None
    risk_score: float
    alert_count: int = 0
    campaign_count: int = 0


class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    meta: dict[str, Any]


class CampaignOut(BaseModel):
    id: str
    title: str
    category: str
    post_count: int
    channels: list[str]
    shared_identifiers: list[str]
    time_span: dict[str, str]
    posting_frequency: float
    similarity: float
    members: list[dict[str, Any]] = []


# --------------------------------------------------------------------------- lexicon
class LexiconTermIn(BaseModel):
    category: str
    term: str
    kind: Literal["terms", "slang", "devanagari", "emojis", "intent"] = "slang"
    weight: float = 1.0
    language: str = "any"


class FeedbackIn(BaseModel):
    alert_id: str
    accepted: bool
    note: str | None = None
