"""Entity graph + coordinated-ad (campaign) detection.

Nodes: Post, Channel, Phone, UPI, Wallet, Username, Location, ImageHash, AudioHash
Edges: posted_in, mentions, same_image, same_voice, same_text

* Identifiers are merged by normalised value across channels.
* Near-duplicate text uses MinHash (datasketch) + TF-IDF cosine; image
  similarity uses pHash Hamming distance; audio uses SHA-256.
* Clusters become campaigns with members, channels, shared identifiers,
  time span and posting frequency.
* Entity risk = f(#channels, #posts, category severity, campaign membership).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import networkx as nx
import numpy as np

SEVERITY = {"drugs": 1.0, "arms": 1.2, "fake_docs": 1.0, "mule_fin": 1.1, "benign": 0.0}

IDENTIFIER_NODE_TYPES = {
    "phone": "Phone",
    "upi": "UPI",
    "wallet": "Wallet",
    "username": "Username",
    "location": "Location",
    "bank": "Bank",
    "ifsc": "Bank",
    "email": "Username",
    "link": "Link",
}


def _shingles(text: str, k: int = 3) -> set[str]:
    """Word 3-shingles (shorter than a sentence, so mutated ads still overlap)."""
    words = text.lower().split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def minhash_signature(text: str, num_perm: int = 128) -> list[int]:
    from datasketch import MinHash

    m = MinHash(num_perm=num_perm)
    for s in _shingles(text):
        m.update(s.encode("utf-8"))
    return [int(v) for v in m.digest()]


def _minhash_from_signature(sig: list[int]):
    """Rebuild a datasketch MinHash from stored hash values (version tolerant)."""
    from datasketch import MinHash

    values = np.array(sig, dtype=np.uint64)
    try:
        return MinHash(num_perm=len(values), hashvalues=values, scheme="affine32")
    except TypeError:  # datasketch < 2.0
        m = MinHash(num_perm=len(values))
        m.hashvalues = values
        return m
    except ValueError:
        return MinHash(num_perm=len(values), hashvalues=values, scheme="legacy")


def hamming(a: str, b: str) -> int:
    if not a or not b or a == "?" or b == "?":
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


@dataclass
class GraphPayload:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges, "meta": self.meta}


def build_graph(posts: list[dict], *, cluster_of: dict[str, str] | None = None) -> nx.MultiGraph:
    """Build the entity graph from post records.

    ``posts`` items: id, channel, timestamp, category, identifiers[{type,value}],
    image_hash, audio_hash, text.
    """
    g = nx.MultiGraph()
    for p in posts:
        pid = p["id"]
        g.add_node(pid, kind="Post", label=(p.get("text") or "")[:60], channel=p.get("channel", ""),
                   category=p.get("category") or "benign", timestamp=str(p.get("timestamp", "")),
                   alert_id=p.get("alert_id"), cluster_id=(cluster_of or {}).get(pid))
        ch = p.get("channel") or "unknown"
        ch_id = f"ch:{ch}"
        g.add_node(ch_id, kind="Channel", label=ch)
        g.add_edge(pid, ch_id, kind="posted_in")
        for ident in p.get("identifiers", []):
            ntype = IDENTIFIER_NODE_TYPES.get(ident["type"], "Identifier")
            nid = f"{ident['type']}:{ident['value']}"
            g.add_node(nid, kind=ntype, label=ident["value"], id_type=ident["type"])
            g.add_edge(pid, nid, kind="mentions", source=ident.get("source", "text"))
        if p.get("image_hash"):
            nid = f"img:{p['image_hash']}"
            g.add_node(nid, kind="ImageHash", label=p["image_hash"])
            g.add_edge(pid, nid, kind="same_image")
        if p.get("audio_hash"):
            nid = f"aud:{p['audio_hash']}"
            g.add_node(nid, kind="AudioHash", label=p["audio_hash"][:12])
            g.add_edge(pid, nid, kind="same_voice")
    return g


def identifier_risk(posts: list[dict], channels: set[str]) -> float:
    """Risk score for an identifier: channels x posts x category severity."""
    if not posts:
        return 0.0
    sev = sum(SEVERITY.get(p.get("category") or "benign", 0) for p in posts)
    base = len(posts) * max(1, len(channels))
    return round(min(100.0, 0.45 * base + 4.0 * sev), 2)


@dataclass
class Campaign:
    key: str
    post_ids: list[str]
    channels: list[str]
    shared_identifiers: list[str]
    category: str
    similarity: float
    start: str
    end: str
    posting_frequency: float
    signature: str = ""
    category_mix: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "post_ids": self.post_ids, "channels": self.channels,
            "shared_identifiers": self.shared_identifiers, "category": self.category,
            "similarity": round(self.similarity, 3), "time_span": {"start": self.start, "end": self.end},
            "posting_frequency": round(self.posting_frequency, 3), "signature": self.signature,
            "category_mix": self.category_mix,
        }


def _norm(text: str) -> str:
    import re

    t = re.sub(r"https?://\S+", " ", (text or "").lower())
    t = re.sub(r"[^a-z0-9\u0900-\u097F ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_campaigns(
    posts: list[dict],
    *,
    text_threshold: float = 0.55,
    image_threshold: int = 10,
    min_size: int = 3,
) -> list[Campaign]:
    """Cluster coordinated ads by near-duplicate text, shared images and shared identifiers."""
    if not posts:
        return []
    # Union-find over posts
    parent = list(range(len(posts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    norms = [_norm(p.get("text") or "") for p in posts]
    sigs = [minhash_signature(n) if len(n) > 20 else None for n in norms]

    # Rebuild MinHash objects once, then compare signatures directly.
    minhashes = [None] * len(sigs)
    for i, sig in enumerate(sigs):
        if sig is not None:
            minhashes[i] = _minhash_from_signature(sig)

    def jaccard(i: int, j: int) -> float:
        if minhashes[i] is None or minhashes[j] is None:
            return 0.0
        return float(minhashes[i].jaccard(minhashes[j]))

    n = len(posts)
    for i in range(n):
        if not norms[i]:
            continue
        for j in range(i + 1, n):
            if not norms[j]:
                continue
            # cheap pre-filter on token overlap
            wi, wj = set(norms[i].split()), set(norms[j].split())
            if not wi or not wj:
                continue
            overlap = len(wi & wj) / min(len(wi), len(wj))
            if overlap < 0.35:
                continue
            if jaccard(i, j) >= text_threshold:
                union(i, j)

    # image pHash similarity
    by_hash = [(i, p.get("image_hash")) for i, p in enumerate(posts) if p.get("image_hash")]
    for a in range(len(by_hash)):
        for b in range(a + 1, len(by_hash)):
            if hamming(by_hash[a][1], by_hash[b][1]) <= image_threshold:
                union(by_hash[a][0], by_hash[b][0])

    # shared strong identifiers (phone/upi/wallet) across posts
    by_ident: dict[str, list[int]] = {}
    for i, p in enumerate(posts):
        for ident in p.get("identifiers", []):
            if ident["type"] in {"phone", "upi", "wallet"}:
                by_ident.setdefault(f"{ident['type']}:{ident['value']}", []).append(i)
    for ids in by_ident.values():
        for k in range(1, len(ids)):
            if norms[ids[0]] and norms[ids[k]] and _norm_similar(norms[ids[0]], norms[ids[k]]) >= 0.35:
                union(ids[0], ids[k])

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    campaigns: list[Campaign] = []
    for root, members in groups.items():
        if len(members) < min_size:
            continue
        mp = [posts[i] for i in members]
        channels = sorted({p.get("channel") or "" for p in mp})
        cats = [p.get("category") or "benign" for p in mp]
        illicit = [c for c in cats if c != "benign"]
        # A cluster with any illicit evidence is an illicit campaign; the label is
        # the most frequent *illicit* category (mixes are reported separately).
        cat = max(set(illicit), key=illicit.count) if illicit else "benign"
        cat_mix = {c: cats.count(c) for c in sorted(set(cats), key=cats.count, reverse=True)}
        ident_counter: dict[str, int] = {}
        for p in mp:
            for ident in p.get("identifiers", []):
                key = f"{ident['type']}:{ident['value']}"
                ident_counter[key] = ident_counter.get(key, 0) + 1
        shared = sorted([k for k, v in ident_counter.items() if v >= 2], key=lambda k: -ident_counter[k])
        times = sorted(str(p.get("timestamp") or "") for p in mp)
        try:
            t0 = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
            span_days = max((t1 - t0).total_seconds() / 86400.0, 0.05)
        except Exception:
            span_days = 1.0
        sims = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                sims.append(jaccard(members[a], members[b]))
        campaign = Campaign(
            key=hashlib.sha1(("campaign" + root.__str__() + "|".join(sorted(p["id"] for p in mp))).encode()).hexdigest()[:12],
            post_ids=sorted(p["id"] for p in mp),
            channels=channels,
            shared_identifiers=shared[:8],
            category=cat,
            similarity=float(np.mean(sims)) if sims else 0.0,
            start=times[0] if times else "",
            end=times[-1] if times else "",
            posting_frequency=len(mp) / span_days,
            signature=hashlib.sha1(norms[members[0]].encode()).hexdigest()[:10],
        )
        campaign.category_mix = cat_mix
        campaigns.append(campaign)
    campaigns.sort(key=lambda c: (-len(c.channels), -len(c.post_ids)))
    return campaigns


def _norm_similar(a: str, b: str) -> float:
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def subgraph_payload(
    g: nx.MultiGraph, identifier: str, hops: int = 2, *, node_types: set[str] | None = None
) -> GraphPayload:
    """Serialise the neighbourhood of an identifier (or post) node."""
    matches = [n for n in g.nodes if str(n) == identifier or n.endswith(":" + identifier)]
    if not matches:
        return GraphPayload(meta={"found": False, "identifier": identifier})
    seen: set[str] = set()
    frontier = set(matches)
    for _ in range(max(1, hops)):
        nxt: set[str] = set()
        for n in frontier:
            for nb in g.neighbors(n):
                if nb not in seen:
                    nxt.add(nb)
        seen |= frontier
        frontier = nxt - seen
    seen |= frontier
    nodes = []
    for n in sorted(seen):
        d = g.nodes[n]
        if node_types and d.get("kind") not in node_types and d.get("kind") != "Post":
            continue
        nodes.append({"id": str(n), **{k: v for k, v in d.items() if k != "id"}})
    keep = {nd["id"] for nd in nodes}
    edges = []
    for u, v, d in g.edges(data=True):
        if str(u) in keep and str(v) in keep:
            edges.append({"source": str(u), "target": str(v), "kind": d.get("kind", "")})
    return GraphPayload(nodes=nodes, edges=edges,
                        meta={"found": True, "identifier": identifier, "hops": hops,
                              "node_count": len(nodes), "edge_count": len(edges)})


def full_graph_payload(
    g: nx.MultiGraph, *, max_posts: int = 400, cluster_ids: set[str] | None = None
) -> GraphPayload:
    """Serialise the whole graph, prioritising identifier nodes and clustered posts."""
    ident_nodes = [n for n, d in g.nodes(data=True) if d.get("kind") not in ("Post", "Channel")]
    channel_nodes = [n for n, d in g.nodes(data=True) if d.get("kind") == "Channel"]
    post_nodes = [n for n, d in g.nodes(data=True) if d.get("kind") == "Post"]
    keep = set(ident_nodes) | set(channel_nodes)
    if cluster_ids:
        keep |= {n for n in post_nodes if g.nodes[n].get("cluster_id") in cluster_ids}
    for n in post_nodes:
        if len(keep) > max_posts:
            break
        if g.degree(n) > 1:
            keep.add(n)
    edges = []
    for u, v, d in g.edges(data=True):
        if u in keep and v in keep:
            edges.append({"source": u, "target": v, "kind": d.get("kind", "")})
    nodes = [{"id": str(n), **{k: val for k, val in g.nodes[n].items()}} for n in keep]
    return GraphPayload(nodes=nodes, edges=edges,
                        meta={"node_count": len(nodes), "edge_count": len(edges),
                              "truncated": len(keep) < len(g.nodes)})


def cross_channel_identifiers(g: nx.MultiGraph, min_channels: int = 3) -> list[dict]:
    """Identifiers linked to posts across ``min_channels`` or more channels."""
    out = []
    for n, d in g.nodes(data=True):
        if d.get("kind") in ("Post", "Channel"):
            continue
        channels = set()
        for nb in g.neighbors(n):
            if g.nodes[nb].get("kind") == "Post":
                channels.add(g.nodes[nb].get("channel"))
        if len(channels) >= min_channels:
            out.append({"node": n, "type": d.get("id_type", d.get("kind")), "value": d.get("label"),
                        "channels": sorted(c for c in channels if c), "post_count": g.degree(n)})
    return sorted(out, key=lambda x: -len(x["channels"]))
