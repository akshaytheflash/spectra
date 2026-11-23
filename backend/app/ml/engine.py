"""Single hybrid engine: classify text and explain, one score.

Combines the lexicon, TF-IDF multi-label head, embedding head and the
calibration stack; adds the financial sub-classifier, human-readable reasons
and the highlighted spans used by the UI.
"""

from __future__ import annotations

import threading


from app.core.config import settings
from app.ml import financial
from app.ml.ensemble import StackedEnsemble, augment_text, label_of
from app.ml.lexicon import MODEL_LABELS, Lexicon, load_lexicon, span_to_markup
from app.ml.normalize import normalize
from app.ml.train import lex_features, meta_features

SEVERITY = {"arms": 1.2, "mule_fin": 1.1, "drugs": 1.0, "fake_docs": 1.0, "benign": 0.0}
FLAG_CONFIDENCE = 0.55  # threshold above which a non-benign class is decided

_lock = threading.Lock()
_model: StackedEnsemble | None = None


def get_model() -> StackedEnsemble | None:
    global _model
    with _lock:
        if _model is None:
            try:
                _model = StackedEnsemble.load(settings.MODEL_DIR)
            except FileNotFoundError:
                return None
        return _model


def reload_model() -> None:
    global _model
    with _lock:
        _model = None


class HybridEngine:
    """Stateless facade; the fitted model is a process-level singleton."""

    def __init__(self, lex: Lexicon | None = None) -> None:
        self.lex = lex or load_lexicon()
        self.model = get_model()

    # -------------------------------------------------------------- predict
    def predict(self, text: str) -> dict:
        """Classify a single text and return category, confidence, explanation."""
        text = text or ""
        res = self.lex.match(text)
        explanation = self.lex.explain(text, res)
        lex_only = self._lexicon_only(res)
        fin = financial.detect(text)
        probs: dict[str, float] = {}
        model_used = False
        if self.model is not None and text.strip():
            probs = self._model_probs(text)
            model_used = True
        else:
            # Fallback: lexicon-driven posterior (used before training finishes).
            probs = lex_only["_probs"]
            lex_only.pop("_probs", None)

        pred = max(probs, key=probs.get) if probs else "benign"
        conf = float(probs.get(pred, 0.0))
        hard_negative = bool(res.hard_negatives) and not self._has_sale_intent(text)
        # A mule/financial ad is always an illicit financial-recruitment signal,
        # unless it is a legitimate store/bank listing (financial detector vetoes).
        if fin.is_financial and not hard_negative and probs.get("mule_fin", 0) < 0.35:
            probs["mule_fin"] = max(probs.get("mule_fin", 0.0), 0.6 if fin.risk != "LOW" else 0.4)
            pred, conf = "mule_fin", probs["mule_fin"]
        if hard_negative and fin.is_financial is False and pred != "drugs" and not fin.is_financial:
            pass
        if pred == "benign":
            category = "benign"
        elif fin.is_financial and pred == "mule_fin":
            category = fin.type or "mule_account"
        else:
            category = self._best_category(pred, res)
        flagged = (
            (pred != "benign" and conf >= FLAG_CONFIDENCE)
            or (fin.is_financial and fin.risk in ("HIGH", "MEDIUM"))
        ) and not hard_negative
        if not flagged:
            category = "benign"
        reasons = self._reasons(pred, category, conf, res, fin, explanation)
        risk = self._risk(pred, conf, res, fin, explanation)
        return {
            "text": text,
            "normalized": normalize(text),
            "label": pred if flagged else "benign",
            "category": category,
            "subcategory": fin.type if (fin.is_financial and flagged) else None,
            "confidence": round(conf if flagged else max(0.0, 1 - probs.get(pred, 0.0) * (pred != "benign")), 4),
            "scores": {k: round(float(v), 4) for k, v in sorted(probs.items(), key=lambda kv: -kv[1])},
            "is_illicit": flagged,
            "risk": risk,
            "explanation": {
                **explanation,
                "markup": span_to_markup(text, explanation.get("spans", [])),
                "reasons": reasons,
                "financial": fin.as_dict(),
                "lexicon_only": lex_only,
                "model_used": model_used,
                "model_version": self.model.version if model_used and self.model else "lexicon-fallback",
            },
        }

    # -------------------------------------------------------------- internals
    def _model_probs(self, text: str) -> dict[str, float]:
        assert self.model is not None
        aug = augment_text(text, self.lex)
        p_tf = self.model.blocks["tfidf"].meta["classifier"].predict_proba(
            self.model.blocks["tfidf"].meta["vectorizer"].transform([aug])
        )
        p_em = None
        if self.model.has_embeddings:
            from app.ml.train import embed, embed_proba

            p_em = embed_proba(self.model.blocks["embed"], embed([normalize(text)]))
        L = lex_features([text], self.lex)
        p_lex = None
        if "lexicon" in self.model.blocks:
            from app.ml.train import lexicon_proba

            p_lex = lexicon_proba(self.model.blocks["lexicon"], L)
        probs = self.model.meta.predict_proba(meta_features(p_tf, p_em, L, p_lex))[0]
        raw = {lbl: float(p) for lbl, p in zip(self.model.labels, probs)}
        return self._apply_hard_negatives(text, raw)

    @staticmethod
    def _has_sale_intent(text: str) -> bool:
        """Strong evidence that a hard-negative phrase is being abused as a cover."""
        lowered = " " + text.lower() + " "
        signals = (
            " properly ", " discreet", "inbox", "kiraye", "rent pe", "rent par", "bechna",
            "commission", "delivery available", "cash only", "mafia", "dealer", "dm me",
            "no questions", "no risk",
        )
        return any(s in lowered for s in signals)

    def _apply_hard_negatives(self, text: str, probs: dict[str, float]) -> dict[str, float]:
        """Curated hard negatives suppress the category (curated + learned)."""
        res = self.lex.match(text)
        if not res.hard_negatives:
            return probs
        if self._has_sale_intent(text):
            return probs
        out = dict(probs)
        for cat in set(res.hard_negatives):
            lbl = label_of(cat)
            out[lbl] = min(out.get(lbl, 0.0), 0.12)
        out["benign"] = max(out.get("benign", 0.0), 0.8)
        tot = sum(max(v, 1e-6) for v in out.values())
        return {k: max(v, 1e-6) / tot for k, v in out.items()}

    def _lexicon_only(self, res) -> dict:
        by_cat = res.by_category()
        scores = {c: sum(h.weight for h in hits if h.kind != "hard_negative") for c, hits in by_cat.items()}
        probs = {lbl: 0.0 for lbl in MODEL_LABELS}
        for cat, sc in scores.items():
            if sc > 0:
                probs[label_of(cat)] += sc
        probs["benign"] = 0.9
        if res.hard_negatives:
            for cat in set(res.hard_negatives):
                probs[label_of(cat)] = 0.0
            probs["benign"] = max(probs["benign"], 2.5)
        total = sum(probs.values()) or 1.0
        probs = {k: v / total for k, v in probs.items()}
        return {
            "category_weights": {k: round(v, 2) for k, v in scores.items() if v},
            "_probs": probs,
        }

    @staticmethod
    def _best_category(label: str, res) -> str:
        by_cat = res.by_category()
        scores = {
            c: sum(h.weight for h in hits if h.kind != "hard_negative") for c, hits in by_cat.items()
            if label_of(c) == label
        }
        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return {"drugs": "drugs", "arms": "arms", "fake_docs": "fake_docs", "mule_fin": "mule_account"}.get(label, "benign")

    def _risk(self, label: str, conf: float, res, fin, explanation: dict) -> str:
        if label == "benign":
            return "LOW"
        if label == "mule_fin":
            return {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(fin.risk, "LOW")
        has_contact = any(s["kind"] in ("slang",) for s in explanation["spans"]) or bool(
            explanation.get("intent_phrases")
        )
        if conf >= 0.8 and (res.by_category().get(label) or has_contact):
            return "HIGH"
        if conf >= FLAG_CONFIDENCE:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _reasons(pred: str, category: str, conf: float, res, fin, explanation: dict) -> list[str]:
        reasons: list[str] = []
        top = explanation.get("top_features", [])[:4]
        if top:
            reasons.append(
                "Lexicon hits: " + ", ".join(f"{t['feature'].split(':', 1)[-1]} ({t['category']})" for t in top)
            )
        if explanation.get("intent_phrases"):
            reasons.append("Intent phrases: " + ", ".join(explanation["intent_phrases"][:5]))
        if explanation.get("hard_negatives"):
            reasons.append("Hard-negative match suppresses: " + ", ".join(explanation["hard_negatives"]))
        reasons.append(f"Model posterior: {pred} = {conf:.2f}")
        if fin.is_financial:
            reasons.append(
                f"Financial ad: type={fin.type}, intent={fin.intent}, risk={fin.risk}"
            )
        return reasons

    # -------------------------------------------------------------- batch
    def predict_many(self, texts: list[str]) -> list[dict]:
        """Batched prediction (one TF-IDF, one embedding pass for N posts)."""
        if not texts:
            return []
        if self.model is None:
            return [self.predict(t) for t in texts]
        from app.ml.train import embed, embed_proba, lexicon_proba

        m = self.model
        aug = [augment_text(t, self.lex) for t in texts]
        p_tf = m.blocks["tfidf"].meta["classifier"].predict_proba(
            m.blocks["tfidf"].meta["vectorizer"].transform(aug)
        )
        p_em = embed_proba(m.blocks["embed"], embed([normalize(t) for t in texts])) if m.has_embeddings else None
        L = lex_features(texts, self.lex)
        p_lex = lexicon_proba(m.blocks["lexicon"], L) if "lexicon" in m.blocks else None
        proba = m.meta.predict_proba(meta_features(p_tf, p_em, L, p_lex))
        out = []
        for i, text in enumerate(texts):
            probs = {lbl: float(p) for lbl, p in zip(m.labels, proba[i])}
            probs = self._apply_hard_negatives(text, probs)
            out.append(self._assemble(text, probs))
        return out

    def explain_only(self, text: str) -> dict:
        """Lexicon breakdown without the model (used by bulk ingest for storage)."""
        res = self.lex.match(text)
        return self.lex.explain(text, res)

    def _assemble(self, text: str, probs: dict[str, float]) -> dict:
        """Shared post-processing for single and batch predictions."""
        res = self.lex.match(text)
        explanation = self.lex.explain(text, res)
        lex_only = self._lexicon_only(res)
        lex_only.pop("_probs", None)
        fin = financial.detect(text)
        pred = max(probs, key=probs.get) if probs else "benign"
        conf = float(probs.get(pred, 0.0))
        hard_negative = bool(res.hard_negatives) and not self._has_sale_intent(text)
        if fin.is_financial and not hard_negative and probs.get("mule_fin", 0) < 0.35:
            probs["mule_fin"] = max(probs.get("mule_fin", 0.0), 0.6 if fin.risk != "LOW" else 0.4)
            pred, conf = "mule_fin", probs["mule_fin"]
        if pred == "benign":
            category = "benign"
        elif fin.is_financial and pred == "mule_fin":
            category = fin.type or "mule_account"
        else:
            category = self._best_category(pred, res)
        flagged = (
            (pred != "benign" and conf >= FLAG_CONFIDENCE)
            or (fin.is_financial and fin.risk in ("HIGH", "MEDIUM"))
        ) and not hard_negative
        if not flagged:
            category = "benign"
        return {
            "text": text,
            "normalized": normalize(text),
            "label": pred if flagged else "benign",
            "category": category,
            "subcategory": fin.type if (fin.is_financial and flagged) else None,
            "confidence": round(conf if flagged else max(0.0, 1 - probs.get(pred, 0.0) * (pred != "benign")), 4),
            "scores": {k: round(float(v), 4) for k, v in sorted(probs.items(), key=lambda kv: -kv[1])},
            "is_illicit": flagged,
            "risk": self._risk(pred, conf, res, fin, explanation),
            "explanation": {
                **explanation,
                "markup": span_to_markup(text, explanation.get("spans", [])),
                "reasons": self._reasons(pred, category, conf, res, fin, explanation),
                "financial": fin.as_dict(),
                "lexicon_only": lex_only,
                "model_used": self.model is not None,
                "model_version": self.model.version if self.model else "lexicon-fallback",
            },
        }


_engine: HybridEngine | None = None


def engine() -> HybridEngine:
    """Cached engine (rebuilt when the model or lexicon changes)."""
    global _engine
    if _engine is None or _engine.model is None:
        _engine = HybridEngine()
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None
    reload_model()


def explain_markup(result: dict) -> list[dict]:
    return result["explanation"]["markup"]
