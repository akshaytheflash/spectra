"""Hybrid classification engine: one score, one explanation.

Signals fused by a calibrated stack:

1. **Curated lexicon features** (``data/lexicon.yaml``): per-category counts,
   weights, slang/emoji/intent/price presence and hard-negatives.
2. **TF-IDF multi-label classifier**: char (2-5) + word (1-2) n-grams, one
   One-vs-Rest LogisticRegression per class trained on the *lexicon-augmented*
   text (so devanagari/leetspeak forms share n-grams).
3. **Multilingual sentence-embedding head**: MiniLM embeddings of the raw text
   + a logistic head, which generalises to unseen slang and paraphrases.

The three blocks are concatenated and a meta LogisticRegression (trained on
out-of-fold probabilities) produces the final calibrated class probabilities.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from app.core.config import settings
from app.ml.lexicon import MODEL_LABELS, Lexicon
from app.ml.normalize import normalize

# Category -> ML label (4 illicit classes + benign).
CATEGORY_TO_LABEL = {
    "drugs": "drugs",
    "arms": "arms",
    "fake_docs": "fake_docs",
    "mule_account": "mule_fin",
    "atm_card": "mule_fin",
    "sim_card": "mule_fin",
    "net_banking_kit": "mule_fin",
}
LABEL_TO_CATEGORIES: dict[str, list[str]] = {lbl: [] for lbl in MODEL_LABELS}
for _c, _l in CATEGORY_TO_LABEL.items():
    LABEL_TO_CATEGORIES[_l].append(_c)

MODEL_VERSION = "spectra-hybrid-v1"
EMBED_DIM = 384


def label_of(category: str) -> str:
    return CATEGORY_TO_LABEL.get(category, "benign")


def augment_text(text: str, lex: Lexicon) -> str:
    """Append explicit category tokens for lexicon hits.

    These act as strong features for the linear models and make the TF-IDF head
    robust to spelling variants (``w33d`` -> ``__drugs_weed``).
    """
    res = lex.match(text)
    tokens: list[str] = []
    for hit in res.hits:
        if hit.kind == "hard_negative":
            tokens.append(f"__neg_{hit.category}")
            continue
        tokens.append(f"__{label_of(hit.category)}_{hit.kind}")
        tokens.append(f"__cat_{hit.category}")
    return normalize(text) + " " + " ".join(sorted(set(tokens)))


@dataclass
class BlockBundle:
    """A fitted feature block with its estimator."""

    name: str
    estimator: Any
    meta: dict = field(default_factory=dict)


class StackedEnsemble:
    """Stores the fitted blocks and the meta-learner."""

    def __init__(self) -> None:
        self.blocks: dict[str, BlockBundle] = {}
        self.meta: Any = None
        self.labels: tuple[str, ...] = MODEL_LABELS
        self.version = MODEL_VERSION
        self.embed_model_name = settings.EMBEDDING_MODEL
        self._embed_fn: Any = None

    # ---------------------------------------------------------------- blocks
    def add_block(self, bundle: BlockBundle) -> None:
        self.blocks[bundle.name] = bundle

    @property
    def has_embeddings(self) -> bool:
        return "embed" in self.blocks

    # ---------------------------------------------------------------- persist
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, bundle in self.blocks.items():
            with open(directory / f"block_{name}.pkl", "wb") as fh:
                pickle.dump({"estimator": bundle.estimator, "meta": bundle.meta}, fh)
        with open(directory / "meta.pkl", "wb") as fh:
            pickle.dump({"meta": self.meta, "labels": self.labels, "version": self.version,
                         "embed_model_name": self.embed_model_name}, fh)

    @classmethod
    def load(cls, directory: Path) -> "StackedEnsemble":
        inst = cls()
        meta_path = directory / "meta.pkl"
        if not meta_path.exists():
            raise FileNotFoundError(f"No trained model in {directory}")
        with open(meta_path, "rb") as fh:
            m = pickle.load(fh)
        inst.meta = m["meta"]
        inst.labels = tuple(m["labels"])
        inst.version = m.get("version", MODEL_VERSION)
        inst.embed_model_name = m.get("embed_model_name", settings.EMBEDDING_MODEL)
        for p in directory.glob("block_*.pkl"):
            name = p.stem.replace("block_", "")
            with open(p, "rb") as fh:
                d = pickle.load(fh)
            inst.add_block(BlockBundle(name=name, estimator=d["estimator"], meta=d.get("meta", {})))
        return inst

    def summary(self) -> dict:
        return {
            "version": self.version,
            "labels": list(self.labels),
            "blocks": {k: b.meta for k, b in self.blocks.items()},
            "has_embeddings": self.has_embeddings,
        }
