"""Universal image pipeline: OCR (multi-pass) + QR/barcode decoding.

Design goals
  * Robust to posters, chat screenshots, memes, ID/card photos, passbook photos,
    low quality / rotated / noisy images and mixed Hindi + English.
  * easyocr (en+hi) primary, pytesseract fallback.
  * Multiple preprocessing passes; results merged with token-level voting, which
    is more reliable than picking one "best" pass.
  * QR decoded on every image with progressive upscaling retries and crops,
    pyzbar + OpenCV QRCodeDetector; UPI payloads parsed into VPA/payee/amount.

Artifacts persisted for the evidence view:
  ``preprocessed`` PNGs and OCR bounding boxes in ORIGINAL image coordinates.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from app.core.config import ROOT, settings

# --------------------------------------------------------------------------- models
_easy: Any = None
_easy_failed = False
_easy_patched = False
# Images are downscaled to this long side before OCR: easyocr's detector runs up
# to 3x internal upscaling, so processing 1280 px costs ~3x more than 640 px for
# the same text. Set SPECTRA_OCR_MAX_SIDE=1280 for maximum accuracy on big hosts.
OCR_MAX_SIDE = int(__import__("os").environ.get("SPECTRA_OCR_MAX_SIDE", "720"))
OCR_CANVAS = int(__import__("os").environ.get("SPECTRA_OCR_CANVAS", "640"))


def _patch_easyocr_boxes() -> bool:
    """Make easyocr's CRAFT coordinate scaling NumPy-2 safe.

    easyocr 1.7 calls ``adjustResultCoordinates`` without ``poly=True`` and then
    mutates the returned ndarray in place; under NumPy >= 1.24/CV 5 boxes come
    back as ragged lists, which raises inside easyocr. We recompute the
    rectangles/polygons as float arrays (same maths, deterministic).
    """
    global _easy_patched
    if _easy_patched:
        return True
    try:
        from easyocr import craft_utils, detection

        # getDetBoxes returns (boxes, polys, mapper); with poly=False the polys are
        # rectangles of shape (4, 2) that easyocr wrongly upsamples 2x afterwards.
        # Re-fit the polygon from the (already scaled) rectangle corners instead.
        _orig_poly = craft_utils.getDetBoxes

        def _poly_boxes(textmap, linkmap, text_threshold, link_threshold, low_text, poly=False,
                        estimate_num_chars=False):
            boxes, _polys, mapper = _orig_poly(
                textmap, linkmap, text_threshold, link_threshold, low_text, poly, estimate_num_chars
            )
            return boxes, boxes, mapper

        craft_utils.getDetBoxes = _poly_boxes
        detection.getDetBoxes = _poly_boxes

        def _adjust(polys, ratio_w, ratio_h, ratio_net=2):
            out = []
            for p in polys:
                if p is None:
                    out.append(None)
                    continue
                a = np.asarray(p, dtype=np.float64).reshape(-1, 2)
                a = a * (ratio_w * ratio_net, ratio_h * ratio_net)
                out.append(a)
            return out

        detection.adjustResultCoordinates = _adjust
        _easy_patched = True
        return True
    except Exception:
        return False


def _easyocr_reader() -> Any:
    global _easy, _easy_failed
    if _easy_failed:
        return None
    if _easy is None:
        try:
            import torch

            torch.set_num_threads(int(__import__("os").environ.get("SPECTRA_TORCH_THREADS", "1")))
            cv2.setNumThreads(int(__import__("os").environ.get("SPECTRA_CV_THREADS", "1")))
            _patch_easyocr_boxes()
            import easyocr

            langs = [l.strip() for l in settings.OCR_LANGS.split(",") if l.strip()]
            _easy = easyocr.Reader(
                langs,
                gpu=False,
                model_storage_directory=str(ROOT / ".models" / "easyocr"),
                user_network_directory=str(ROOT / ".models" / "easyocr" / "user_network"),
                verbose=False,
            )
        except Exception:
            _easy_failed = True
            return None
    return _easy


@dataclass
class TextBox:
    text: str
    confidence: float
    x: int
    y: int
    w: int
    h: int
    engine: str = "easyocr"
    pass_name: str = ""


@dataclass
class QRResult:
    data: str
    kind: str  # upi|wallet|url|text
    corners: list[list[int]] = field(default_factory=list)
    parsed: dict = field(default_factory=dict)
    engine: str = "pyzbar"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImageResult:
    text: str = ""
    boxes: list[TextBox] = field(default_factory=list)
    qr_codes: list[QRResult] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)
    preprocessed_paths: list[str] = field(default_factory=list)
    image_sha256: str = ""
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "boxes": [asdict(b) for b in self.boxes],
            "qr_codes": [asdict(q) for q in self.qr_codes],
            "languages": self.languages,
            "passes": self.passes,
            "preprocessed_paths": self.preprocessed_paths,
            "image_sha256": self.image_sha256,
            "width": self.width,
            "height": self.height,
        }


# --------------------------------------------------------------------------- preprocessing
def _resize(img: np.ndarray, target_min: int = 640, cap: int | None = None) -> np.ndarray:
    cap = cap or OCR_MAX_SIDE
    """Upscale small images for OCR and cap large ones to bound memory."""
    h, w = img.shape[:2]
    scale = max(1.0, target_min / max(min(h, w), 1))
    if scale > 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if max(img.shape[:2]) > cap:
        f = cap / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


def _denoise(gray: np.ndarray) -> np.ndarray:
    """Edge-preserving denoise on a single channel (cheap median + NL-means)."""
    return cv2.fastNlMeansDenoising(cv2.medianBlur(gray, 3), None, 7, 7, 21)


def _deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate skew with min-area rectangles over binarised foreground."""
    inv = cv2.bitwise_not(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1])
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 500:
        return gray, 0.0
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) > 30:  # too ambiguous, skip
        return gray, 0.0
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), angle


def preprocess_passes(img: np.ndarray) -> dict[str, np.ndarray]:
    """Build several complementary views of the image."""
    img = _resize(img, cap=OCR_MAX_SIDE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    den = _denoise(gray)
    desk, angle = _deskew(den)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(desk)
    adaptive = cv2.adaptiveThreshold(
        cv2.medianBlur(clahe, 3), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(clahe, -1, kernel)
    # OCR views are single-channel to keep memory bounded on small hosts.
    return {
        "original": img,
        "color": cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
        "adaptive": adaptive,
        "otsu": otsu,
        "sharp": sharp,
        "_angle": np.array([angle]),
    }


# --------------------------------------------------------------------------- OCR
_WORD_RE = re.compile(r"[A-Za-z0-9+@._\-]{2,}")


def _ocr_easyocr(views: dict[str, np.ndarray], passes: tuple[str, ...] = ("color", "adaptive", "sharp")) -> list[TextBox]:
    reader = _easyocr_reader()
    if reader is None:
        return []
    boxes: list[TextBox] = []
    # Adaptive pass catches dark posters; colour pass catches screenshots.
    for name in passes:
        if len(passes) == 1:
            name = next(iter(passes))
        view = views[name]
        try:
            res = reader.readtext(
                view, detail=1, paragraph=False, width_ths=0.5, mag_ratio=1.0,
                canvas_size=OCR_CANVAS, decoder="greedy",
            )
        except Exception:
            _patch_easyocr_boxes()
            try:
                res = reader.readtext(view, detail=1, paragraph=False, mag_ratio=1.0,
                                      canvas_size=OCR_CANVAS, decoder="greedy")
            except Exception:
                continue
        for bbox, text, conf in res:
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            boxes.append(
                TextBox(
                    text=text,
                    confidence=float(min(max(conf, 0.0), 1.0)),
                    x=int(min(xs)), y=int(min(ys)),
                    w=int(max(xs) - min(xs)), h=int(max(ys) - min(ys)),
                    engine="easyocr", pass_name=name,
                )
            )
    return boxes


def _ocr_tesseract(views: dict[str, np.ndarray]) -> list[TextBox]:
    try:
        import pytesseract

        lang = "eng+hin"
    except Exception:
        return []
    out: list[TextBox] = []
    for name in ("otsu", "sharp"):
        view = views[name]
        try:
            data = pytesseract.image_to_data(view, lang=lang, output_type=pytesseract.Output.DICT)
        except Exception:
            try:
                data = pytesseract.image_to_data(view, lang="eng", output_type=pytesseract.Output.DICT)
            except Exception:
                return out
        for i, txt in enumerate(data.get("text", [])):
            txt = (txt or "").strip()
            try:
                conf = float(data["conf"][i])
            except (KeyError, ValueError, TypeError):
                conf = -1
            if not txt or conf < 30:
                continue
            out.append(
                TextBox(
                    text=txt, confidence=conf / 100.0,
                    x=int(data["left"][i]), y=int(data["top"][i]),
                    w=int(data["width"][i]), h=int(data["height"][i]),
                    engine="tesseract", pass_name=name,
                )
            )
    return out


def _merge_boxes(boxes: list[TextBox]) -> list[TextBox]:
    """Merge multi-pass results: keep one box per text+location, prefer highest conf."""
    best: dict[tuple[str, int, int], TextBox] = {}
    for b in boxes:
        key = (b.text.lower(), b.x // 40, b.y // 40)
        cur = best.get(key)
        if cur is None or b.confidence > cur.confidence:
            best[key] = b
    merged = sorted(best.values(), key=lambda b: (b.y, b.x))
    # A second vote: if the same text appears in >=2 passes, boost confidence.
    from collections import Counter

    counts = Counter(b.text.lower() for b in boxes)
    for b in merged:
        if counts[b.text.lower()] >= 2:
            b.confidence = min(0.99, b.confidence + 0.08)
    return merged


def recycle_identifier_boxes(detected: list[TextBox], seen: set[str], raw_gray: np.ndarray) -> list[TextBox]:
    """Second recognizer pass on upscaled crops around phone/UPI-like tokens.

    Detection frequently splits a phone number or UPI into pieces ("fskart7",
    "438@fakebank"); we re-OCR the joined horizontal neighbourhood at 3x so the
    identifier is recovered cleanly.  This is much cheaper than a third full pass.
    """
    pat = re.compile(r"(\d{3,}|@[a-z]|UPI|whatsapp|telegram|t\.me|call)", re.I)
    out: list[TextBox] = []
    for i, b in enumerate(detected):
        token = b.text.strip()
        if not pat.search(token) or token.lower() in seen:
            continue
        # merge with the nearest right-hand neighbour on the same line
        neighbours = [
            n for n in detected
            if n is not b and abs(n.y - b.y) <= max(n.h, b.h) * 0.8 and 0 <= n.x - (b.x + b.w) <= b.h * 3
        ]
        right = min(neighbours, key=lambda n: n.x) if neighbours else None
        x0 = b.x
        x1 = (right.x + right.w) if right else (b.x + b.w)
        slack = max(4, b.h // 3)
        y0, y1 = max(0, b.y - slack), min(raw_gray.shape[0], b.y + b.h + slack)
        x0, x1 = max(0, x0 - slack), min(raw_gray.shape[1], x1 + slack)
        crop = raw_gray[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        if crop.shape[1] > crop.shape[0] * 7:  # unusually wide -> scan the full line instead
            row = [n for n in detected if n is not b and abs(n.y - b.y) <= max(n.h, b.h)]
            y0 = max(0, min(n.y for n in row) - 8)
            y1 = min(raw_gray.shape[0], max(n.y + n.h for n in row) + 8)
            x0 = max(0, min(n.x for n in row) - 8)
            x1 = min(raw_gray.shape[1], max(n.x + n.w for n in row) + 8)
            crop = raw_gray[y0:y1, x0:x1]
            if crop.size == 0 or crop.shape[1] > crop.shape[0] * 10:
                continue
        scale = max(1.0, 90.0 / max(crop.shape[0], 1))
        crop = cv2.resize(crop, None, fx=min(scale, 4.0), fy=min(scale, 4.0), interpolation=cv2.INTER_CUBIC)
        pad = cv2.copyMakeBorder(crop, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=(255 if raw_gray.mean() > 127 else 0))
        boxes = _ocr_easyocr({"color": pad, "adaptive": pad, "sharp": pad}, ("color",))
        for nb in boxes:
            nb.x, nb.y, nb.w, nb.h = x0, y0, x1 - x0, y1 - y0
            nb.pass_name = "recycle"
        out.extend(boxes)
    return out


def _join_boxes(boxes: list[TextBox]) -> str:
    """Reconstruct reading order text from boxes (line grouping by y)."""
    if not boxes:
        return ""
    rows: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: (b.y, b.x)):
        for row in rows:
            if abs(row[0].y - b.y) <= max(12, row[0].h * 0.6):
                row.append(b)
                break
        else:
            rows.append([b])
    lines = [" ".join(b.text for b in sorted(row, key=lambda b: b.x)) for row in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------- QR
def _detect_lang(text: str) -> list[str]:
    langs = set()
    if re.search(r"[\u0900-\u097F]", text):
        langs.add("hi")
    if re.search(r"[A-Za-z]{2,}", text):
        langs.add("en")
    return sorted(langs)


def parse_qr_payload(data: str) -> tuple[str, dict]:
    """Classify a QR payload and parse UPI / wallet / URL structures."""
    low = data.strip().lower()
    if low.startswith("upi://"):
        parsed = urlparse(data)
        q = {k: unquote(v[0]) for k, v in parse_qs(parsed.query).items()}
        vpa = q.get("pa", "")
        return "upi", {
            "vpa": vpa,
            "payee_name": q.get("pn", ""),
            "amount": q.get("am", ""),
            "currency": q.get("cu", "INR"),
            "txn_note": q.get("tn", ""),
            "merchant_code": q.get("mc", ""),
        }
    if re.match(r"^(bitcoin|btc|ethereum|tron|trx|tether|usdt):", low):
        scheme = low.split(":", 1)[0]
        addr = re.sub(r"^\w+:", "", data).split("?")[0]
        return "wallet", {"chain": scheme.upper(), "address": addr}
    if re.match(r"^(0x[a-f0-9]{40}|bc1[a-z0-9]{20,}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[a-km-zA-HJ-NP-Z1-9]{33})$", data.strip()):
        return "wallet", {"address": data.strip()}
    if low.startswith(("http://", "https://", "www.", "t.me/")):
        parsed = urlparse(data if "://" in data else "https://" + data)
        return "url", {"url": data, "host": parsed.netloc or parsed.path.split("/")[0],
                       "path": parsed.path}
    if re.search(r"@", data) and len(data) < 120 and " " not in data:
        return "upi", {"vpa": data.strip()}
    return "text", {"raw": data}


def _opencv_qr_variants(img: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Build scale-aware variants for QR detection (QRs are found best when large).

    Returns (image, scale) pairs so corners can be mapped back to original pixels.
    """
    h, w = img.shape[:2]
    out: list[tuple[np.ndarray, float]] = [(img, 1.0)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out.append((gray, 1.0))
    # upscale so the long side is ~1200 px (bounded for small hosts)
    target = 1200
    scale = min(3.0, max(1.0, target / max(h, w)))
    if scale > 1.0:
        out.append((cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST), scale))
    return out


def decode_qr(image_path: str | Path) -> list[QRResult]:
    """Decode QR/barcodes with progressive upscaling + crops.

    pyzbar is used when libzbar is available, OpenCV's QRCodeDetector always.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return []
    results: list[QRResult] = []
    seen: set[str] = set()
    h, w = img.shape[:2]

    def _up(img: np.ndarray, scale: int) -> np.ndarray:
        hh, ww = img.shape[:2]
        target = (ww * scale, hh * scale)
        if max(target) > 900:  # bound CPU work on small hosts
            f = 900 / max(target)
            target = (max(40, int(target[0] * f)), max(40, int(target[1] * f)))
        return cv2.resize(img, target, interpolation=cv2.INTER_NEAREST)

    def _add(data: str, corners: Any, engine: str) -> None:
        data = (data or "").strip()
        if not data or data in seen or len(data) < 3:
            return
        seen.add(data)
        kind, parsed = parse_qr_payload(data)
        pts = []
        if corners is not None:
            try:
                pts = [[int(p[0]), int(p[1])] for p in corners]
            except Exception:
                pts = []
        results.append(QRResult(data=data, kind=kind, corners=pts, parsed=parsed, engine=engine))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # size-normalised variants for OpenCV (mapped back by scale)
    scaled = _opencv_qr_variants(img)
    # central crop (QR inside a poster) upscaled; corners mapped back by offset/scale
    ch, cw = int(h * 0.7), int(w * 0.7)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    crop = img[y0 : y0 + ch, x0 : x0 + cw]
    crop_scale = min(3.0, max(1.0, 1200 / max(ch, cw)))
    crop_up = cv2.resize(crop, None, fx=crop_scale, fy=crop_scale, interpolation=cv2.INTER_NEAREST)

    # pyzbar (needs libzbar; optional)
    try:
        from pyzbar import pyzbar

        for v in [img, gray, _up(img, 2), _up(img, 3), crop_up]:
            for code in pyzbar.decode(v):
                try:
                    data = code.data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                poly = getattr(code, "polygon", None)
                corners = [[p.x, p.y] for p in poly] if poly else None
                _add(data, corners, "pyzbar")
    except Exception:
        pass

    # OpenCV detector on size-normalised variants, then on the upscaled crop
    detector = cv2.QRCodeDetector()
    for v, scale in scaled:
        try:
            ok, decoded, pts, _ = detector.detectAndDecodeMulti(v)
        except Exception:
            continue
        if not ok or decoded is None:
            continue
        for i, d in enumerate(decoded):
            corners = pts[i] / scale if pts is not None and len(pts) > i else None
            _add(d, corners, "opencv")
    try:
        data, pts, _ = detector.detectAndDecode(crop_up)
        if data:
            corners = (pts / crop_scale + [x0, y0]) if pts is not None else None
            _add(data, corners, "opencv-crop")
    except Exception:
        pass
    return results


# --------------------------------------------------------------------------- facade
def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _scale_back(box: "TextBox", orig_shape: tuple, proc_shape: tuple) -> tuple[int, int, int, int]:
    """Map a box from the resized processing image back to original pixels."""
    fx = orig_shape[1] / max(proc_shape[1], 1)
    fy = orig_shape[0] / max(proc_shape[0], 1)
    return int(box.x * fx), int(box.y * fy), int(box.w * fx), int(box.h * fy)


def analyze_image(
    image_path: str | Path,
    *,
    save_dir: Path | None = None,
    do_ocr: bool = True,
    do_qr: bool = True,
    max_ocr_passes: int | None = None,
    run_recycle: bool | None = None,
) -> ImageResult:
    """Run the full universal OCR + QR pipeline on an image file."""
    image_path = Path(image_path)
    raw = np.fromfile(str(image_path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image: {image_path}")
    result = ImageResult(
        image_sha256=file_sha256(image_path),
        width=int(img.shape[1]),
        height=int(img.shape[0]),
    )
    if do_qr:
        result.qr_codes = decode_qr(image_path)
    if do_ocr:
        views = preprocess_passes(img)
        views.pop("_angle", None)
        ocr_names = ("color", "adaptive", "sharp")
        if max_ocr_passes is not None:
            ocr_names = ocr_names[: max(1, max_ocr_passes)]
        result.passes = list(ocr_names)
        if run_recycle is None:
            run_recycle = bool(int(__import__("os").environ.get("SPECTRA_OCR_RECYCLE", "1")))
        easy_boxes = _ocr_easyocr(views, ocr_names)
        tess_boxes = [] if easy_boxes else _ocr_tesseract(views)
        for b in easy_boxes + tess_boxes:  # map back to original coordinates
            b.x, b.y, b.w, b.h = _scale_back(b, img.shape, views["original"].shape)
        if run_recycle and easy_boxes:
            seen = {b.text.strip().lower() for b in easy_boxes}
            raw_gray = cv2.cvtColor(views["original"], cv2.COLOR_BGR2GRAY)
            extra = recycle_identifier_boxes([b for b in easy_boxes], seen, raw_gray)
            extra = [b for b in extra if b.text.strip().lower() not in seen]
            for b in extra:
                b.x, b.y, b.w, b.h = _scale_back(b, img.shape, views["original"].shape)
            easy_boxes.extend(extra)
        boxes = _merge_boxes(easy_boxes + tess_boxes)
        result.boxes = boxes
        result.text = _join_boxes(boxes)
        result.languages = _detect_lang(result.text)
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            stem = image_path.stem
            for name, view in views.items():
                p = save_dir / f"{stem}_{name}.png"
                cv2.imwrite(str(p), view)
                result.preprocessed_paths.append(str(p))
    return result


def exif_orientation_note(path: Path) -> dict:
    """Best-effort EXIF metadata for provenance."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            return {str(k): str(v)[:120] for k, v in exif.items()}
    except Exception:
        return {}


def audio_to_wav(src: str | Path, dst: str | Path) -> Path:
    """Convert any audio (ogg/opus/mp3/m4a/wav) to 16 kHz mono wav via ffmpeg."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return dst
    except (subprocess.CalledProcessError, FileNotFoundError):
        if str(src).endswith(".wav") and Path(src) != dst:
            dst.write_bytes(Path(src).read_bytes())
            return dst
        raise
