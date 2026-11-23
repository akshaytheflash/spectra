"""Pre-compute post embeddings for instant semantic/free-text search.

Stores ``backend/artifacts/embed_cache.npz`` (post id -> embedding). The API
loads it lazily on the first semantic query and keeps adding new posts in
memory, so the UI never has to embed thousands of posts on demand.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Post  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.ml import embed_cache  # noqa: E402
from app.ml.normalize import normalize  # noqa: E402
from app.ml.train import embed  # noqa: E402

INDEX_PATH = settings.ARTIFACT_DIR / "embed_cache.npz"


def main() -> None:
    t0 = time.time()
    with session_scope() as db:
        rows = db.execute(select(Post.id, Post.text)).all()
    pairs = [(str(pid), normalize(text or "")) for pid, text in rows if (text or "").strip()]
    if not pairs:
        print("[search-index] no posts to index")
        return
    embed_cache.ensure(pairs, embed)
    mat = embed_cache.matrix(pairs)
    ids = np.array([pid for pid, _ in pairs])
    hashes = np.array([_h(text) for _, text in pairs])
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(INDEX_PATH, ids=ids, hashes=hashes, vectors=mat.astype(np.float32))
    print(f"[search-index] embedded {len(pairs)} posts in {time.time() - t0:.1f}s -> {INDEX_PATH.name}")


def _h(text: str) -> str:
    import hashlib

    return hashlib.sha1(text[:400].encode("utf-8", "ignore")).hexdigest()[:12]


if __name__ == "__main__":
    main()
