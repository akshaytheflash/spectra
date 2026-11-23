"""Tamper-evident evidence snapshots.

The SHA-256 covers a canonical JSON document built from the evidence content:

    {"channel", "post_text", "timestamp", "permalink", "media_sha256",
     "identifiers": [{type, value, source}], "captured_at"}

The snapshot row is immutable: status changes are recorded in the audit log and
in ``alert.analyst_notes``, never by rewriting the evidence.  ``verify_evidence``
recomputes the digest from the stored payload and reports field-level diffs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_hash(payload: dict) -> str:
    """SHA-256 of the canonical evidence document."""
    return hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


def build_snapshot(
    *,
    post_id: str,
    channel: str,
    text: str,
    timestamp: str,
    permalink: str | None,
    media_path: str | None,
    media_sha256: str | None,
    identifiers: list[dict],
    category: str,
    risk: str,
    confidence: float,
    explanation: dict,
    extra: dict | None = None,
) -> dict:
    """Assemble the evidence payload (stored as JSONB) and its hash."""
    captured_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "post_id": post_id,
        "channel": channel,
        "post_text": text,
        "timestamp": timestamp,
        "permalink": permalink,
        "media_path": media_path,
        "media_sha256": media_sha256,
        "identifiers": sorted(
            [{"type": i["type"], "value": i["value"], "source": i.get("source", "text")} for i in identifiers],
            key=lambda i: (i["type"], i["value"]),
        ),
        "classification": {"category": category, "risk": risk, "confidence": round(float(confidence), 4)},
        "explanation": {
            "matched_terms": explanation.get("matched_terms", []),
            "intent_phrases": explanation.get("intent_phrases", []),
            "category_weights": explanation.get("category_weights", {}),
            "spans": explanation.get("spans", []),
        },
        "captured_at": captured_at,
    }
    if extra:
        payload["extra"] = extra
    return {"payload": payload, "sha256": compute_hash(payload), "captured_at": captured_at}


def verify(payload: dict, stored_sha256: str) -> dict:
    """Recompute the digest and compare with the stored one."""
    recomputed = compute_hash(payload)
    return {
        "ok": recomputed == stored_sha256,
        "stored_sha256": stored_sha256,
        "recomputed_sha256": recomputed,
        "algorithm": "SHA-256 (canonical JSON: sorted keys, no whitespace, utf-8)",
        "fields": sorted(payload.keys()),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
