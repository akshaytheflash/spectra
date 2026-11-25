"""Graph, stats, map, search and model-metrics endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Alert, ModelRun, Post
from app.db.session import get_db
from app.pipeline import graph as graph_mod
from app.schemas import GraphResponse, SearchResponse
from app.services import (
    campaigns_for_post_alerts,
    load_posts_with_identifiers,
    map_points,
    overview_stats,
    refresh_campaigns,
    search_posts,
)

router = APIRouter(tags=["analytics"])


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    identifier: str | None = None,
    hops: int = Query(2, ge=1, le=3),
    node_types: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Entity graph; pass ``identifier`` for a 1-3 hop neighbourhood."""
    posts = load_posts_with_identifiers(db)
    cluster_of = campaigns_for_post_alerts(db)
    g = graph_mod.build_graph(posts, cluster_of=cluster_of)
    allowed = set(node_types.split(",")) if node_types else None
    if identifier:
        payload = graph_mod.subgraph_payload(g, identifier, hops, node_types=allowed)
    else:
        payload = graph_mod.full_graph_payload(g, cluster_ids=set(cluster_of.values()) if cluster_of else None)
    data = payload.as_dict()
    data["meta"]["cross_channel"] = graph_mod.cross_channel_identifiers(g, min_channels=3)
    data["meta"]["node_type_counts"] = _count_types(g)
    return data


def _count_types(g) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, d in g.nodes(data=True):
        out[d.get("kind", "?")] = out.get(d.get("kind", "?"), 0) + 1
    return out


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    """Dashboard KPIs and chart series."""
    refresh_campaigns(db)
    return overview_stats(db)


@router.get("/map")
def map_data(db: Session = Depends(get_db)) -> dict:
    return {"points": map_points(db)}


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    channel: str | None = None,
    category: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str = Query("auto", pattern="^(auto|hybrid|identifier|semantic|text)$"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    return search_posts(db, q, channel=channel, category=category, source=source,
                        date_from=date_from, date_to=date_to, mode=mode, limit=limit)


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict:
    """Model metrics for the /metrics page (persisted from the last training run)."""
    path = settings.MODEL_DIR / "metrics.json"
    file_metrics = json.loads(path.read_text()) if path.exists() else {}
    run = db.execute(select(ModelRun).order_by(ModelRun.trained_at.desc())).scalar_one_or_none()
    return {
        "model": file_metrics,
        "latest_run": {
            "id": run.id, "version": run.version, "trained_at": run.trained_at,
            "metrics": run.metrics, "confusion": run.confusion, "labels": run.labels,
            "train_size": run.train_size, "test_size": run.test_size,
        } if run else None,
        "feedback_count": len(db.execute(select(Alert).where(Alert.status.in_(["confirmed", "dismissed"]))).scalars().all()),
    }


@router.get("/channels/names")
def channels_list(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Post.channel)).scalars().all()
    return {"channels": sorted({r for r in rows if r})}
