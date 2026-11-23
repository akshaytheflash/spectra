"""Vision (OCR + QR) and speech tests. Skipped gracefully when models are absent."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.pipeline import vision


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _make_qr(tmp_path: Path, payload: str, degrade: str = "clean") -> Path:
    qrcode = pytest.importorskip("qrcode")
    from PIL import Image

    qr = qrcode.QRCode(box_size=6, border=3)
    qr.add_data(payload)
    qr.make()
    img = qr.make_image().convert("RGB")
    arr = np.array(img)
    if degrade == "noisy":
        arr = np.clip(arr.astype(np.float32) + np.random.normal(0, 20, arr.shape), 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    elif degrade == "small":
        small = img.resize((img.width // 3, img.height // 3), Image.BILINEAR)
        img = small
    elif degrade == "rotated":
        img = img.rotate(7, expand=True, fillcolor=(255, 255, 255))
    out = tmp_path / f"qr_{degrade}.png"
    img.save(out)
    return out


@pytest.mark.parametrize("degrade", ["clean", "noisy", "small", "rotated"])
def test_qr_decodes_upi(tmp_path: Path, degrade: str) -> None:
    vpa = "ravi.k1111@fakebank"
    path = _make_qr(tmp_path, f"upi://pay?pa={vpa}&pn=Ravi%20K&am=1500&cu=INR", degrade)
    codes = vision.decode_qr(path)
    assert codes, degrade
    assert codes[0].kind == "upi"
    assert codes[0].parsed["vpa"] == vpa
    assert codes[0].parsed["amount"] == "1500"


def test_qr_inside_poster(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    path = _make_qr(tmp_path, "upi://pay?pa=mule.ravi@fakebank&pn=Mule")
    poster = Image.new("RGB", (1000, 1400), (15, 15, 20))
    d = ImageDraw.Draw(poster)
    d.text((40, 40), "ACCOUNT RENT - KYC DONE - 9000011111", fill=(255, 255, 255))
    qr = Image.open(path).resize((420, 420))
    poster.paste(qr, (290, 900))
    out = tmp_path / "poster_qr.png"
    poster.save(out)
    codes = vision.decode_qr(out)
    assert any(c.parsed.get("vpa") == "mule.ravi@fakebank" for c in codes)


@pytest.mark.skipif(not _has_module("easyocr") and not _has_module("pytesseract"), reason="no OCR engine")
def test_ocr_reads_poster_text(tmp_path: Path) -> None:
    """OCR must recover the phone + UPI printed on a synthetic poster."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for c in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
        if Path(c).exists():
            font = ImageFont.truetype(c, 54)
            break
    img = Image.new("RGB", (1200, 600), (10, 10, 14))
    d = ImageDraw.Draw(img)
    d.text((50, 80), "PREMIUM MAAL", font=font, fill=(255, 255, 255))
    d.text((50, 240), "CALL 9000011111", font=font, fill=(255, 210, 0))
    d.text((50, 360), "UPI ravi.k1111@fakebank", font=font, fill=(220, 220, 220))
    path = tmp_path / "poster.png"
    img.save(path)
    res = vision.analyze_image(path, max_ocr_passes=1)
    compact = res.text.replace(" ", "")
    assert "9000011111" in compact, res.text
    assert res.boxes, "no OCR boxes returned"


@pytest.mark.skipif(not _has_module("faster_whisper"), reason="faster-whisper not installed")
def test_speech_transcribes_and_spots_numbers(tmp_path: Path) -> None:
    """Transcribe a synthetic voice note; spoken digits must become numeric."""
    from app.pipeline import speech

    sample = Path(__file__).resolve().parents[2] / "data" / "generated" / "audio"
    wavs = sorted(sample.glob("*.wav"))
    if not wavs:
        pytest.skip("no generated audio")
    tr = speech.transcribe_file(wavs[0])
    assert tr.segments and any(s.end > s.start for s in tr.segments)
    assert tr.audio_sha256 and tr.language
    print("transcript:", tr.text[:160], flush=True)


def test_spoken_number_compaction() -> None:
    from app.pipeline.speech import compact_spoken_numbers

    out = compact_spoken_numbers("call karo nine zero zero zero zero one two three four five six abhi")
    assert "90000123456" in out


def _write_tone_wav(path: Path, seconds: float = 0.6) -> None:
    import math
    import wave

    rate = 16000
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += int(0.3 * math.sin(2 * math.pi * 220 * i / rate) * 32767).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def test_audio_conversion(tmp_path: Path) -> None:
    """ffmpeg conversion to 16 kHz mono wav (the path every voice note takes)."""
    src = tmp_path / "in.wav"
    _write_tone_wav(src)
    out = vision.audio_to_wav(src, tmp_path / "out.wav")
    assert out.exists() and out.stat().st_size > 1000
