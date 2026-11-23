"""Hybrid engine, lexicon, financial detector, normalization and scope tests."""

from __future__ import annotations

import pytest

from app.core.scope import check_public_target
from app.ml import financial
from app.ml.engine import engine
from app.ml.lexicon import load_lexicon
from app.ml.normalize import normalize


# --------------------------------------------------------------------------- lexicon
def test_lexicon_loads_and_matches_hinglish() -> None:
    lex = load_lexicon()
    res = lex.match("Fresh maal available hai, w33d bhi hai 🔌")
    cats = res.by_category()
    assert len(cats["drugs"]) >= 2


def test_devanagari_terms_match_romanized_text() -> None:
    lex = load_lexicon()
    res = lex.match("गांजा माल उपलब्ध है")
    assert res.by_category()["drugs"]


def test_emoji_only_ad() -> None:
    lex = load_lexicon()
    assert lex.emoji_only("🍁🔌💨") == "drugs"


# --------------------------------------------------------------------------- normalization
def test_normalize_leetspeak_and_emoji() -> None:
    n = normalize("w33d m@al 🍁")
    assert "weed" in n and "maal" in n and "maple_leaf" in n


def test_normalize_devanagari_digits() -> None:
    assert "98765" in normalize("नंबर ९८७६५ है")


# --------------------------------------------------------------------------- financial
def test_financial_high_risk() -> None:
    r = financial.detect("Bank account kiraye pe available, KYC done. Daily commission 2500. contact 9000011111")
    assert r.is_financial and r.intent == "RENT" and r.risk == "HIGH"
    assert r.type == "bank_account"
    assert any(e["kind"] == "commission" for e in r.evidence)


def test_financial_medium_risk_without_contact() -> None:
    r = financial.detect("net banking kit with otp available for rent pe")
    assert r.is_financial and r.risk in ("MEDIUM", "LOW")
    assert r.type == "net_banking_kit"


def test_financial_benign_banking_not_flagged() -> None:
    r = financial.detect("Open a savings account with 4% interest at our nearest branch")
    assert r.is_financial is False


def test_financial_sim_subtype() -> None:
    r = financial.detect("otp linked sim available, daily rent 500, dm me")
    assert r.is_financial and "sim" in r.types


# --------------------------------------------------------------------------- engine
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Account kiraye pe available, ATM card + passbook + SIM, daily commission 2000, 9000011111", "mule_fin"),
        ("Fresh maal landed, weed 2k per 10g, dm @plug_99", "drugs"),
        ("Desi katta 9mm available with cartridges, 25k, Noida", "arms"),
        ("Ready made Aadhaar and PAN card banwa lo same day 9000012345", "fake_docs"),
    ],
)
def test_engine_flags_illicit(text: str, expected: str) -> None:
    res = engine().predict(text)
    assert res["is_illicit"], res
    assert res["scores"].get(expected, 0) > 0.3


@pytest.mark.parametrize(
    "text",
    [
        "Weed killer spray 500ml available at our garden store",
        "Best cookies recipe with butter and sugar",
        "Open a savings account with 4% interest",
        "Credit card offer: apply online and get cashback",
        "Buy a new SIM at the store with eKYC",
        "Nerf toy gun for kids birthday",
        "Pharmacy: generic medicines available",
    ],
)
def test_engine_ignores_hard_negatives(text: str) -> None:
    res = engine().predict(text)
    assert res["is_illicit"] is False, (text, res["scores"])


def test_explanation_contains_spans() -> None:
    res = engine().predict("Fresh m@al hai, DM kar 9000012345")
    spans = res["explanation"]["spans"]
    assert spans and any(s["kind"] == "slang" for s in spans)
    assert res["explanation"]["reasons"]


# --------------------------------------------------------------------------- scope
def test_scope_accepts_public_links() -> None:
    assert check_public_target("https://t.me/durov").allowed
    assert check_public_target("t.me/durov/15").message_id == 15
    assert check_public_target("@durov").channel == "durov"


def test_scope_rejects_private_invites() -> None:
    for bad in ["https://t.me/+AbCdEf123", "t.me/joinchat/AAAAA", "https://wa.me/919999999999", "not a channel!"]:
        assert check_public_target(bad).allowed is False, bad
