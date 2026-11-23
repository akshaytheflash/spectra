"""Identifier extraction tests: obfuscated phones, Devanagari digits, spoken
numbers, UPI ids, UPI QR payloads, wallets, usernames, locations."""

from __future__ import annotations

import pytest

from app.ml import extractors
from app.ml.extractors import (
    eth_checksum,
    extract_all,
    extract_phones,
    extract_upis,
    extract_wallets,
    normalize_phone,
)
from app.pipeline.vision import parse_qr_payload

E164 = "+919876543210"


# --------------------------------------------------------------------------- phones
@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+919876543210",
        "+91 98765 43210",
        "91-9876543210",
        "09876543210",
        "0 98765 43210",
        "98765-43210",
        "9 8 7 6 5 4 3 2 1 0",
        "9876 54 3210",
        "९८७६५४३२१०",  # Devanagari digits
        "+९१ ९८७६५ ४३२१०",
    ],
)
def test_phone_spellings_normalize_to_e164(raw: str) -> None:
    assert normalize_phone(raw) == E164, raw


@pytest.mark.parametrize(
    "text",
    [
        "call 9 8 7 6 5 4 3 2 1 0 daily",
        "number hai 9876543210 ok",
        "phone: +91-98765-43210",
        "मेरा नंबर ९८७६५४३२१० है",
        "whatsapp karo 0 98765 43210",
    ],
)
def test_phone_detected_in_text(text: str) -> None:
    vals = [f.value for f in extract_phones(text)]
    assert E164 in vals, (text, vals)


def test_spoken_digits_are_detected() -> None:
    text = "call me on nine eight seven six five four three two one zero or nine eight seven six five four three two one one"
    vals = {f.value for f in extract_phones(text)}
    assert "+919876543210" in vals
    assert "+919876543211" in vals


def test_hindi_spoken_digits() -> None:
    text = "mera number nau aath saat chhe paanch chaar teen do ek zero hai"
    vals = {f.value for f in extract_phones(text)}
    assert "+919876543210" in vals


def test_invalid_numbers_rejected() -> None:
    for bad in ["1234567890", "98765", "0000000000", "+1 415 555 0100"]:
        assert normalize_phone(bad) is None, bad


# --------------------------------------------------------------------------- UPI
def test_upi_plain_and_known_handle() -> None:
    found = extract_upis("pay to ravi.kumar123@fakebank or 9000012345@paytm")
    vals = {f.value for f in found}
    assert "ravi.kumar123@fakebank" in vals
    assert "9000012345@paytm" in vals
    conf = {f.value: f.confidence for f in found}
    assert conf["9000012345@paytm"] > conf["ravi.kumar123@fakebank"]


def test_upi_obfuscated_at() -> None:
    found = extract_upis("send to mule.ravi(at)fakebank only")
    assert [f.value for f in found] == ["mule.ravi@fakebank"]


def test_upi_qr_payload_parsed() -> None:
    payload = "upi://pay?pa=ravi.k1111@fakebank&pn=Ravi%20Kumar&am=2500&cu=INR&tn=ok"
    kind, parsed = parse_qr_payload(payload)
    assert kind == "upi"
    assert parsed["vpa"] == "ravi.k1111@fakebank"
    assert parsed["payee_name"] == "Ravi Kumar"
    assert parsed["amount"] == "2500"
    assert parsed["currency"] == "INR"


def test_qr_wallet_and_url_payloads() -> None:
    kind, parsed = parse_qr_payload("bitcoin:bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq?amount=0.01")
    assert kind == "wallet" and parsed["chain"] == "BITCOIN"
    assert parsed["address"].startswith("bc1q")
    kind, parsed = parse_qr_payload("https://t.me/ravi_supply/42")
    assert kind == "url" and parsed["host"] == "t.me"


# --------------------------------------------------------------------------- wallets
def test_eth_checksum_known_vector() -> None:
    # EIP-55 reference vector
    assert eth_checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


def test_wallets_detected() -> None:
    text = (
        "BTC bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq "
        "ETH 0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed "
        "TRX TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    )
    chains = {f.extra.get("chain") for f in extract_wallets(text)}
    assert {"BTC", "ETH", "TRX/USDT-TRC20"} <= chains


def test_wallet_checksum_flag() -> None:
    bad = extract_wallets("send 0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD now")[0]
    assert bad.extra["checksum"] is False


# --------------------------------------------------------------------------- usernames / links
def test_usernames_and_links() -> None:
    text = "dm @ravi_maal or t.me/ravisupply, insta: mule.raj whatsapp https://wa.me/919876543210"
    found = extract_all(text)
    types = {(f.type, f.value) for f in found}
    assert ("username", "@ravi_maal") in types
    assert ("username", "@ravisupply") in types
    assert ("username", "@mule.raj") in types
    assert ("phone", "+919876543210") in types
    assert any(f.type == "link" and "t.me/ravisupply" in f.value for f in found)


def test_locations_gazetteer() -> None:
    found = extract_all("delivery in Noida and Delhi NCR, Gurgaon, बांद्रा")
    locs = {f.value for f in found if f.type == "location"}
    assert {"Noida", "Delhi NCR", "Gurugram"} <= locs


def test_ifsc_and_banks() -> None:
    found = extract_all("IFSC HDFC0001234 for SBI account")
    types = {f.type for f in found}
    assert "ifsc" in types and "bank" in types


def test_emails_are_not_upis() -> None:
    found = extract_all("mail me at john.doe@gmail.com")
    upis = [f for f in found if f.type == "upi"]
    assert all(f.value != "john.doe@gmail.com" for f in upis)


def test_source_tagging() -> None:
    found = extract_all("call 9876543210", source="voice")
    assert all(f.source == "voice" for f in found)


def test_wa_me_yields_phone_and_link() -> None:
    found = extract_all("https://wa.me/919000012345")
    types = {f.type for f in found}
    assert {"phone", "link"} <= types
