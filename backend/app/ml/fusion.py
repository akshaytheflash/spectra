"""Fusion of the local hybrid engine with the LLM scanner verdict.

The local model is fast, deterministic and works offline; the LLM reads novel
slang, Hinglish paraphrases and emoji-only listings. The fusion is deliberately
conservative:

* both agree a post is illicit -> raise confidence and keep the stronger risk
* LLM flags, engine does not     -> adopt the LLM verdict (recall win)
* engine flags, LLM says benign  -> keep it only when the engine is very
  confident and has concrete evidence; otherwise suppress (precision win)
* both benign                    -> benign, with any emoji read-out attached
"""

from __future__ import annotations

from app.ml.emoji_signals import EmojiSignal, boost, scan, summary
from app.ml.llm import Verdict

_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

_LOCAL_TO_LLM = {
    "drugs": "drugs",
    "arms": "arms",
    "fake_docs": "fake_docs",
    "mule_fin": "mule_fin",
    "mule_account": "mule_fin",
    "atm_card": "mule_fin",
    "sim_card": "mule_fin",
    "net_banking_kit": "mule_fin",
    "other_illegal": "other_illegal",
    "benign": "benign",
}

_LLM_TO_CATEGORY = {
    "drugs": "drugs",
    "arms": "arms",
    "fake_docs": "fake_docs",
    "mule_fin": "mule_account",
    "other_illegal": "other_illegal",
}


def _max_risk(*values: str) -> str:
    best = "LOW"
    for v in values:
        v = (v or "LOW").upper()
        if _RISK_ORDER.get(v, 0) > _RISK_ORDER.get(best, 0):
            best = v
    return best


def _emoji_payload(signals: list[EmojiSignal]) -> list[dict]:
    return [s.as_dict() for s in signals]


def fuse(local: dict, verdict: Verdict | None, *, text: str) -> dict:
    """Return a copy of the engine result with LLM + emoji evidence fused in."""
    out = {**local}
    explanation = dict(local.get("explanation") or {})
    signals = scan(text)
    emoji_boost = boost(text, signals)
    llm = verdict or Verdict.unavailable("not-run")

    explanation["emoji_signals"] = _emoji_payload(signals)
    explanation["emoji_summary"] = summary(signals)
    explanation["llm"] = llm.as_dict()

    local_flag = bool(local.get("is_illicit"))
    local_conf = float(local.get("confidence") or 0.0)
    local_cat = str(local.get("category") or "benign")
    local_risk = str(local.get("risk") or "LOW")

    llm_flag = bool(llm.available and llm.flag)
    llm_conf = float(llm.confidence or 0.0)
    llm_cat = _LLM_TO_CATEGORY.get(llm.category, "other_illegal") if llm_flag else "benign"

    flagged = False
    category = "benign"
    confidence = local_conf
    risk = "LOW"
    source = "engine"
    note = ""

    if not llm.available:
        flagged, category, risk = local_flag, local_cat, local_risk
        confidence = local_conf
        # emoji-led ads still flag when the LLM is offline
        if not flagged and emoji_boost:
            cat, floor = max(emoji_boost.items(), key=lambda kv: kv[1])
            if floor >= 0.6:
                flagged, category, confidence, risk = True, cat, floor, "MEDIUM"
                source = "emoji"
        note = f"llm unavailable: {llm.error or 'unknown'}"
    elif llm_flag and local_flag:
        agree = _LOCAL_TO_LLM.get(local_cat) == llm.category
        if agree:
            confidence = min(0.99, 0.5 * local_conf + 0.5 * llm_conf + 0.05)
            category, risk = local_cat, _max_risk(local_risk, llm.severity)
            note = "engine + LLM agree"
        else:
            confidence = min(0.97, max(local_conf, llm_conf) * 0.92)
            category = llm_cat if llm_conf >= local_conf + 0.15 else local_cat
            risk = _max_risk(local_risk, llm.severity)
            note = f"engine={local_cat}, llm={llm.category}; stronger verdict leads"
        flagged = True
        source = "engine+llm"
    elif llm_flag:
        flagged = True
        category = llm_cat
        confidence = max(0.62, min(0.97, llm_conf))
        risk = llm.severity if llm.severity in _RISK_ORDER else "MEDIUM"
        source = "llm"
        note = "LLM recall win (engine was below threshold)"
    elif local_flag:
        strong = local_conf >= 0.82 and (
            explanation.get("top_features")
            or explanation.get("intent_phrases")
            or explanation.get("emoji_signals")
        )
        if strong:
            flagged, category, risk = True, local_cat, local_risk
            confidence = max(0.6, local_conf * 0.9)
            source = "engine"
            note = "engine kept over LLM dissent (high confidence + concrete evidence)"
        else:
            flagged, category, confidence, risk = False, "benign", max(0.4, 1 - local_conf), "LOW"
            source = "llm-veto"
            note = "LLM dissent suppressed a weak engine flag"
    else:
        # both benign: allow a strong emoji-only listing through
        if emoji_boost:
            cat, floor = max(emoji_boost.items(), key=lambda kv: kv[1])
            if floor >= 0.66:
                flagged, category, confidence, risk = True, cat, floor, "MEDIUM"
                source = "emoji"
                note = "emoji-led listing"
        if not flagged:
            flagged, category, confidence, risk = False, "benign", max(0.4, 1 - local_conf), "LOW"
            source = "engine"
            note = "both models benign"

    out.update(
        {
            "is_illicit": flagged,
            "label": _LOCAL_TO_LLM.get(category, category) if flagged else "benign",
            "category": category if flagged else "benign",
            "risk": risk if flagged else "LOW",
            "confidence": round(float(confidence), 4),
            "explanation": {
                **explanation,
                "fusion": {
                    "source": source,
                    "note": note,
                    "engine": {
                        "flag": local_flag,
                        "category": local_cat,
                        "confidence": round(local_conf, 3),
                        "risk": local_risk,
                    },
                    "llm": {
                        "available": llm.available,
                        "flag": llm_flag,
                        "category": llm.category,
                        "confidence": round(llm_conf, 3),
                        "severity": llm.severity,
                        "model": llm.model,
                    },
                    "emoji_boost": emoji_boost,
                },
            },
        }
    )
    return out
