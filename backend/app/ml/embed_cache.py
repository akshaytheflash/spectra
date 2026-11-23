"""In-memory post embedding cache for semantic search.

Encoder output for a post never changes, so search re-embeds only new posts.
The cache is process-local (fine for the single-process demo) and is keyed by
post id + text hash; it is bounded so long ingest sessions stay small.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Iterable

import numpy as np

_lock = threading.Lock()
_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
MAX_ITEMS = 6000


def _key(post_id: str, text: str) -> str:
    return f"{post_id}:{hashlib.sha1(text[:400].encode('utf-8', 'ignore')).hexdigest()[:12]}"


_loaded_index = False


def load_index() -> None:
    """Load pre-computed embeddings (``backend/artifacts/embed_cache.npz``) once."""
    global _loaded_index
    if _loaded_index:
        return
    _loaded_index = True
    from app.core.config import settings

    path = settings.ARTIFACT_DIR / "embed_cache.npz"
    if not path.exists():
        return
    try:
        data = np.load(path, allow_pickle=False)
        with _lock:
            for pid, h, vec in zip(data["ids"], data["hashes"], data["vectors"]):
                _cache[f"{pid}:{h}"] = vec.astype(np.float32)
    except Exception:
        return


def ensure(posts: Iterable[tuple[str, str]], embed_fn) -> None:
    """Embed any posts whose text is not cached yet (one batched encode)."""
    load_index()
    missing: list[tuple[str, str]] = []
    with _lock:
        for pid, text in posts:
            if _key(pid, text) not in _cache:
                missing.append((pid, text))
    if not missing:
        return
    vecs = embed_fn([t[:800] for _, t in missing])
    with _lock:
        for (pid, text), vec in zip(missing, vecs):
            _cache[_key(pid, text)] = np.asarray(vec, dtype=np.float32)
        while len(_cache) > MAX_ITEMS:
            _cache.popitem(last=False)


def matrix(posts: list[tuple[str, str]]) -> np.ndarray | None:
    """Stacked, L2-normalised embedding matrix in the given order."""
    load_index()
    with _lock:
        rows = [_cache.get(_key(pid, text)) for pid, text in posts]
    if any(r is None for r in rows):
        return None
    m = np.vstack([r for r in rows if r is not None])
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norms, 1e-8)


def size() -> int:
    with _lock:
        return len(_cache)


def clear() -> None:
    with _lock:
        _cache.clear()
