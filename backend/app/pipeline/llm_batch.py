"""Batching policy for LLM scans over bulk ingest.

Scanning thousands of posts one-by-one is slow and expensive. This helper
selects the messages that need an LLM opinion (flagged, borderline, or carrying
an emoji signal) and batches them. Live/manual scans can ask for ``mode="all"``.
"""

from __future__ import annotations

from app.core.config import settings
from app.ml import emoji_signals
from app.ml.llm import Verdict, scanner


def is_candidate(text: str, prediction: dict | None) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if prediction is None:
        return True
    if prediction.get("is_illicit"):
        return True
    scores = prediction.get("scores") or {}
    if float(scores.get("benign", 1.0)) < 0.9:
        return True
    return bool(emoji_signals.scan(text))


def scan_texts(
    texts: list[str],
    predictions: list[dict | None] | None = None,
    *,
    context: str = "",
    mode: str = "candidates",
    limit: int | None = None,
) -> list[Verdict | None]:
    """Return one verdict (or None) per text, in order.

    ``mode``:
      * ``all``        - scan every non-empty message (manual/live scans)
      * ``candidates`` - flagged, borderline or emoji-bearing messages only
      * ``off``        - no LLM calls
    ``limit`` caps how many candidates are sent (highest-signal first).
    """
    verdicts: list[Verdict | None] = [None] * len(texts)
    if mode == "off" or not settings.llm_ready:
        return verdicts
    if mode == "all":
        indexes = [i for i, t in enumerate(texts) if (t or "").strip()]
    else:
        predictions = predictions or [None] * len(texts)
        indexes = [
            i for i, t in enumerate(texts)
            if is_candidate(t, predictions[i] if i < len(predictions) else None)
        ]

        def _priority(i: int) -> tuple:
            pred = predictions[i] if i < len(predictions) else None
            if pred is None:
                return (0, 0.0, 0)
            emoji_count = len(emoji_signals.scan(texts[i]))
            benign = float((pred.get("scores") or {}).get("benign", 1.0))
            return (0 if pred.get("is_illicit") else 1, -emoji_count, benign)

        indexes.sort(key=_priority)
        if limit:
            indexes = indexes[:limit]
    if not indexes:
        return verdicts
    results = scanner().scan_many([texts[i] for i in indexes], context=context)
    for i, verdict in zip(indexes, results):
        verdicts[i] = verdict
    return verdicts
