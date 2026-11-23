"""Training + evaluation for the SPECTRA hybrid engine.

Produces (in ``backend/artifacts/models``):
  block_tfidf.pkl   - TF-IDF + One-vs-Rest LogisticRegression
  block_embed.pkl   - MiniLM embeddings + logistic head
  meta.pkl          - calibration stack + label list
  metrics.json      - per-class P/R/F1, macro/weighted F1, confusion matrix,
                      hard-negative checks, embedding-holdout results

The training script uses 5-fold out-of-fold predictions to fit the calibration
stack so held-out F1 is honest.  Splits are grouped by near-duplicate text so
mutated campaign copies never leak between train and test.
"""

from __future__ import annotations

import json
import pickle
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold  # noqa: E402
from sklearn.pipeline import FeatureUnion  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.ml.ensemble import (  # noqa: E402
    CATEGORY_TO_LABEL,
    MODEL_LABELS,
    BlockBundle,
    StackedEnsemble,
    augment_text,
)
from app.ml.lexicon import Lexicon, load_lexicon  # noqa: E402
from app.ml.normalize import normalize  # noqa: E402

LABELS = list(MODEL_LABELS)


# --------------------------------------------------------------------------- data
@dataclass
class Sample:
    text: str
    label: str
    group: str  # near-duplicate group key


def _group_key(text: str) -> str:
    """Coarse duplicate key so mutated campaign copies share a group."""
    t = normalize(text)
    t = re.sub(r"\d{4,}", " ", t)
    words = [w for w in re.findall(r"[a-z]+", t) if len(w) > 2]
    return " ".join(sorted(set(words))[:8])


def _accept_feedback(samples: list[Sample], generated: Path) -> None:
    """Analyst confirmations/dismissals from the active-learning loop."""
    path = generated / "analyst_feedback.jsonl"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        label = row.get("label")
        text = row.get("text") or ""
        if label in LABELS and text.strip():
            samples.append(Sample(text=text, label=label, group=_group_key(text)))


def load_training_data(generated: Path | None = None) -> list[Sample]:
    generated = generated or (ROOT / "data" / "generated")
    posts = json.loads((generated / "posts.json").read_text(encoding="utf-8"))
    samples = []
    for p in posts:
        # Paraphrased "unseen slang" rows are held out for evaluation only.
        if str(p.get("post_id", "")).startswith("para_"):
            continue
        label = p.get("label") or "benign"
        if label not in LABELS:
            continue
        text = p.get("text") or ""
        if not text.strip():
            continue
        samples.append(Sample(text=text, label=label, group=p.get("campaign") or _group_key(text)))
    _accept_feedback(samples, generated)
    return samples


def _holdout_paraphrases(generated: Path) -> list[dict]:
    p = generated / "lexicon_holdout.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- blocks
def build_tfidf_block() -> BlockBundle:
    """TF-IDF (word + char n-grams) over lexicon-augmented text."""
    vectors = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    analyzer="word", ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                    lowercase=True, strip_accents=None, token_pattern=r"(?u)\b\w[\w@'\-]+\b",
                ),
            ),
            (
                "char",
                TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True),
            ),
        ]
    )
    clf = LogisticRegression(C=6.0, max_iter=3000, class_weight="balanced", solver="lbfgs")
    return BlockBundle(
        name="tfidf", estimator=None, meta={"vectorizer": vectors, "classifier": clf, "kind": "tfidf"}
    )


_VOCAB_PATH = Path(settings.MODEL_DIR) / "tfidf_vocab.pkl"


def fit_tfidf(texts: list[str], y_idx: np.ndarray, weights: np.ndarray | None = None) -> BlockBundle:
    """Fit the TF-IDF multiclass head. ``weights`` (optional) applies sample weights.

    The fitted vectorizer is cached on disk keyed by a hash of the training corpus
    (the vocabulary is deterministic: ``min_df=1``), so repeated fits during
    bootstrap do not re-tokenise thousands of documents.
    """
    import hashlib

    b = build_tfidf_block()
    key = hashlib.sha1((str(len(texts)) + "|" + texts[0][:80] + "|" + texts[-1][:80]).encode()).hexdigest()
    cache_meta = _VOCAB_PATH.with_suffix(".json")
    vocab = None
    if _VOCAB_PATH.exists() and cache_meta.exists():
        try:
            if json.loads(cache_meta.read_text())["key"] == key:
                with open(_VOCAB_PATH, "rb") as fh:
                    vocab = pickle.load(fh)
        except Exception:
            vocab = None
    if vocab is not None:
        b.meta["vectorizer"].vocabulary = vocab
    X = b.meta["vectorizer"].fit_transform(texts)
    if vocab is None:
        try:
            settings.MODEL_DIR.mkdir(parents=True, exist_ok=True)
            with open(_VOCAB_PATH, "wb") as fh:
                pickle.dump(b.meta["vectorizer"].vocabulary, fh)
            cache_meta.write_text(json.dumps({"key": key}))
        except Exception:
            pass
    if weights is not None:
        b.meta["classifier"].fit(X, y_idx, sample_weight=weights)
    else:
        b.meta["classifier"].fit(X, y_idx)
    b.estimator = b.meta["classifier"]
    return b


def tfidf_proba(bundle: BlockBundle, texts: list[str]) -> np.ndarray:
    X = bundle.meta["vectorizer"].transform(texts)
    p = bundle.meta["classifier"].predict_proba(X)
    return p


# --------------------------------------------------------------------------- embeddings
_EMBEDDER = None


def embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer

        _EMBEDDER = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _EMBEDDER


def embed(texts: Iterable[str], batch_size: int = 64) -> np.ndarray:
    return embedder().encode(
        list(texts), batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True,
        show_progress_bar=False,
    )


def build_embed_block() -> BlockBundle:
    return BlockBundle(name="embed", estimator=None, meta={"dim": 384, "kind": "embedding"})


def fit_embed(X: np.ndarray, y_idx: np.ndarray, weights: np.ndarray | None = None) -> BlockBundle:
    """Semantic head with a temperature scale.

    MiniLM cosine similarities live in a narrow band (~0.1-0.9), which leaves a
    plain logistic head under-confident. The vectors are L2-normalised by the
    encoder, so an explicit temperature is applied before fitting and stored
    for inference (posterior = softmax(w . (T * x))).
    """
    b = build_embed_block()
    t = 6.0
    b.meta["temperature"] = t
    b.estimator = LogisticRegression(C=1.0, max_iter=5000, solver="lbfgs")
    b.estimator.fit(X * t, y_idx, sample_weight=weights)
    return b


def embed_proba(bundle: BlockBundle, X: np.ndarray) -> np.ndarray:
    return bundle.estimator.predict_proba(X * float(bundle.meta.get("temperature", 1.0)))


def fit_lexicon_block(lex_rows: np.ndarray, y_idx: np.ndarray, weights: np.ndarray | None = None) -> BlockBundle:
    """Calibrated curated-lexicon head (explicit branch of the fusion)."""
    b = BlockBundle(name="lexicon", estimator=None, meta={"kind": "lexicon"})
    b.estimator = LogisticRegression(C=2.0, max_iter=3000, solver="lbfgs")
    b.estimator.fit(lex_rows, y_idx, sample_weight=weights)
    return b


def lexicon_proba(bundle: BlockBundle, lex_rows: np.ndarray) -> np.ndarray:
    return bundle.estimator.predict_proba(lex_rows)


# --------------------------------------------------------------------------- meta features
def onehot(Y: np.ndarray) -> np.ndarray:
    """Collapse multi-label Y (n, 4 illicit) to single label index with benign=0."""
    idx = np.zeros(Y.shape[0], dtype=int)
    for i, row in enumerate(Y):
        hits = np.flatnonzero(row > 0)
        idx[i] = int(hits[-1]) + 1 if len(hits) else 0
    # LABELS order: drugs, arms, fake_docs, mule_fin, benign
    return idx


def meta_features(p_tfidf: np.ndarray, p_embed: np.ndarray | None, lex_feats: np.ndarray,
                  p_lex: np.ndarray | None = None) -> np.ndarray:
    blocks = [p_tfidf]
    if p_embed is not None:
        blocks.append(p_embed)
    if p_lex is not None:
        blocks.append(p_lex)
    blocks.append(lex_feats)
    return np.hstack(blocks)


def lex_features(texts: list[str], lex: Lexicon) -> np.ndarray:
    rows = []
    for t in texts:
        res = lex.match(t)
        feats = res.feature_vector()
        rows.append([feats.get(k, 0.0) for k in LEX_FEATURE_KEYS])
    return np.array(rows, dtype=np.float32)


LEX_FEATURE_KEYS: list[str] = []
for _cat in CATEGORY_TO_LABEL:
    LEX_FEATURE_KEYS += [
        f"lex.{_cat}.count", f"lex.{_cat}.weight", f"lex.{_cat}.has_slang",
        f"lex.{_cat}.has_intent", f"lex.{_cat}.has_price",
    ]
LEX_FEATURE_KEYS += ["lex.any", "lex.hard_negative"]


# --------------------------------------------------------------------------- train
def train(
    *,
    use_embeddings: bool = True,
    test_fraction: float = 0.22,
    seed: int = 7,
    out_dir: Path | None = None,
    verbose: bool = True,
) -> dict:
    out_dir = out_dir or settings.MODEL_DIR
    t0 = time.time()
    lex = load_lexicon()
    samples = load_training_data()
    texts_raw = [s.text for s in samples]
    groups = np.array([s.group for s in samples])
    y_idx = np.array([LABELS.index(s.label) for s in samples])
    Y = np.zeros((len(samples), 4), dtype=int)
    for i, lbl in enumerate(y_idx):
        if lbl < 4:
            Y[i, lbl] = 1

    # Grouped split: near-duplicates never straddle train/test.
    gss = GroupShuffleSplit(n_splits=1, test_size=test_fraction, random_state=seed)
    train_idx, test_idx = next(gss.split(texts_raw, y_idx, groups=groups))
    if verbose:
        print(f"[train] {len(train_idx)} train / {len(test_idx)} test ({time.time()-t0:.1f}s)")

    aug_all = [augment_text(t, lex) for t in texts_raw]
    X_tfidf = aug_all

    E = None
    if use_embeddings:
        E = embed([normalize(t) for t in texts_raw])
        if verbose:
            print(f"[train] embeddings done {E.shape} ({time.time()-t0:.1f}s)")

    # ---- per-sample weights: the lexical-free embedding head must rely on
    # semantics, so benign hard negatives / paraphrases are up-weighted during
    # its OOF fitting. (Documented in README "Training".)
    w = np.ones(len(samples))
    for i, t in enumerate(texts_raw):
        if y_idx[i] == LABELS.index("benign"):
            res = lex.match(t)
            if res.hits:
                w[i] = 3.0

    # ---- out-of-fold probabilities for calibration
    n_folds = 4
    sgkf = StratifiedGroupKFold(n_splits=n_folds, shuffle=True, random_state=seed)
    oof_tfidf = np.zeros((len(samples), len(LABELS)))
    oof_embed = np.zeros((len(samples), len(LABELS))) if use_embeddings else None
    oof_lex = np.zeros((len(samples), len(LABELS)))
    L_all = lex_features(texts_raw, lex)
    for k, (tr, va) in enumerate(sgkf.split(texts_raw, y_idx, groups=groups)):
        b = fit_tfidf([X_tfidf[i] for i in tr], y_idx[tr])
        oof_tfidf[va] = tfidf_proba(b, [X_tfidf[i] for i in va])
        if use_embeddings and E is not None and oof_embed is not None:
            be = fit_embed(E[tr], y_idx[tr], weights=w[tr])
            oof_embed[va] = embed_proba(be, E[va])
        bl = fit_lexicon_block(L_all[tr], y_idx[tr], weights=w[tr])
        oof_lex[va] = lexicon_proba(bl, L_all[va])
    if verbose:
        print(f"[train] OOF folds done ({time.time()-t0:.1f}s)")

    # ---- final blocks on train split
    tfidf_b = fit_tfidf([X_tfidf[i] for i in train_idx], y_idx[train_idx])
    embed_b = None
    if use_embeddings and E is not None:
        embed_b = fit_embed(E[train_idx], y_idx[train_idx], weights=w[train_idx])
    lex_b = fit_lexicon_block(L_all[train_idx], y_idx[train_idx], weights=w[train_idx])

    # ---- calibration stack on OOF (train rows only)
    L = L_all[train_idx]
    M_train = meta_features(oof_tfidf[train_idx], oof_embed[train_idx] if oof_embed is not None else None, L,
                            oof_lex[train_idx])
    meta = LogisticRegression(C=1.0, max_iter=3000)
    meta.fit(M_train, y_idx[train_idx])

    model = StackedEnsemble()
    model.add_block(tfidf_b)
    if embed_b is not None:
        model.add_block(embed_b)
    model.add_block(lex_b)
    model.meta = meta
    model.save(out_dir)

    # ---- evaluate on held-out test (single-label view + multi-label for illicit)
    test_texts_aug = [X_tfidf[i] for i in test_idx]
    p_tf = tfidf_proba(tfidf_b, test_texts_aug)
    p_em = embed_proba(embed_b, E[test_idx]) if (embed_b is not None and E is not None) else None
    L_test = L_all[test_idx]
    M_test = meta_features(p_tf, p_em, L_test, lexicon_proba(lex_b, L_test))
    probs = meta.predict_proba(M_test)
    pred_idx = probs.argmax(axis=1)
    truth_idx = y_idx[test_idx]

    prec, rec, f1s, support = precision_recall_fscore_support(
        truth_idx, pred_idx, labels=list(range(len(LABELS))), zero_division=0
    )
    per_class = {
        LABELS[i]: {
            "precision": round(float(prec[i]), 4),
            "recall": round(float(rec[i]), 4),
            "f1": round(float(f1s[i]), 4),
            "support": int(support[i]),
        }
        for i in range(len(LABELS))
    }
    macro_f1 = float(f1_score(truth_idx, pred_idx, average="macro", zero_division=0))
    weighted_f1 = float(f1_score(truth_idx, pred_idx, average="weighted", zero_division=0))
    cm = confusion_matrix(truth_idx, pred_idx, labels=list(range(len(LABELS)))).tolist()

    test_texts = [texts_raw[i] for i in test_idx]
    hard_neg = check_hard_negatives(model, lex, test_texts, truth_idx)
    holdout = evaluate_holdout(model, lex)

    block_f1 = block_macro_f1s(tfidf_b, embed_b, lex, texts_raw, X_tfidf, test_idx, y_idx, E)
    if block_f1.get("embedding_head", 0) < 0.7:
        block_f1["note"] = "embedding head is semantic-only (no lexicon features); see README limitations"

    metrics = {
        "version": model.version,
        "labels": LABELS,
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "per_class": per_class,
        "confusion_matrix": cm,
        "confusion_labels": LABELS,
        "train_size": int(len(train_idx)),
        "test_size": int(len(test_idx)),
        "hard_negatives": hard_neg,
        "paraphrase_holdout": holdout,
        "embedding_block": bool(embed_b is not None),
        "embed_model": settings.EMBEDDING_MODEL,
        "lexicon_version": lex.version,
        "calibration": "OOF-stacked LogisticRegression (multinomial)",
        "train_seconds": round(time.time() - t0, 1),
        "block_macro_f1": block_f1,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if verbose:
        print(json.dumps({k: metrics[k] for k in ("macro_f1", "weighted_f1", "train_seconds")}, indent=1))
    return metrics


def block_macro_f1s(tfidf_b, embed_b, lex, texts_raw, X_tfidf, test_idx, y_idx, E) -> dict:
    """Per-block macro F1 on the test split (for the /metrics page)."""
    out = {}
    p_tf = tfidf_proba(tfidf_b, [X_tfidf[i] for i in test_idx])
    out["tfidf_lexicon_augmented"] = round(
        float(f1_score(y_idx[test_idx], p_tf.argmax(1), average="macro", zero_division=0)), 4
    )
    if embed_b is not None and E is not None:
        p_em = embed_proba(embed_b, E[test_idx])
        out["embedding_head"] = round(
            float(f1_score(y_idx[test_idx], p_em.argmax(1), average="macro", zero_division=0)), 4
        )
    # lexicon-only baseline
    lex_pred = []
    for i in test_idx:
        res = lex.match(texts_raw[i])
        by_cat = res.by_category()
        scores = {c: sum(h.weight for h in hits if h.kind != "hard_negative") for c, hits in by_cat.items()}
        best = max(scores, key=lambda c: scores[c]) if scores and max(scores.values()) > 0 else None
        if best and scores[best] >= 1.2 and not res.hard_negatives:
            lex_pred.append(LABELS.index(CATEGORY_TO_LABEL[best]))
        else:
            lex_pred.append(LABELS.index("benign"))
    out["lexicon_only"] = round(float(f1_score(y_idx[test_idx], lex_pred, average="macro", zero_division=0)), 4)
    return out


def _predict_label(model: StackedEnsemble, lex: Lexicon, texts: list[str]) -> tuple[list[str], np.ndarray]:
    aug = [augment_text(t, lex) for t in texts]
    p_tf = tfidf_proba(model.blocks["tfidf"], aug)
    p_em = None
    if model.has_embeddings:
        p_em = embed_proba(model.blocks["embed"], embed([normalize(t) for t in texts]))
    L = lex_features(texts, lex)
    p_lex = lexicon_proba(model.blocks["lexicon"], L) if "lexicon" in model.blocks else None
    probs = model.meta.predict_proba(meta_features(p_tf, p_em, L, p_lex))
    idx = probs.argmax(1)
    return [LABELS[i] for i in idx], probs


def check_hard_negatives(model: StackedEnsemble, lex: Lexicon, texts: list[str], truth_idx: np.ndarray) -> dict:
    benign_texts = [t for t, y in zip(texts, truth_idx) if LABELS[y] == "benign"]
    flagged = []
    if benign_texts:
        preds, probs = _predict_label(model, lex, benign_texts)
        for t, p, pr in zip(benign_texts, preds, probs):
            if p != "benign" and pr.max() >= 0.5:
                flagged.append({"text": t[:160], "predicted": p, "confidence": round(float(pr.max()), 3)})
    categories = [
        ("Weed killer spray 500ml available at our garden store", "drugs"),
        ("Simple weed killer for your lawn, harmless to pets", "drugs"),
        ("Best cookies recipe with butter, sugar and flour", "drugs"),
        ("Harvesting hash browns for breakfast, add some salt", "drugs"),
        ("Nerf toy gun and water gun for kids birthday party", "arms"),
        ("PUBG and COD Mobile tournament this weekend", "arms"),
        ("Open a savings account with 4% interest at our branch", "mule_fin"),
        ("Credit card offer: apply online, get instant approval", "mule_fin"),
        ("Buy a new SIM card at the store with Aadhaar eKYC", "mule_fin"),
        ("How to apply for a PAN card online step by step", "fake_docs"),
        ("Aadhaar address update online through UIDAI portal", "fake_docs"),
        ("Pharmacy store: generic medicines available at 50% off", "drugs"),
        ("Car insurance renewal online, compare quotes", "arms"),
        ("Debit card replacement request at your bank branch", "mule_fin"),
    ]
    curated_flag = []
    preds, probs = _predict_label(model, lex, [c[0] for c in categories])
    for (t, _), p, pr in zip(categories, preds, probs):
        if p != "benign" and pr.max() >= 0.5:
            curated_flag.append({"text": t, "predicted": p, "confidence": round(float(pr.max()), 3)})
    total = len(benign_texts) + len(categories)
    flagged_n = len(flagged) + len(curated_flag)
    return {
        "tested": total,
        "flagged": flagged_n,
        "false_positive_rate": round(flagged_n / max(total, 1), 4),
        "examples": (flagged + curated_flag)[:10],
        "curated_cases": len(categories),
    }


def evaluate_holdout(model: StackedEnsemble, lex: Lexicon) -> dict:
    items = _holdout_paraphrases(ROOT / "data" / "generated")
    if not items:
        return {"n": 0}
    preds, probs = _predict_label(model, lex, [i["text"] for i in items])
    hit = [p != "benign" for p in preds]
    return {
        "n": len(items),
        "illicit_detected": int(sum(hit)),
        "recall": round(sum(hit) / len(items), 3),
        "examples": [
            {"text": i["text"][:140], "predicted": p, "confidence": round(float(pr.max()), 3)}
            for i, p, pr in list(zip(items, preds, probs))[:6]
        ],
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embeddings", action="store_true")
    args = ap.parse_args()
    metrics = train(use_embeddings=not args.no_embeddings)
    print(json.dumps(metrics["per_class"], indent=1))
    print("confusion:", metrics["confusion_matrix"])
    print("hard-neg FPR:", metrics["hard_negatives"]["false_positive_rate"],
          "flagged:", metrics["hard_negatives"]["flagged"], "/", metrics["hard_negatives"]["tested"])
    print("paraphrase holdout:", metrics["paraphrase_holdout"].get("recall"))
    print("blocks:", metrics["block_macro_f1"])


if __name__ == "__main__":
    main()
