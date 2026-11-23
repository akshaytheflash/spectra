"""OCR robustness benchmark + regression guard.

Scoring is measured per image, not per document: a barcode-shaped advert is
judged on how many of its text lines and identifiers were recovered.  The
benchmark is also used for the `/metrics` OCR panel and by the unit test
(`test_ocr_identifier_accuracy_gate`).

Run:  python scripts/bench_ocr.py                 # bundled samples
      python scripts/bench_ocr.py --dir data/generated/images --limit 60
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ml.extractors import extract_all  # noqa: E402
from app.pipeline import vision  # noqa: E402

IDENT_RE = re.compile(r"(\+?91[-\s]?\d{5}[-\s]?\d{5}|\b[6-9]\d{9}\b|[a-z0-9._-]+@[a-z]{2,})", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def expected_from_poster(p: dict) -> dict:
    """Ground truth for a generated synthetic media post."""
    seller = p.get("seller") or {}
    lines = []
    if p.get("label") == "drugs":
        lines = ["Fresh stock", "pure quality", "Home delivery"]
    elif p.get("label") == "mule_fin":
        lines = ["KYC done account", "passbook", "Daily commission"]
    elif p.get("label") == "arms":
        lines = ["9mm", "cartridges", "Lowest price"]
    elif p.get("label") == "fake_docs":
        lines = ["Aadhaar", "PAN", "Same day delivery"]
    return {
        "identifiers": [v for v in (seller.get("phone"), seller.get("upi")) if v],
        "lines": lines,
    }


def load_sample(limit: int) -> list[dict]:
    posts = json.loads((ROOT / "data" / "generated" / "posts.json").read_text(encoding="utf-8"))
    out = []
    for p in posts:
        if not p.get("image_path"):
            continue
        if p.get("image_kind") == "poster" or p.get("qr_payload"):
            out.append(p)
        if len(out) >= limit:
            break
    return out


def score_one(p: dict) -> dict:
    path = ROOT / "data" / "generated" / p["image_path"]
    t0 = time.time()
    res = vision.analyze_image(path, max_ocr_passes=1)
    secs = time.time() - t0
    ocr_norm = _norm(res.text)
    exp = expected_from_poster(p)
    # identifier recall: exact digit substring or VPA local-part present
    id_hits = 0
    for ident in exp["identifiers"]:
        if "@" in ident:
            local = _norm(ident.split("@")[0])
            got = any(f.type == "upi" and f.value.split("@")[0] == ident.split("@")[0] for f in extract_all(res.text))
            got = got or (len(local) >= 4 and local in _norm("".join(q.data for q in res.qr_codes) + res.text))
        else:
            got = _norm(ident)[-7:] in ocr_norm
        id_hits += int(got)
    line_hits = sum(1 for ln in exp["lines"] if _norm(ln)[:8] in ocr_norm)
    return {
        "file": p["image_path"],
        "variant": p.get("media_variant"),
        "kind": p.get("image_kind") or ("qr" if p.get("qr_payload") else "chat"),
        "seconds": round(secs, 2),
        "identifier_recall": id_hits / max(1, len(exp["identifiers"])),
        "line_recall": line_hits / max(1, len(exp["lines"])),
        "qr_decoded": bool(res.qr_codes),
        "qr_expected": bool(p.get("qr_payload")),
        "text": res.text[:200],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--out", default=str(ROOT / "backend" / "artifacts" / "ocr_bench.json"))
    args = ap.parse_args()
    samples = load_sample(args.limit)
    rows = []
    for i, p in enumerate(samples):
        r = score_one(p)
        rows.append(r)
        print(f"[{i+1}/{len(samples)}] {r['file']:<26} {r['variant']:<8} id_recall={r['identifier_recall']:.2f} "
              f"line_recall={r['line_recall']:.2f} qr={r['qr_decoded']}/{r['qr_expected']} {r['seconds']}s", flush=True)
    n = max(1, len(rows))
    summary = {
        "samples": len(rows),
        "mean_identifier_recall": round(sum(r["identifier_recall"] for r in rows) / n, 3),
        "mean_line_recall": round(sum(r["line_recall"] for r in rows) / n, 3),
        "qr_decode_rate": round(sum(r["qr_decoded"] for r in rows if r["qr_expected"]) /
                                max(1, sum(1 for r in rows if r["qr_expected"])), 3),
        "mean_seconds": round(sum(r["seconds"] for r in rows) / n, 2),
        "by_variant": {},
        "rows": rows,
    }
    for r in rows:
        v = summary["by_variant"].setdefault(r["variant"] or "?", {"n": 0, "id": 0.0})
        v["n"] += 1
        v["id"] += r["identifier_recall"]
    for v in summary["by_variant"].values():
        v["id"] = round(v["id"] / v["n"], 3)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
