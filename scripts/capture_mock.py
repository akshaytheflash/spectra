"""Capture real API responses from a running local backend into mock fixtures.

Usage:
    1. start the backend:  uvicorn app.main:app --port 8000   (from backend/)
    2. python scripts/capture_mock.py

Writes one JSON file per endpoint into frontend/src/lib/mockdata/ so the
deployed static build can answer /api-less same-origin requests without a
server behind it.
"""

from __future__ import annotations

import json
import pathlib
import sys

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "src" / "lib" / "mockdata"

client = httpx.Client(base_url=BASE, timeout=180.0)
written: list[str] = []


def save(name: str, path: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    written.append(f"{name}  {path}  {len(json.dumps(data))}B")


def get(name: str, path: str, fallback=None) -> object | None:
    try:
        r = client.get(path)
        if r.status_code >= 400:
            print(f"SKIP {name}: {r.status_code} {path} :: {r.text[:200]}")
            return fallback
        try:
            data = r.json()
        except Exception:
            print(f"SKIP {name}: non-json {path}")
            return fallback
        save(name, path, data)
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {exc}")
        return fallback


def call(name: str, method: str, path: str, body=None) -> object | None:
    try:
        r = client.request(method, path, json=body)
        if r.status_code >= 400:
            print(f"SKIP {name}: {r.status_code} {path} :: {r.text[:200]}")
            return None
        data = r.json()
        save(name, path, data)
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {exc}")
        return None


def main() -> int:
    # --- health / overview -------------------------------------------------
    get("health", "/health")
    get("stats", "/stats")
    get("metrics", "/metrics")
    get("ops_status", "/ops/status")
    get("realtime_config", "/realtime/config")

    # --- alerts ------------------------------------------------------------
    alerts = get("alerts", "/alerts?page=1&page_size=50")
    get("alerts_overview", "/alerts?page=1&page_size=9&sort=timestamp&order=desc")
    get("alerts_recent", "/alerts/recent")
    get("campaigns", "/campaigns")
    identifiers = get("identifiers", "/identifiers")

    ids: list[str] = []
    if isinstance(alerts, dict):
        ids = [str(a.get("id")) for a in (alerts.get("items") or [])[:4]]

    for i, alert_id in enumerate(ids):
        detail = get(f"alert_{i}", f"/alerts/{alert_id}")
        get(f"alert_{i}_verify", f"/alerts/{alert_id}/verify")
        if i == 0 and isinstance(detail, dict):
            # exercise the mutating paths, then leave the fixture as the
            # "confirmed" state the UI wants to render
            call(f"alert_{i}_status", "PATCH", f"/alerts/{alert_id}/status",
                 {"status": "confirmed", "notes": "captured by capture_mock.py"})
            get(f"alert_{i}", f"/alerts/{alert_id}")

    # --- channels ----------------------------------------------------------
    get("channels", "/channels?limit=200")
    get("channels_monitored", "/channels?monitored=true&limit=6")
    get("channels_status", "/channels/status")
    get("channels_names", "/channels/names")
    get("channels_discover", "/channels/discover?q=drugs&limit=20")
    get("search", "/search?q=drugs&mode=auto")
    get("ingest_jobs", "/ingest/jobs")

    # --- graph / map -------------------------------------------------------
    graph = get("graph", "/graph")
    ident = None
    if isinstance(graph, dict):
        for n in graph.get("nodes") or []:
            if n.get("kind") in ("Phone", "UPI", "Username", "Wallet", "Bank"):
                ident = n.get("id")
                if ident:
                    break
    get("graph_focused", f"/graph?identifier={ident}&hops=2" if ident else "/graph")
    get("map", "/map")

    # --- lexicon / identity / classify -------------------------------------
    lex = get("lexicon", "/lexicon")
    term_id = None
    if isinstance(lex, dict):
        terms = lex.get("terms") or []
        if terms:
            term_id = terms[0].get("id")
    created = call("lexicon_term_create", "POST", "/lexicon/terms",
                   {"category": "drugs", "term": "capture-mock-probe",
                    "kind": "slang", "weight": 0.5})
    if isinstance(created, dict) and created.get("id") is not None:
        call("lexicon_term_delete", "DELETE", f"/lexicon/terms/{created['id']}")
    elif term_id is not None:
        call("lexicon_term_delete", "DELETE", f"/lexicon/terms/{term_id}")

    call("classify", "POST", "/classify",
         {"text": "weed ganja available, telegram @seller99, upi 9876543210@okaxis"})

    ident_value = None
    if isinstance(identifiers, dict):
        for row in identifiers.get("items") or []:
            if row.get("type") in ("phone", "upi", "username", "wallet"):
                ident_value = row.get("value")
                if ident_value:
                    break
    if ident_value:
        get("identity", f"/identity/{ident_value}")

    # --- feedback (writes a row; harmless, DB is backed up) ----------------
    if ids:
        call("feedback", "POST", "/feedback",
             {"alert_id": ids[0], "accepted": True, "note": "captured by capture_mock.py"})

    print(f"\nwrote {len(written)} fixtures to {OUT}")
    for line in written:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
