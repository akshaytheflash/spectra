"""Lexicon editor + active-learning feedback endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ROOT, settings
from app.db.models import Alert, AuditLog, LexiconTerm
from app.db.session import get_db
from app.ml import lexicon as lexmod
from app.ml.engine import reset_engine
from app.schemas import FeedbackIn, LexiconTermIn

router = APIRouter(tags=["lexicon"])

FEEDBACK_PATH = settings.DATA_DIR / "generated" / "analyst_feedback.jsonl"


def _overlay_rows(db: Session) -> list[dict]:
    return [
        {"category": t.category, "term": t.term, "kind": t.kind, "weight": t.weight}
        for t in db.execute(select(LexiconTerm)).scalars()
    ]


def _reload(db: Session) -> None:
    lexmod.set_overlay(_overlay_rows(db))
    reset_engine()


@router.get("/lexicon")
def get_lexicon(db: Session = Depends(get_db)) -> dict:
    with open(lexmod.LEXICON_PATH, encoding="utf-8") as fh:
        base = json.load(fh)
    overlay = [
        {"id": t.id, "category": t.category, "term": t.term, "kind": t.kind, "weight": t.weight,
         "language": t.language, "created_by": t.created_by, "created_at": t.created_at}
        for t in db.execute(select(LexiconTerm)).scalars()
    ]
    return {"version": base.get("version"), "categories": base.get("categories", {}), "overlay": overlay}


@router.post("/lexicon/terms")
def add_term(body: LexiconTermIn, db: Session = Depends(get_db)) -> dict:
    """Add a slang/emoji/term without redeploying; reloads the in-process lexicon."""
    if body.category not in lexmod.CATEGORIES:
        raise HTTPException(400, f"unknown category {body.category}")
    term = body.term.strip()
    if not term:
        raise HTTPException(400, "empty term")
    existing = db.execute(
        select(LexiconTerm).where(LexiconTerm.category == body.category, LexiconTerm.term == term,
                                  LexiconTerm.kind == body.kind)
    ).scalar_one_or_none()
    if existing is None:
        existing = LexiconTerm(category=body.category, term=term, kind=body.kind, weight=body.weight,
                               language=body.language)
        db.add(existing)
        db.commit()
    _reload(db)
    return {"ok": True, "id": existing.id, "reloaded": True}


@router.delete("/lexicon/terms/{term_id}")
def delete_term(term_id: str, db: Session = Depends(get_db)) -> dict:
    t = db.get(LexiconTerm, term_id)
    if t is None:
        raise HTTPException(404, "term not found")
    db.delete(t)
    db.commit()
    _reload(db)
    return {"ok": True}


@router.post("/lexicon/reload")
def reload_lexicon(db: Session = Depends(get_db)) -> dict:
    _reload(db)
    return {"ok": True, "overlay_terms": len(_overlay_rows(db))}


@router.post("/feedback")
def feedback(body: FeedbackIn, db: Session = Depends(get_db)) -> dict:
    """Active learning: analyst decisions become labelled training rows.

    Confirmed alerts are saved as positive examples of their predicted
    category; dismissed alerts as hard negatives. ``scripts/retrain.py`` folds
    this file into the next training run.
    """
    alert = db.get(Alert, body.alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "alert_id": alert.id,
        "category": alert.category,
        "label": alert.category if body.accepted else "benign",
        "accepted": body.accepted,
        "note": body.note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    db.add(AuditLog(alert_id=alert.id, actor="analyst", action="active_learning",
                    notes=("confirmed" if body.accepted else "dismissed") + (f": {body.note}" if body.note else "")))
    db.commit()
    return {"ok": True, "saved": str(FEEDBACK_PATH.relative_to(ROOT)) if FEEDBACK_PATH.is_relative_to(ROOT) else str(FEEDBACK_PATH)}
