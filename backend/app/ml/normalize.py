"""Text normalization shared by the ML engine and the identifier extractors.

Pipeline:
  NFKC/NFKC unicode normalization -> Devanagari digit mapping -> emoji ->
  ``:emoji_name:`` tokens -> Devanagari transliteration to Roman -> lower case
  -> de-leetspeak -> collapse repeated characters -> whitespace collapse.

The normalizer is deterministic and dependency-light so the same representation
is used at training and inference time.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass

import emoji as emoji_lib

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")

# Common Hinglish spelling variants -> canonical lexicon spelling.
_HINGLISH_FIXES = {
    "ganjaa": "ganja",
    "gaanza": "ganja",
    "charas": "charas",
    "cars": "charas",
    "kitaye": "kiraye",
    "kiraya": "kiraye",
    "kraye": "kiraye",
    "kirye": "kiraye",
    "achount": "account",
    "acount": "account",
    "accnt": "account",
    "ac": "account",
    "acounts": "accounts",
    "kyc": "kyc",
    "tammancha": "tamancha",
    "tamanchaa": "tamancha",
    "catta": "katta",
    "kata": "katta",
    "aadhar": "aadhaar",
    "adhar": "aadhaar",
    "pan card": "pan card",
    "golis": "goli",
    "snoww": "snow",
    "mdma": "mdma",
    "whatapp": "whatsapp",
    "telygram": "telegram",
}

_LEET = str.maketrans(
    {
        "@": "a",
        "4": "a",
        "3": "e",
        "0": "o",
        "1": "i",
        "$": "s",
        "5": "s",
        "7": "t",
        "€": "e",
        "!": "i",
        "|": "l",
    }
)

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


@functools.lru_cache(maxsize=1)
def _transliterator():  # pragma: no cover - optional dependency
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        return lambda s: transliterate(s, sanscript.DEVANAGARI, sanscript.IAST)
    except Exception:
        return None


def transliterate_devanagari(text: str) -> str:
    """Transliterate Devanagari characters to Latin (IAST) when present."""
    if not DEVANAGARI_RANGE.search(text):
        return text
    fn = _transliterator()
    if fn is None:  # graceful fallback: strip combining marks
        return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    try:
        out = fn(text)
        # IAST diacritics are noise for matching; fold them to ASCII.
        return "".join(
            c for c in unicodedata.normalize("NFKD", out) if not unicodedata.combining(c)
        )
    except Exception:
        return text


def devanagari_to_digits(text: str) -> str:
    return text.translate(DEVANAGARI_DIGITS)


def emoji_to_tokens(text: str) -> str:
    """Replace emojis with textual tokens: ``🍁`` -> ``:maple_leaf:``."""
    try:
        return emoji_lib.demojize(text, language="en", delimiters=(" :emoji_", ": "))
    except Exception:
        return text


def de_leetspeak(text: str) -> str:
    """Fold common leet substitutions in word-like tokens (``w33d`` -> ``weed``).

    Only tokens that mix letters with leet digits/symbols (or use leet symbols
    for letters) are folded, so ordinary words such as ``some`` are untouched.
    ``$`` is deliberately not folded (it appears in prices like ``₹2k/$``).
    """
    def _fix(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok.isalpha():
            return tok
        if re.match(r"\d", tok):  # digit-led tokens are quantities (2k, 50g, 9mm)
            return tok
        return tok.translate(_LEET)

    return re.sub(r"[a-z0-9@$!|€]*[a-z][a-z0-9@$!|€]*", _fix, text)


def normalize(text: str, *, leet: bool = True, translit: bool = True) -> str:
    """Full normalization used by the ML engine."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = devanagari_to_digits(t)
    t = emoji_to_tokens(t)
    if translit:
        t = transliterate_devanagari(t)
        t = unicodedata.normalize("NFKD", t)
        t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = t.replace("’", "'").replace("`", "'")
    if leet:
        t = de_leetspeak(t)
    t = re.sub(r"(.)\1{3,}", r"\1\1", t)  # gaaaaanja -> gaanja
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    for bad, good in _HINGLISH_FIXES.items():
        if bad != good:
            t = re.sub(rf"(?<![a-z0-9]){re.escape(bad)}(?![a-z0-9])", good, t)
    return t.strip()


def normalize_for_matching(text: str) -> str:
    """Normalization for identifier extraction: keeps digits/URLs intact."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = devanagari_to_digits(t)
    return t


@dataclass(slots=True)
class Span:
    """A matched span in the ORIGINAL text used for UI highlighting."""

    start: int
    end: int
    text: str
    kind: str  # slang|emoji|intent|price|contact|hard_negative
    category: str
    weight: float


def strip_urls(text: str) -> str:
    return _URL_RE.sub(" ", text)
