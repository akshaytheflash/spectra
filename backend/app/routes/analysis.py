"""Classification + single-item analysis endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.ml import extractors
from app.ml.engine import engine
from app.ml.fusion import fuse
from app.ml.llm import scanner
from app.pipeline import speech, vision
from app.schemas import ClassifyRequest, ClassifyResponse

router = APIRouter(tags=["analysis"])


def classify_payload(text: str, *, use_llm: bool = True) -> dict:
    result = engine().predict(text)
    verdict = scanner().scan(text) if (use_llm and settings.llm_ready and (text or "").strip()) else None
    result = fuse(result, verdict, text=text)
    found = extractors.extract_all(text, source="text")
    result["identifiers"] = [f.as_dict() for f in found]
    return result


@router.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest) -> dict:
    """Classify a text post and return category, confidence and explanation."""
    return classify_payload(req.text, use_llm=req.use_llm)


@router.post("/analyze/image")
async def analyze_image_endpoint(
    file: UploadFile = File(..., description="Poster, screenshot, ID/card photo or QR image"),
    run_ocr: bool = Form(True),
) -> dict:
    """OCR + QR + classification for a single uploaded image."""
    suffix = Path(file.filename or "upload.png").suffix.lower() or ".png"
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.UPLOAD_DIR) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        res = vision.analyze_image(tmp, do_ocr=run_ocr)
        parts = [res.text]
        for q in res.qr_codes:
            if q.kind == "upi":
                parts.append(f"UPI {q.parsed.get('vpa', '')} payee {q.parsed.get('payee_name', '')}")
            elif q.kind == "wallet":
                parts.append(f"wallet {q.parsed.get('address', '')}")
        joined = "\n".join(p for p in parts if p)
        result = classify_payload(joined)
        idents = result["identifiers"]
        for q in res.qr_codes:
            if q.kind in ("upi", "wallet"):
                val = q.parsed.get("vpa") or q.parsed.get("address")
                idents.append({"type": q.kind, "value": val, "raw": q.data, "source": "qr", "confidence": 0.97})
        return {
            "ocr": res.as_dict(),
            "classification": result,
            "identifiers": idents,
            "text": joined,
        }
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/analyze/audio")
async def analyze_audio_endpoint(file: UploadFile = File(...)) -> dict:
    """Transcribe a voice note (Hindi/Hinglish/English) and classify it."""
    suffix = Path(file.filename or "audio.ogg").suffix.lower() or ".ogg"
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    tmpdir = Path(tempfile.mkdtemp(dir=settings.UPLOAD_DIR))
    src = tmpdir / f"upload{suffix}"
    src.write_bytes(data)
    try:
        tr = speech.transcribe_file(src, workdir=tmpdir)
        result = classify_payload(tr.text)
        identifiers = result["identifiers"]
        for ident in identifiers:
            ident["source"] = "voice"
        return {
            "transcript": tr.as_dict(),
            "classification": result,
            "identifiers": identifiers,
        }
    finally:
        for p in tmpdir.iterdir():
            p.unlink(missing_ok=True)
        tmpdir.rmdir()
