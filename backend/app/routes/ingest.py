"""Ingest endpoints: file upload (CSV/JSON/ZIP/media), Telegram links, pipeline."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IngestJob
from app.db.session import get_db, session_scope
from app.jobs import JobContext, create_job, job_dict, run_in_background
from app.pipeline import ingest
from app.pipeline import telegram as tg
from app.schemas import JobOut, PipelineRequest, TelegramIngestRequest
from app.services import refresh_campaigns

router = APIRouter(tags=["ingest"])

# canonical column aliases for the organizers' dataset and generic exports
COLUMN_ALIASES = {
    "post_id": ["post_id", "id", "message_id", "msg_id", "postid"],
    "platform": ["platform", "source", "network"],
    "channel": ["channel", "channel_name", "channel_username", "group", "account", "page", "username"],
    "timestamp": ["timestamp", "date", "datetime", "time", "posted_at", "created_at"],
    "text": ["text", "message", "content", "caption", "body", "post"],
    "image_path": ["image_path", "image", "photo", "media", "attachment", "image_url"],
    "audio_path": ["audio_path", "audio", "voice", "voice_note", "audio_url"],
    "label": ["label", "ground_truth", "class", "category", "gold"],
}


def map_columns(headers: list[str], provided: dict[str, str] | None) -> dict[str, str | None]:
    """Resolve dataset columns -> canonical field names (case/space tolerant)."""
    mapping: dict[str, str | None] = {canon: None for canon in COLUMN_ALIASES}
    if provided:
        for canon, col in provided.items():
            if canon in mapping and col:
                mapping[canon] = col
    norm = {h.strip().lower().replace(" ", "_"): h for h in headers}
    for canon, aliases in COLUMN_ALIASES.items():
        if mapping[canon]:
            continue
        for a in aliases:
            if a in norm:
                mapping[canon] = norm[a]
                break
    return mapping


def _rows_from_file(name: str, data: bytes) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    lower = name.lower()
    if lower.endswith(".json") or lower.endswith(".jsonl") or lower.endswith(".ndjson"):
        payload = json.loads(data.decode("utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("posts") or payload.get("data") or payload.get("items") or [payload]
        return list(payload), None
    text = data.decode("utf-8-sig", errors="replace")
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [dict(r) for r in reader]
    mapping = map_columns(list(reader.fieldnames or []), None)
    return rows, {k: v for k, v in mapping.items() if v}


def ingest_rows(ctx: JobContext, rows: list[dict[str, Any]], *, media_root: Path | None,
                mapping: dict[str, str] | None, do_ocr: bool) -> dict:
    mapping = mapping or map_columns(list(rows[0].keys()) if rows else [], None)
    created, flagged, errors = 0, 0, []
    ctx.set_total(len(rows))
    # Batch the text classifier: one TF-IDF + one embedding pass for the upload.
    from app.ml.engine import engine

    texts = []
    for row in rows:
        col = mapping.get("text")
        texts.append(str(row.get(col) or "").strip() if col else "")
    try:
        predictions = engine().predict_many(texts)
    except Exception:
        predictions = [None] * len(rows)
    with session_scope() as db:
        for row, prediction in zip(rows, predictions):
            def col(name: str) -> str:
                c = mapping.get(name)
                return str(row.get(c) or "").strip() if c else ""

            text = col("text")
            image = col("image_path")
            audio = col("audio_path")
            if not text and not image and not audio:
                ctx.step()
                continue
            media: Path | None = None
            if image:
                cand = (media_root / image) if media_root else Path(image)
                if cand.exists():
                    media = cand
            if media is None and audio:
                cand = (media_root / audio) if media_root else Path(audio)
                if cand.exists():
                    media = cand
            try:
                post = ingest.create_post(
                    db, text=text, channel=col("channel") or "imported",
                    timestamp=col("timestamp") or None, external_id=col("post_id") or None,
                    platform=col("platform") or "imported", label=col("label") or None,
                )
                res = ingest.process_post(db, post, media_path=media, do_ocr=do_ocr, max_ocr_passes=1,
                                          prediction=prediction)
                created += 1
                if res.alert_id:
                    flagged += 1
                ctx.step(f"processed {created}/{len(rows)}")
            except Exception as exc:
                errors.append(f"row {col('post_id')}: {exc}")
                ctx.step()
    refresh_campaigns(session_scope_db(), force=True)
    return {"posts": created, "alerts": flagged, "errors": errors[:20], "error_count": len(errors)}


def session_scope_db() -> Session:
    """Helper that returns a fresh session for post-processing hooks."""
    from app.db.session import SessionLocal

    return SessionLocal()


async def _telegram_job(ctx: JobContext, req: TelegramIngestRequest) -> dict:
    media_dir = settings.UPLOAD_DIR / "telegram" / f"job_{ctx.job_id}"
    msgs, reports, mode = await tg.collect(
        req.targets, limit=req.limit, offset_id=req.offset_id, media_dir=media_dir,
        progress=lambda m: (ctx.log(m), None)[1],
    )
    ctx.set_total(len(msgs))
    created, flagged = 0, 0
    errors: list[str] = []
    from app.ml.engine import engine as get_engine

    text_preds = get_engine().predict_many([m.text or "" for m in msgs]) if msgs else []
    for m, text_pred in zip(msgs, text_preds):
        with session_scope() as db:
            try:
                post = ingest.create_post(
                    db, text=m.text, channel=m.channel, timestamp=m.timestamp, external_id=m.external_id,
                    permalink=m.permalink, views=m.views,
                )
                res = ingest.process_post(
                    db, post, media_path=Path(m.media_path) if m.media_path else None,
                    prediction=None if m.media_path else text_pred,
                    do_ocr=req.do_ocr, max_ocr_passes=req.ocr_passes,
                )
                created += 1
                if res.alert_id:
                    flagged += 1
            except Exception as exc:
                errors.append(f"{m.external_id}: {exc}")
        ctx.step(f"analyzed {created}/{len(msgs)}")
    refresh_campaigns(session_scope_db(), force=True)
    return {
        "mode": mode,
        "channels": [r.__dict__ for r in reports],
        "posts": created, "alerts": flagged, "errors": errors[:20],
        "resume": {"offset_id": reports[-1].next_offset if reports else None},
    }


@router.post("/ingest/telegram", response_model=JobOut)
async def ingest_telegram(
    req: TelegramIngestRequest, background: BackgroundTasks, db: Session = Depends(get_db)
) -> dict:
    """Analyse public t.me links / @usernames: verify public, fetch last N, run pipeline."""
    from app.core.scope import check_public_target

    decisions = [check_public_target(t) for t in req.targets]
    rejected = [d.reason for d in decisions if not d.allowed]
    if rejected and len(rejected) == len(decisions):
        raise HTTPException(400, "; ".join(rejected))
    job_id = create_job("telegram", req.model_dump(), total=0)
    with session_scope() as sdb:
        job = sdb.get(IngestJob, job_id)
        job.message = "collecting public channel messages" if tg.live_available() else "demo mode (no Telegram session)"
        job_dict_out = job_dict(job)

    async def _runner() -> None:
        ctx = JobContext(job_id)
        try:
            result = await _telegram_job(ctx, req)
            result["rejected"] = rejected
            ctx.finish(result, message=f"{result['posts']} posts analysed, {result['alerts']} alerts")
        except Exception as exc:  # pragma: no cover
            ctx.fail(str(exc))

    background.add_task(_run_async, _runner)
    return job_dict_out


def _run_async(coro_factory) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro_factory())
    finally:
        loop.close()


@router.post("/ingest/file", response_model=JobOut)
async def ingest_file(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON column mapping {canonical: column}"),
    do_ocr: bool = Form(True),
    db: Session = Depends(get_db),
) -> dict:
    """Upload CSV/JSON (optionally zipped with images/audio) of posts."""
    name = file.filename or "upload.csv"
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    job_dir = settings.UPLOAD_DIR / "jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    parsed_mapping = json.loads(mapping) if mapping else None
    rows: list[dict[str, Any]] = []
    media_root: Path | None = None
    if name.lower().endswith(".zip"):
        extract_dir = job_dir / f"zip_{int(asyncio.get_event_loop().time() * 1000)}"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    continue
                zf.extract(member, extract_dir)
        candidates = list(extract_dir.rglob("*.json")) + list(extract_dir.rglob("*.csv"))
        if not candidates:
            raise HTTPException(400, "zip contains no .json/.csv manifest")
        manifest = candidates[0]
        rows, parsed_mapping = _rows_from_file(manifest.name, manifest.read_bytes())
        media_root = manifest.parent
    else:
        rows, parsed_mapping = _rows_from_file(name, data)
    if not rows:
        raise HTTPException(400, "no rows found")
    job_id = create_job("csv", {"file": name, "mapping": parsed_mapping}, total=len(rows))
    background.add_task(
        run_in_background,
        lambda ctx: ingest_rows(ctx, rows, media_root=media_root, mapping=parsed_mapping, do_ocr=do_ocr),
        job_id,
        len(rows),
    )
    with session_scope() as sdb:
        return job_dict(sdb.get(IngestJob, job_id))


@router.post("/ingest/media", response_model=JobOut)
async def ingest_media(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    text: str = Form(""),
    channel: str = Form("uploads"),
    do_ocr: bool = Form(True),
) -> dict:
    """Upload a single image / QR / voice note for analysis and storage."""
    name = file.filename or "upload.bin"
    suffix = Path(name).suffix.lower()
    if suffix not in (ingest.IMAGE_EXT | ingest.AUDIO_EXT):
        raise HTTPException(400, f"unsupported media type {suffix}")
    dest_dir = settings.UPLOAD_DIR / "incoming"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{int(asyncio.get_event_loop().time() * 1000)}_{name}"
    dest.write_bytes(await file.read())
    job_id = create_job("media", {"file": name, "channel": channel}, total=1)

    def _job(ctx: JobContext) -> dict:
        with session_scope() as db:
            post = ingest.create_post(db, text=text, channel=channel, platform="upload")
            res = ingest.process_post(db, post, media_path=dest, do_ocr=do_ocr)
            ctx.step("analyzed")
            return {"post_id": post.id, "alert_id": res.alert_id, "category": res.category,
                    "risk": res.risk, "confidence": res.confidence}

    background.add_task(run_in_background, _job, job_id, 1)
    with session_scope() as sdb:
        return job_dict(sdb.get(IngestJob, job_id))


@router.post("/pipeline/run", response_model=JobOut)
async def pipeline_run(req: PipelineRequest, background: BackgroundTasks) -> dict:
    """Re-run classification/identifiers over posts (e.g. after lexicon edits)."""
    job_id = create_job("pipeline", req.model_dump(), total=0)

    def _job(ctx: JobContext) -> dict:
        from app.ml.engine import reset_engine

        reset_engine()
        with session_scope() as db:
            q = select(ingest.Post)
            if req.post_ids:
                q = q.where(ingest.Post.id.in_(req.post_ids))
            posts = db.execute(q).scalars().all()
            ctx.set_total(len(posts))
            flagged = 0
            for p in posts:
                res = ingest.process_post(db, p, media_path=None)
                if res.alert_id:
                    flagged += 1
                ctx.step()
        refresh_campaigns(session_scope_db(), force=True)
        return {"posts": len(posts), "alerts": flagged}

    background.add_task(run_in_background, _job, job_id, 0)
    with session_scope() as sdb:
        return job_dict(sdb.get(IngestJob, job_id))


@router.get("/ingest/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(IngestJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job_dict(job)


@router.get("/ingest/jobs")
def list_jobs(db: Session = Depends(get_db), limit: int = 20) -> dict:
    jobs = db.execute(select(IngestJob).order_by(IngestJob.started_at.desc()).limit(limit)).scalars().all()
    return {"items": [job_dict(j) for j in jobs]}
