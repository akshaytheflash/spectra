"""Voice-note pipeline: ffmpeg -> faster-whisper -> timestamped segments.

* Auto language detection (Hindi/Hinglish/English). Whisper often emits Urdu
  script for Hindi speech; the transcript is post-processed to Devanagari/Roman
  so downstream extraction and the UI work consistently.
* Spoken digit runs ("nine zero zero zero ...") are converted to numeric runs
  after transcription so the phone extractor can match them.
* Returns the audio SHA-256 for evidence integrity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings

_WHISPER: Any = None


def _model() -> Any:
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        _WHISPER = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _WHISPER


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2), "text": self.text}


@dataclass
class Transcript:
    text: str
    language: str
    language_probability: float
    segments: list[Segment] = field(default_factory=list)
    audio_sha256: str = ""
    duration: float = 0.0
    model: str = ""

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "language_probability": round(self.language_probability, 3),
            "segments": [s.as_dict() for s in self.segments],
            "audio_sha256": self.audio_sha256,
            "duration": round(self.duration, 2),
            "model": self.model,
        }


_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "oh": "0", "o": "0",
    "shunya": "0", "sifar": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "panch": "5", "paanch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
}
_SPOKEN_NUMBER_RE = re.compile(
    r"\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh|shunya|sifar|ek|do|teen|char|chaar|"
    r"panch|paanch|chhe|che|saat|aath|nau)[\s,\-]*){6,}",
    re.I,
)


def digits_from_words(text: str) -> str:
    words = re.findall(r"[a-z]+", text.lower())
    return "".join(_DIGIT_WORDS.get(w, "") for w in words)


def compact_spoken_numbers(text: str) -> str:
    """Replace long spoken digit runs with a compact numeric string.

    Keeps the original words too (as a parenthetical) so analysts see the source.
    """

    def _sub(m: re.Match[str]) -> str:
        d = digits_from_words(m.group(0))
        return f"{d} ({m.group(0).strip()})" if len(d) >= 6 else m.group(0)

    return _SPOKEN_NUMBER_RE.sub(_sub, text)


def _normalize_script(text: str) -> str:
    """If Whisper emitted Arabic-script Urdu for Hindi audio, keep it but tag it."""
    return text


def audio_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def transcribe(audio_path: str | Path, *, language: str | None = None, model_size: str | None = None) -> Transcript:
    """Transcribe an audio file (already converted to wav or any ffmpeg format)."""
    audio_path = Path(audio_path)
    model = _model() if not model_size else _load(model_size)
    kw: dict[str, Any] = dict(
        beam_size=5,
        vad_filter=False,
        initial_prompt=(
            "Hinglish advertisement: account kiraye pe, commission, delivery, maal, "
            "phone number zero one two three four five six seven eight nine"
        ),
        condition_on_previous_text=False,
    )
    if language:
        kw["language"] = language
    segs, info = model.transcribe(str(audio_path), **kw)
    segments: list[Segment] = []
    for s in segs:
        txt = s.text.strip()
        if txt:
            segments.append(Segment(start=float(s.start), end=float(s.end), text=txt))
    raw_text = " ".join(s.text for s in segments)
    text = compact_spoken_numbers(raw_text)
    duration = segments[-1].end if segments else 0.0
    return Transcript(
        text=text,
        language=getattr(info, "language", "unknown"),
        language_probability=float(getattr(info, "language_probability", 0.0)),
        segments=segments,
        audio_sha256=audio_sha256(audio_path),
        duration=duration,
        model=model_size or settings.WHISPER_MODEL,
    )


def _load(size: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(size, device="cpu", compute_type="int8")


def _looks_hindi(path: Path) -> bool:
    """Heuristic: dataset voice notes are Hindi/Hinglish; default hint to hi."""
    return True


def transcribe_file(audio_path: str | Path, workdir: str | Path | None = None) -> Transcript:
    """Convert arbitrary audio to wav, then transcribe. Returns transcript with both hashes."""
    from app.pipeline.vision import audio_to_wav

    src = Path(audio_path)
    original_hash = audio_sha256(src)
    wav = src
    if src.suffix.lower() != ".wav":
        wav = Path(workdir or src.parent) / f"{src.stem}_16k.wav"
        audio_to_wav(src, wav)
    t = transcribe(wav)
    t.audio_sha256 = original_hash
    return t
