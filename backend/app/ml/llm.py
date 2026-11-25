"""LLM scanning layer (OpenAI-compatible chat completions).

Configured for BharatCode by default:

    POST https://bharatcode.ai/api/model/v1/chat/completions
    Authorization: Bearer <BHARATCODE_API_KEY>
    model: glm-5.3-flash

The scanner returns a strict, typed verdict (flag / category / severity /
confidence / emoji signals / evidence / explanation). It complements the local
hybrid classifier: the local model is fast and offline, the LLM reads novel
slang, Hinglish paraphrases and emoji-only listings that the lexicon has not
seen yet.

Failure policy: every method degrades to ``Verdict.unavailable()``. A network
or quota problem must never break ingest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from app.core.config import settings

log = logging.getLogger("spectra.llm")

CATEGORIES = {"drugs", "arms", "fake_docs", "mule_fin", "other_illegal", "benign"}
SEVERITIES = {"HIGH", "MEDIUM", "LOW"}

SYSTEM_PROMPT = """You are a narcotics and arms trafficking analyst supporting the Rajasthan Police.
You review messages collected from PUBLIC Telegram channels. Your job is to decide whether a
message is a commercial offer, solicitation or coordination for illegal goods or financial crime.

FLAG when the message offers, sells, buys, rents, supplies or advertises:
- drugs (weed/ganja/charas/hash/MDMA/cocaine/heroin/opium/meth/LSD/edibles/vapes with THC)
- weapons, ammunition, explosives, country-made pistols (katta/tamancha), silencers, IEDs
- forged or purchased identity documents (Aadhaar, PAN, DL, passport, marksheets, SIMs)
- mule bank accounts, ATM cards with PIN, net-banking kits, OTP-linked SIMs, account/card rental

DO NOT flag: news reports, police/awareness posts, recovery or de-addiction help, satire,
movie/TV/game content, toy or airsoft guns, pharmacy or licensed medical sales, cooking
ingredients (hash browns, weed killer), SIM store or bank advertisements, personal use without
any trade intent, or vague slang with no offer.

Emojis are a real signal in this domain and must be read explicitly. Examples:
🍁 🌿 ❄️ 💊 🍄 🧊 💨 🔌 🥦 🌵 = cannabis/cocaine/meth/MDMA/edibles
🔫 💣 🧨 🗡️ ⚔️ = firearms/explosives/blades
💳 🏦 🏧 📲 💰 = bank accounts, ATM cards, OTP/banking kits
🪪 📜 🖨️ = identity documents / forgery
An emoji alone is weak evidence in a normal paragraph, but an emoji-only or
emoji-led listing ("fresh stock 🍁🔌💨") is a strong trade signal.
Understand Hindi, Hinglish and Romanised spellings (maal, stuff, plug, katta, tamancha,
account kiraye pe, kit with OTP).

Return ONLY a JSON object, no markdown:
{
  "flag": true|false,
  "category": "drugs"|"arms"|"fake_docs"|"mule_fin"|"other_illegal"|"benign",
  "severity": "HIGH"|"MEDIUM"|"LOW",
  "confidence": 0.0-1.0,
  "language": "en"|"hi"|"hinglish"|"other",
  "emoji_signals": [{"emoji": "🍁", "meaning": "cannabis", "category": "drugs"}],
  "evidence": ["short exact phrase copied from the message"],
  "explanation": "one sentence a police analyst can read",
  "identifiers": [{"type": "phone"|"upi"|"wallet"|"handle"|"link", "value": "..."}]
}
Set category to "benign" and flag to false when the message is not an offer.
Keep evidence phrases under 60 characters. Never invent identifiers that are not in the text.
"""

BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "Return ONLY a JSON object, no markdown:",
    "You will receive several numbered messages. Judge each independently.\n"
    "Return ONLY a JSON object, no markdown:",
).replace(
    '"flag": true|false,',
    '"results": [{"index": 0, "flag": true|false,',
).replace(
    '"identifiers": [{"type": "phone"|"upi"|"wallet"|"handle"|"link", "value": "..."}]\n}',
    '"identifiers": [{"type": "phone"|"upi"|"wallet"|"handle"|"link", "value": "..."}]}]\n}',
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


@dataclass
class Verdict:
    flag: bool = False
    category: str = "benign"
    severity: str = "LOW"
    confidence: float = 0.0
    language: str = "other"
    emoji_signals: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    explanation: str = ""
    identifiers: list[dict] = field(default_factory=list)
    available: bool = True
    model: str = ""
    latency_ms: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "flag": self.flag,
            "category": self.category,
            "severity": self.severity,
            "confidence": round(float(self.confidence), 3),
            "language": self.language,
            "emoji_signals": self.emoji_signals,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "identifiers": self.identifiers,
            "available": self.available,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }

    @classmethod
    def unavailable(cls, error: str) -> "Verdict":
        return cls(available=False, error=error)


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _coerce(payload: dict, *, model: str, latency_ms: int) -> Verdict:
    category = str(payload.get("category") or "benign").strip().lower()
    if category not in CATEGORIES:
        category = "other_illegal" if payload.get("flag") else "benign"
    severity = str(payload.get("severity") or "LOW").strip().upper()
    if severity not in SEVERITIES:
        severity = "MEDIUM" if payload.get("flag") else "LOW"
    flag = bool(payload.get("flag")) and category != "benign"
    emojis = payload.get("emoji_signals") or []
    if not isinstance(emojis, list):
        emojis = []
    evidence = payload.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    identifiers = payload.get("identifiers") or []
    if not isinstance(identifiers, list):
        identifiers = []
    return Verdict(
        flag=flag,
        category=category if flag else "benign",
        severity=severity if flag else "LOW",
        confidence=_clamp(payload.get("confidence"), 0.5 if flag else 0.2),
        language=str(payload.get("language") or "other"),
        emoji_signals=[e for e in emojis if isinstance(e, dict)][:12],
        evidence=[str(e)[:120] for e in evidence][:8],
        explanation=str(payload.get("explanation") or "")[:500],
        identifiers=[i for i in identifiers if isinstance(i, dict)][:12],
        available=True,
        model=model,
        latency_ms=latency_ms,
    )


def _parse_json(content: str) -> dict | None:
    content = (content or "").strip()
    fence = _CODE_FENCE.search(content)
    if fence:
        content = fence.group(1).strip()
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    block = _JSON_BLOCK.search(content)
    if not block:
        return None
    try:
        value = json.loads(block.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class LLMScanner:
    """Thread-safe, cached, failure-tolerant chat-completions client."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, Verdict] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_size = 2048

    @property
    def available(self) -> bool:
        return settings.llm_ready

    @property
    def model(self) -> str:
        return settings.BHARATCODE_MODEL

    # ------------------------------------------------------------------ cache
    def _cache_key(self, text: str, context: str) -> str:
        raw = f"{self.model}\x00{context}\x00{text}".encode("utf-8", "ignore")
        return hashlib.sha256(raw).hexdigest()

    def _cache_get(self, key: str) -> Verdict | None:
        with self._cache_lock:
            verdict = self._cache.get(key)
            if verdict is not None:
                self._cache.move_to_end(key)
            return verdict

    def _cache_put(self, key: str, verdict: Verdict) -> None:
        with self._cache_lock:
            self._cache[key] = verdict
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    # ------------------------------------------------------------------ HTTP
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.BHARATCODE_API_KEY.strip()}",
            "Content-Type": "application/json",
        }

    def _url(self) -> str:
        return settings.BHARATCODE_BASE_URL.rstrip("/") + "/chat/completions"

    def _post(self, messages: list[dict], *, max_tokens: int = 1400) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        last_error = "unknown error"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=settings.LLM_TIMEOUT) as client:
                    res = client.post(self._url(), headers=self._headers(), json=payload)
                if res.status_code in (429, 500, 502, 503, 504):
                    last_error = f"http {res.status_code}"
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if res.status_code >= 400:
                    detail = res.text[:300].replace(settings.BHARATCODE_API_KEY, "***")
                    raise RuntimeError(f"http {res.status_code}: {detail}")
                return res.json()
            except httpx.TimeoutException:
                last_error = "timeout"
                time.sleep(1.5 * (attempt + 1))
            except httpx.HTTPError as exc:
                last_error = f"network: {exc.__class__.__name__}"
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(last_error)

    @staticmethod
    def _content(body: dict) -> str:
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    # ------------------------------------------------------------------ single
    def scan(self, text: str, *, context: str = "") -> Verdict:
        """Classify one message. Returns ``Verdict.unavailable`` on failure."""
        text = (text or "").strip()
        if not text:
            return Verdict(available=True, model=self.model)
        if not self.available:
            return Verdict.unavailable("llm-disabled")
        key = self._cache_key(text, context)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        user = (
            f"CHANNEL: @{context or 'unknown'}\n"
            f"MESSAGE:\n{text[:6000]}"
        )
        t0 = time.time()
        try:
            body = self._post(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
            )
            payload = _parse_json(self._content(body))
            if payload is None:
                # one corrective retry, cheap and bounded
                body = self._post(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                        {"role": "user", "content": "Your previous answer was not valid JSON. Return only the JSON object."},
                    ]
                )
                payload = _parse_json(self._content(body))
            if payload is None:
                verdict = Verdict.unavailable("unparseable-llm-response")
            else:
                verdict = _coerce(payload, model=self.model, latency_ms=int((time.time() - t0) * 1000))
        except Exception as exc:  # never break ingest
            log.warning("LLM scan failed: %s", str(exc)[:200])
            verdict = Verdict.unavailable(str(exc)[:200])
        self._cache_put(key, verdict)
        return verdict

    # ------------------------------------------------------------------ batch
    def scan_many(self, texts: Iterable[str], *, context: str = "", chunk: int = 8) -> list[Verdict]:
        """Batch scan with bounded parallelism; order is preserved."""
        items = [t or "" for t in texts]
        if not items:
            return []
        if not self.available:
            return [Verdict.unavailable("llm-disabled") for _ in items]
        results: list[Verdict | None] = [None] * len(items)
        pending: list[tuple[int, list[int], list[str]]] = []
        for start in range(0, len(items), chunk):
            indexes = list(range(start, min(start + chunk, len(items))))
            # serve cached / empty texts without a request
            need: list[int] = []
            for i in indexes:
                text = items[i].strip()
                if not text:
                    results[i] = Verdict(available=True, model=self.model)
                    continue
                cached = self._cache_get(self._cache_key(text, context))
                if cached is not None:
                    results[i] = cached
                else:
                    need.append(i)
            if need:
                pending.append((start, need, [items[i] for i in need]))

        def _run(job: tuple[int, list[int], list[str]]) -> None:
            _, indexes, texts_chunk = job
            verdicts = self._scan_chunk(texts_chunk, context=context)
            for i, verdict in zip(indexes, verdicts):
                results[i] = verdict
                if verdict.available:
                    self._cache_put(self._cache_key(items[i].strip(), context), verdict)

        workers = max(1, min(int(settings.LLM_MAX_CONCURRENCY or 1), len(pending) or 1))
        if len(pending) == 1:
            _run(pending[0])
        elif pending:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_run, pending))
        return [r or Verdict.unavailable("no-result") for r in results]

    def _scan_chunk(self, texts: list[str], *, context: str) -> list[Verdict]:
        numbered = "\n\n".join(f"[{i}] {t[:3500]}" for i, t in enumerate(texts))
        user = (
            f"CHANNEL: @{context or 'unknown'}\n"
            f"Judge each numbered message independently.\n\n{numbered}"
        )
        t0 = time.time()
        try:
            body = self._post(
                [{"role": "system", "content": BATCH_SYSTEM_PROMPT}, {"role": "user", "content": user}],
                max_tokens=2600,
            )
            payload = _parse_json(self._content(body))
            rows = (payload or {}).get("results") if payload else None
            if not isinstance(rows, list):
                # fall back to one-by-one rather than dropping the chunk
                return [self.scan(t, context=context) for t in texts]
            by_index: dict[int, dict] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    by_index[int(row.get("index"))] = row
                except (TypeError, ValueError):
                    continue
            latency = int((time.time() - t0) * 1000)
            out: list[Verdict] = []
            for i in range(len(texts)):
                row = by_index.get(i)
                out.append(
                    _coerce(row, model=self.model, latency_ms=latency)
                    if row is not None
                    else Verdict.unavailable("missing-batch-result")
                )
            return out
        except Exception as exc:
            log.warning("LLM batch failed: %s", str(exc)[:200])
            return [self.scan(t, context=context) for t in texts]

    def ping(self) -> dict:
        """Small connectivity check for the dashboard settings page."""
        if not self.available:
            return {"ok": False, "reason": "llm-disabled"}
        t0 = time.time()
        try:
            body = self._post(
                [
                    {"role": "system", "content": "Return only the JSON object {\"ok\": true}."},
                    {"role": "user", "content": "ping"},
                ],
                max_tokens=64,
            )
            return {
                "ok": bool(self._content(body)),
                "model": self.model,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:200], "model": self.model}


_scanner: LLMScanner | None = None


def scanner() -> LLMScanner:
    global _scanner
    if _scanner is None:
        _scanner = LLMScanner()
    return _scanner
