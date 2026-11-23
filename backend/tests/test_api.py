"""API tests over an isolated SQLite database: ingest -> alert -> evidence
integrity, workflow transitions, graph linking across 3+ channels, campaigns."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.db.session import init_db

    init_db()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _post(client, text: str, channel: str, ts: datetime, ext: str | None = None) -> str:
    """Insert a post through the pipeline service (same code path as ingest)."""
    from app.db.session import session_scope
    from app.pipeline import ingest

    with session_scope() as db:
        post = ingest.create_post(db, post_id=str(uuid.uuid4()), text=text, channel=channel,
                                  timestamp=ts, external_id=ext or uuid.uuid4().hex[:8])
        ingest.process_post(db, post)
        return post.id


@pytest.fixture(scope="module")
def seeded(client):
    now = datetime.now(timezone.utc)
    star = "9000011111"
    ids = []
    for i, ch in enumerate(["alpha_deals", "beta_supply", "gamma_khata", "delta_gully"]):
        ids.append(_post(client, f"Account kiraye pe available, KYC done, ATM + passbook + SIM, "
                                 f"daily commission 2000, contact {star} dm @ravisupply_ncr", ch, now - timedelta(days=i)))
    ids.append(_post(client, "Fresh maal landed, weed 2k per 10g, dm @plug_99", "alpha_deals", now))
    # benign with a hard negative must not create an alert
    ids.append(_post(client, "Weed killer spray 500ml at our garden store", "lok_mela", now))
    return {"ids": ids, "phone": star}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_benign_not_alerted(client, seeded):
    from sqlalchemy import select

    from app.db.models import Alert, Post
    from app.db.session import session_scope

    with session_scope() as db:
        post = db.get(Post, seeded["ids"][-1])
        assert post.status == "ok"
        assert db.execute(select(Alert).where(Alert.post_id == post.id)).scalar_one_or_none() is None


def test_alerts_have_evidence_and_verify(client, seeded):
    r = client.get("/alerts?page_size=5")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    aid = items[0]["id"]
    detail = client.get(f"/alerts/{aid}").json()
    assert detail["evidence"]["sha256"]
    assert detail["category"] in ("bank_account", "atm_debit_card", "sim", "net_banking_kit",
                                  "mule_account", "mule_fin", "drugs")
    v = client.get(f"/alerts/{aid}/verify").json()
    assert v["ok"] is True and v["recomputed_sha256"] == v["stored_sha256"]


def test_evidence_tamper_detected(client, seeded):
    from app.pipeline.evidence import compute_hash, verify

    payload = {"post_text": "x", "channel": "c", "identifiers": []}
    h = compute_hash(payload)
    payload["post_text"] = "tampered"
    assert verify(payload, h)["ok"] is False


def test_status_workflow_and_audit(client, seeded):
    aid = client.get("/alerts?page_size=1").json()["items"][0]["id"]
    r = client.patch(f"/alerts/{aid}/status", json={"status": "under_review", "notes": "triage"})
    assert r.status_code == 200
    r2 = client.patch(f"/alerts/{aid}/status", json={"status": "confirmed", "notes": "verified FIR"})
    assert r2.status_code == 200
    bad = client.patch(f"/alerts/{aid}/status", json={"status": "new"})
    assert bad.status_code == 409  # confirmed -> new is illegal
    detail = client.get(f"/alerts/{aid}").json()
    assert [a["to_status"] for a in detail["audit"]][-2:] == ["under_review", "confirmed"]


def test_graph_links_identifier_across_three_channels(client, seeded):
    # identifiers are normalised to E.164 in storage and in the graph
    r = client.get(f"/graph?identifier=%2B91{seeded['phone']}&hops=2")
    body = r.json()
    assert body["meta"]["found"] is True
    kinds = {n["kind"] for n in body["nodes"]}
    assert "Phone" in kinds and "Post" in kinds
    channels = {n.get("channel") for n in body["nodes"] if n["kind"] == "Post" and n.get("channel")}
    assert len(channels) >= 3, channels


def test_campaigns_detected(client, seeded):
    r = client.get("/campaigns?refresh=true")
    body = r.json()
    assert body["total"] >= 1
    top = body["items"][0]
    assert len(top["channels"]) >= 3
    assert any("9000011111" in s for s in top["shared_identifiers"])


def test_identifiers_registry_and_export(client, seeded):
    r = client.get("/identifiers?type=phone")
    rows = r.json()["items"]
    assert any(row["value"] == "+919000011111" and row["channel_count"] >= 3 for row in rows)
    csv_resp = client.get("/identifiers/export.csv")
    assert csv_resp.status_code == 200 and "type,value" in csv_resp.text


def test_classify_endpoint(client):
    r = client.post("/classify", json={"text": "Account kiraye pe daily commission 2000 contact 9000012345"})
    body = r.json()
    assert body["is_illicit"] and body["category"].startswith(("mule", "atm", "net_banking"))
    assert any(i["type"] == "phone" for i in body["identifiers"])


def test_telegram_scope_rejects_invites(client):
    r = client.post("/ingest/telegram", json={"targets": ["https://t.me/+abc"], "limit": 5})
    assert r.status_code == 400


def test_report_pdf(client, seeded):
    aid = client.get("/alerts?page_size=1").json()["items"][0]["id"]
    r = client.get(f"/report/{aid}.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_stats(client, seeded):
    s = client.get("/stats").json()
    assert s["posts_scanned"] >= 5 and s["alerts"] >= 1 and "source_breakdown" in s
