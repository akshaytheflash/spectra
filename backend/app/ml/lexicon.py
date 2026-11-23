"""Curated lexicon: loading, matching, feature extraction and explanations.

Hits are produced on BOTH the raw (case-folded) original text and the fully
normalized text (leetspeak folded, transliterated), so that:

* ``w33d`` / ``m@al`` / ``गांजा`` match ``weed`` / ``maal`` / ``ganja``;
* explanation spans point at the ORIGINAL characters for highlighting in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from app.ml.normalize import emoji_to_tokens, normalize

LEXICON_PATH = Path(__file__).resolve().parents[3] / "data" / "lexicon.yaml"

CATEGORIES: tuple[str, ...] = (
    "drugs",
    "arms",
    "fake_docs",
    "mule_account",
    "atm_card",
    "sim_card",
    "net_banking_kit",
)
# Categories that roll up into each ML class.
MODEL_LABELS: tuple[str, ...] = ("drugs", "arms", "fake_docs", "mule_fin", "benign")

_INTENT_SPAN_RE = re.compile(
    r"\b(available|dm|inbox|chahiye|chahie|milega|mil jayega|bechna|bech|kiraye|kiraya|kraye|"
    r"rent pe|rent par|on rent|sale|sell|buy|trade|supply|delivery|commission|daily|contact|"
    r"whatsapp|telegram|price|rate|kit|otp|with pin|urgent)\b",
    re.I,
)


@dataclass
class LexiconHit:
    category: str
    kind: str  # term|slang|devanagari|emoji|intent|price
    term: str
    start: int
    end: int
    weight: float
    raw: str = ""


@dataclass
class LexiconResult:
    hits: list[LexiconHit] = field(default_factory=list)
    hard_negatives: list[str] = field(default_factory=list)
    emoji_only_category: str | None = None

    def by_category(self) -> dict[str, list[LexiconHit]]:
        out: dict[str, list[LexiconHit]] = {c: [] for c in CATEGORIES}
        for h in self.hits:
            out.setdefault(h.category, []).append(h)
        return out

    def feature_vector(self) -> dict[str, float]:
        """Numeric features consumed by the fusion layer."""
        feats: dict[str, float] = {}
        by_cat = self.by_category()
        for cat in CATEGORIES:
            hits = by_cat.get(cat, [])
            kinds = {h.kind for h in hits}
            feats[f"lex.{cat}.count"] = float(len(hits))
            feats[f"lex.{cat}.weight"] = float(sum(h.weight for h in hits))
            feats[f"lex.{cat}.has_slang"] = float(bool(kinds & {"slang", "devanagari", "emoji"}))
            feats[f"lex.{cat}.has_intent"] = float("intent" in kinds)
            feats[f"lex.{cat}.has_price"] = float("price" in kinds)
        feats["lex.any"] = float(bool(self.hits))
        feats["lex.hard_negative"] = float(bool(self.hard_negatives))
        return feats


class Lexicon:
    """Loads ``data/lexicon.yaml`` + analyst overlay terms."""

    def __init__(self, data: dict, overlay: Iterable[dict] | None = None) -> None:
        self.version = int(data.get("version", 1))
        self.categories: dict[str, dict] = data.get("categories", {})
        self.emoji_only_map: dict[str, list[str]] = data.get("emoji_only_map", {})
        self.hard_negatives: list[dict] = list(data.get("hard_negatives", []))
        self.overlay: list[dict] = list(overlay or [])
        # pre-compile
        self._compiled: dict[str, list[tuple[str, str, float, re.Pattern[str]]]] = {}
        self._hn_compiled: list[tuple[re.Pattern[str], str]] = []
        self._emoji_set: dict[str, str] = {}
        for cat, spec in self.categories.items():
            w = float(spec.get("weight", 1.0))
            patterns: list[tuple[str, str, float, re.Pattern[str]]] = []
            for kind in ("terms", "slang"):
                for term in spec.get(kind, []) or []:
                    patterns.append((kind, term, w, self._term_re(term)))
            for term in spec.get("devanagari", []) or []:
                patterns.append(("devanagari", term, w, self._term_re(term, transliterate_ok=True)))
            for em in spec.get("emojis", []) or []:
                self._emoji_set[em] = cat
            for it in spec.get("intent", []) or []:
                patterns.append(("intent", it, w * 0.8, self._term_re(it)))
            for pat in spec.get("price_patterns", []) or []:
                try:
                    patterns.append(("price", pat, w * 0.6, re.compile(pat)))
                except re.error:
                    continue
            self._compiled[cat] = patterns
        for hn in self.hard_negatives:
            try:
                self._hn_compiled.append((re.compile(hn["pattern"]), hn["category"]))
            except (KeyError, re.error):
                continue
        for ov in self.overlay:
            cat = ov.get("category", "")
            term = (ov.get("term") or "").strip()
            if cat not in self.categories or not term:
                continue
            kind = ov.get("kind", "slang")
            weight = float(ov.get("weight", 1.0)) * float(self.categories[cat].get("weight", 1.0))
            self._compiled[cat].append((kind, term, weight, self._term_re(term, transliterate_ok=True)))
            if kind == "emojis":
                self._emoji_set[term] = cat

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _term_re(term: str, transliterate_ok: bool = False) -> re.Pattern[str]:
        t = term.strip()
        if t.startswith("(?i)"):
            return re.compile(t)
        words = [re.escape(w) for w in t.split()]
        body = r"\s*".join(words)
        # Devanagari terms are matched against transliterated text as well.
        if transliterate_ok:
            norm = re.escape(normalize(t, leet=False))
            body = f"(?:{body}|\\s*{norm})"
        return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.I)

    def _emoji_spans(self, text: str) -> list[tuple[str, int, int, str, float]]:
        """Locate coded emojis (original text) and their token equivalents."""
        out = []
        tokenized = emoji_to_tokens(text.lower())
        for em, cat in self._emoji_set.items():
            for m in re.finditer(re.escape(em), text):
                w = float(self.categories[cat].get("weight", 1.0))
                out.append((em, m.start(), m.end(), cat, w))
            # token form (:emoji_maple_leaf:) fallback for text that already has tokens
            name = emoji_to_tokens(em.lower()).strip()
            if name and name != em.lower() and name in tokenized:
                idx = tokenized.find(name)
                out.append((em, idx, idx + len(name), cat, float(self.categories[cat].get("weight", 1.0))))
        return out

    def emoji_only(self, text: str) -> str | None:
        """Detect emoji-only coded ads like ``🍁🔌💨``."""
        stripped = text.strip()
        if not stripped:
            return None
        # Must contain no ASCII letters/digits -- emojis and separators only.
        if re.search(r"[A-Za-z0-9]", stripped):
            return None
        scores: dict[str, int] = {}
        for em, cat in self._emoji_set.items():
            if em in text:
                scores[cat] = scores.get(cat, 0) + 1
        if not scores:
            return None
        return max(scores, key=lambda k: scores[k])

    # ------------------------------------------------------------------ match
    def match(self, text: str) -> LexiconResult:
        """Run all patterns on original + normalized text."""
        res = LexiconResult()
        if not text:
            return res
        norm = normalize(text)
        seen: set[tuple[str, str, int]] = set()

        for cat, patterns in self._compiled.items():
            for kind, term, weight, rx in patterns:
                if kind == "price":
                    for m in rx.finditer(text):
                        key = (cat, kind, m.start())
                        if key not in seen:
                            seen.add(key)
                            res.hits.append(LexiconHit(cat, kind, m.group(0), m.start(), m.end(), weight, m.group(0)))
                    continue
                found = False
                for m in rx.finditer(text):
                    key = (cat, kind, m.start())
                    if key in seen:
                        continue
                    seen.add(key)
                    found = True
                    res.hits.append(LexiconHit(cat, kind, term, m.start(), m.end(), weight, m.group(0)))
                # Leet / transliteration / typo variants are matched on the normalized text;
                # map back to a best-effort original span.
                if not found and kind in ("slang", "terms", "devanagari"):
                    for m in rx.finditer(norm):
                        start, end = _map_norm_span(text, norm, m.start(), m.end())
                        key = (cat, kind, start)
                        if key in seen:
                            continue
                        seen.add(key)
                        res.hits.append(
                            LexiconHit(cat, kind, term, start, end, weight * 0.95, text[start:end])
                        )
        # emojis
        emoji_seen: set[tuple[str, int]] = set()
        for em, start, end, cat, w in self._emoji_spans(text):
            if (em, start) in emoji_seen:
                continue
            emoji_seen.add((em, start))
            key = (cat, "emoji", start)
            if key in seen:
                continue
            seen.add(key)
            res.hits.append(LexiconHit(cat, "emoji", em, start, end, w, em))
        # hard negatives
        for rx, cat in self._hn_compiled:
            for m in rx.finditer(text):
                res.hard_negatives.append(cat)
                res.hits.append(LexiconHit(cat, "hard_negative", m.group(0), m.start(), m.end(), -1.0, m.group(0)))
                break
        res.emoji_only_category = self.emoji_only(text) if not res.hits else None
        if res.emoji_only_category:
            res.hits.append(
                LexiconHit(res.emoji_only_category, "emoji", "emoji-only ad", 0, len(text), 1.2, text)
            )
        # Contradicting adjacent hits (e.g. one "sale" token present for several
        # distinct categories) are likely generic commerce language; intent hits
        # that only co-occur with >=2 categories are kept at half weight.
        intent_cats: dict[str, set[str]] = {}
        for h in res.hits:
            if h.kind == "intent":
                intent_cats.setdefault(h.term.lower(), set()).add(h.category)
        for h in res.hits:
            if h.kind == "intent" and len(intent_cats.get(h.term.lower(), ())) >= 2:
                h.weight *= 0.5
        return res

    # ------------------------------------------------------------ explanation
    def explain(self, text: str, result: LexiconResult) -> dict:
        """Build the explanation payload: highlighted spans + top features."""
        spans: list[dict] = []
        for h in result.hits:
            if h.kind == "hard_negative":
                kind = "hard_negative"
            elif h.kind == "emoji":
                kind = "emoji"
            elif h.kind == "intent":
                kind = "intent"
            elif h.kind == "price":
                kind = "price"
            else:
                kind = "slang"
            spans.append(
                {
                    "start": h.start,
                    "end": h.end,
                    "text": h.raw or h.term,
                    "kind": kind,
                    "category": h.category,
                    "weight": round(h.weight, 2),
                }
            )
        # Merge overlapping spans so the UI can render cleanly.
        spans.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
        merged: list[dict] = []
        for s in spans:
            if merged and s["start"] < merged[-1]["end"] and s["category"] == merged[-1]["category"]:
                prev = merged[-1]
                prev["end"] = max(prev["end"], s["end"])
                prev["text"] = text[prev["start"] : prev["end"]]
                continue
            merged.append(dict(s))
        per_cat: dict[str, float] = {c: 0.0 for c in CATEGORIES}
        for h in result.hits:
            if h.kind != "hard_negative":
                per_cat[h.category] = per_cat.get(h.category, 0) + h.weight
        top = sorted(
            (
                {"feature": f"{h.kind}:{h.term}", "category": h.category, "weight": round(h.weight, 2)}
                for h in result.hits
                if h.kind != "hard_negative"
            ),
            key=lambda d: -d["weight"],
        )[:8]
        matched = sorted({h.raw or h.term for h in result.hits if h.kind != "hard_negative"})
        intents: list[str] = [s for s in _INTENT_SPAN_RE.findall(normalize(text, leet=False))]
        return {
            "spans": merged,
            "top_features": top,
            "matched_terms": matched,
            "intent_phrases": sorted(set(i.lower() for i in intents)),
            "hard_negatives": sorted(set(result.hard_negatives)),
            "category_weights": {k: round(v, 2) for k, v in per_cat.items() if v},
        }


def _map_norm_span(orig: str, norm: str, ns: int, ne: int) -> tuple[int, int]:
    """Approximate mapping of a span in normalized text back to original text."""
    ratio = len(orig) / max(len(norm), 1)
    start = min(int(ns * ratio), len(orig))
    end = min(max(int(ne * ratio), start + 1), len(orig))
    return start, end


_LEXICON: Lexicon | None = None


def load_lexicon(force: bool = False) -> Lexicon:
    """Cached lexicon loader."""
    global _LEXICON
    if _LEXICON is not None and not force:
        return _LEXICON
    with open(LEXICON_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    _LEXICON = Lexicon(data)
    return _LEXICON


def set_overlay(rows: Iterable[dict]) -> Lexicon:
    """Reload lexicon with analyst overlay rows (from ``lexicon_terms``)."""
    global _LEXICON
    with open(LEXICON_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    _LEXICON = Lexicon(data, overlay=rows)
    return _LEXICON


def span_to_markup(text: str, spans: list[dict]) -> list[dict]:
    """Split text into renderable segments (plain + highlighted) for the UI."""
    spans = sorted([s for s in spans if s.get("end", 0) > s.get("start", 0)], key=lambda s: s["start"])
    out: list[dict] = []
    cursor = 0
    for s in spans:
        if s["start"] < cursor:
            continue
        if s["start"] > cursor:
            out.append({"text": text[cursor : s["start"]], "kind": "plain"})
        out.append({"text": text[s["start"] : s["end"]], "kind": s["kind"], "category": s["category"]})
        cursor = s["end"]
    if cursor < len(text):
        out.append({"text": text[cursor:], "kind": "plain"})
    return out
