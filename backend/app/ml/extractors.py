"""Identifier extraction from free text (chat, OCR, transcripts, QR payloads).

All extractors return :class:`Found` records with the ORIGINAL raw string, a
normalised value, a confidence in [0, 1] and the character span, so the UI can
highlight them.

Supported:
  phone    - Indian mobiles in +91 / 0 / spaced / dashed / obfuscated
             ("9 8 7 6 5...", "nine eight seven...", "98765-43210", "9876 54 3210"),
             Devanagari digits ("९८७६५..."), and 91- prefixed forms -> +91XXXXXXXXXX
  upi      - name@bank validated against known handles, obfuscated "(at)"/" at "
  wallet   - BTC (bech32 + legacy base58check), ETH (EIP-55 checksum), TRX/TRC20
  username - @handle, t.me/, telegram.me/, wa.me/, instagram.com/
  location - gazetteer of Indian cities/states/areas incl. Hinglish spellings
  bank     - bank names and IFSC codes
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hinglish spoken digits
    "shunya": "0", "sifar": "0", "ek": "1", "do": "2", "teen": "3", "char": "4",
    "chaar": "4", "panch": "5", "paanch": "5", "chhe": "6", "che": "6", "chhah": "6",
    "saat": "7", "aath": "8", "nau": "9", "no": "9",
}


@dataclass(slots=True)
class Found:
    """One extracted identifier."""

    type: str
    value: str
    raw: str
    start: int = 0
    end: int = 0
    confidence: float = 0.85
    source: str = "text"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "value": self.value,
            "raw": self.raw,
            "start": self.start,
            "end": self.end,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "extra": self.extra,
        }


# --------------------------------------------------------------------------- phones
_MOBILE_RE = re.compile(
    r"(?:(?:\+|00)\s?91[\s\-]?|91[\s\-](?=\d))?"
    r"(?:(?:\d[\s\-\.]?){10})(?!\d)"
)
_SPOKEN_RUN_RE = re.compile(
    r"\b(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|shunya|sifar|ek|do|teen|char|chaar|"
    r"panch|paanch|chhe|che|chhah|saat|aath|nau|no)\b"
    r"(?:[\s,\-]+\b(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|shunya|sifar|ek|do|teen|char|"
    r"chaar|panch|paanch|chhe|che|chhah|saat|aath|nau|no)\b){9}",
    re.I,
)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


def _valid_mobile(d: str) -> bool:
    """Indian mobile: 10 digits starting 6-9 (synthetic range 90000xxxxx allowed)."""
    if len(d) != 10:
        return False
    return d[0] in "6789"


def normalize_phone(raw: str) -> str | None:
    """Normalise any recognised Indian mobile spelling to ``+91XXXXXXXXXX``.

    Devanagari digits (``९८७६५…``) and full-width digits are folded first.
    """
    raw = unicodedata.normalize("NFKC", raw or "").translate(_DEVANAGARI_DIGITS)
    d = _digits_only(raw)
    if d.startswith("0091"):
        d = d[4:]
    elif d.startswith("91") and len(d) == 12:
        d = d[2:]
    elif d.startswith("0") and len(d) == 11:
        d = d[1:]
    if _valid_mobile(d):
        return "+91" + d
    return None


def extract_phones(text: str) -> list[Found]:
    out: list[Found] = []
    for m in _MOBILE_RE.finditer(text):
        raw = m.group(0)
        val = normalize_phone(raw)
        if not val:
            continue
        conf = 0.95 if "+91" in raw else 0.9
        out.append(Found("phone", val, raw.strip(), m.start(), m.end(), conf))
    # spoken digits: "nine eight seven six five four three two one zero"
    for m in _SPOKEN_RUN_RE.finditer(text):
        words = re.findall(r"[a-z]+", m.group(0).lower())
        d = "".join(DIGIT_WORDS.get(w, "") for w in words)
        if len(d) >= 10:
            val = normalize_phone(d[-10:]) if _valid_mobile(d[-10:]) else (normalize_phone(d) or None)
            if val:
                out.append(Found("phone", val, m.group(0), m.start(), m.end(), 0.8, extra={"spoken": True}))
    # "nine eight seven six five, four three two one zero" with commas handled above.
    # Obfuscated devanagari digits already normalised by caller for OCR/voice paths.
    return _dedupe(out)


def spoken_digits_to_number(text: str) -> str:
    """Convert a digit-word run to digits (used by the voice pipeline)."""
    words = re.findall(r"[a-z]+", text.lower())
    return "".join(DIGIT_WORDS.get(w, w if w.isdigit() else "") for w in words)


# --------------------------------------------------------------------------- UPI
# Public handle set (NPCI member banks / PSPs). Kept broad to avoid false
# negatives; validation failures are surfaced as low confidence instead.
KNOWN_UPI_HANDLES: set[str] = {
    # banks
    "sbi", "hdfcbank", "hdfc", "icici", "axisbank", "axis", "kotak", "kotak811", "yesbank", "yes",
    "pnb", "punjabnationalbank", "bob", "bankofbaroda", "barodampay", "canara", "cnrb", "unionbank",
    "unionbankofindia", "idbi", "idfcbank", "idfcfirstbank", "federal", "federalbank", "indus",
    "indusind", "rblbank", "rbl", "bandhan", "aubank", "australiabank", "dbs", "yesbankltd",
    "centralbank", "cbin", "iob", "indianbank", "ucobank", "karb", "karnatakabank", "southindianbank",
    "csbpay", "jkb", "jkbank", "equitasbank", "ujjivanbank", "ujjivan", "dhanlaxmi", "tjsb",
    "waheda", "ippb", "jsb", "idfc", "icicibank", "ybl",
    # PPI / wallets
    "paytm", "ptys", "ptmx", "phonepe", "ybl", "ibl", "axl", "okbizaxis", "okaxis", "oksbi", "okhdfcbank",
    "okicici", "apl", "upi", "rapl", "fbl", "shb", "airtel", "airtelpaymentsbank", "jiomoney",
    "jupiteraxis", "fam", "naviaxis", "slice", "superyes", "grabpay", "waaxis", "wahdfcbank",
    "waicici", "wasbi", "freecharge", "amazon", "amazonpay", "mobikwik", "zaakpay", "timecosmos",
    "cossb", "smb", "pz", "pingpay", "tapicici", "abfspay", "abfspay", "mahb", "sib", "hsbc",
    "citi", "sc", "scbl", "deutsche", "barclays", "smbc", "nabard", "apb", "abc", "tmb", "tmbank",
    "kbl", "kalp", "kvb", "kvbank", "dcb", "dcbbank", "esaf", "fincare", "suryoday", "nainital",
    "nkgsb", "jsbp", "psb", "pallavaram", "rmgb", "indianbank", "cbin", "puriepay", "vybebank",
    "vyb", "pdbl", "pdb", "jax", "awesome", "cinepoliscfc", "nmc", "yesg", "yesbank", "bnc",
    "nsdl", "aditya", "allbank", "alldigi", "jain", "rajgov", "rk", "sjedb", "shivalikbank",
    "ikwik", "chase", "hsbc", "mayabank", "mkbl", "noi", "ezeepay", "ekqr", "airtelbank",
}
# Handles used only by the synthetic dataset (accepted, but lower confidence than
# a real NPCI member handle).
_SYNTHETIC_HANDLES = {"fakebank", "mockbank", "testbank"}
_UPI_RE = re.compile(r"\b[a-z0-9][a-z0-9._\-]{1,63}@[a-z][a-z0-9]{1,24}\b", re.I)
_UPI_OBF_RE = re.compile(
    r"\b([a-z0-9][a-z0-9._\-]{1,63})\s*(?:\(at\)|\[at\]| at the rate of | at )\s*([a-z0-9]{2,24})\b", re.I
)


def extract_upis(text: str) -> list[Found]:
    """Extract UPI VPAs. ``paytmqr281005050101@paytm`` and ``name(at)fakebank`` work."""
    out: list[Found] = []
    lowered = text.lower()
    for m in _UPI_RE.finditer(lowered):
        vpa = m.group(0)
        user, handle = vpa.split("@", 1)
        if handle in KNOWN_UPI_HANDLES and handle in _SYNTHETIC_HANDLES:
            conf = 0.85
        elif handle in KNOWN_UPI_HANDLES:
            conf = 0.92
        else:
            conf = 0.6
        # number-only users are usually emails (e.g. 98765@gmail.com); keep UPI-like ones
        out.append(
            Found(
                "upi",
                vpa,
                text[m.start() : m.end()],
                m.start(),
                m.end(),
                conf,
                extra={"handle_known": handle in KNOWN_UPI_HANDLES, "payee": user},
            )
        )
    for m in _UPI_OBF_RE.finditer(lowered):
        user, handle = m.group(1), m.group(2)
        if handle in {"the", "rate", "of", "and", "for", "with", "upi"}:
            continue
        vpa = f"{user}@{handle}"
        out.append(
            Found("upi", vpa, text[m.start() : m.end()], m.start(), m.end(), 0.6, extra={"obfuscated": True})
        )
    return _dedupe(out)


# --------------------------------------------------------------------------- wallets
_BTC_BECH32_RE = re.compile(r"\b(bc1)[a-z0-9]{25,62}\b", re.I)
_BTC_LEGACY_RE = re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b")
_ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TRON_RE = re.compile(r"\bT[a-km-zA-HJ-NP-Z1-9]{33}\b")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    num = 0
    for c in s:
        num = num * 58 + _B58.index(c)
    return num.to_bytes((num.bit_length() + 7) // 8, "big")


def _b58check_ok(addr: str) -> bool:
    try:
        raw = _b58decode(addr)
    except ValueError:
        return False
    if len(raw) < 5:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_ok(addr: str) -> bool:
    try:
        pos = addr.rfind("1")
        hrp, data = addr[:pos], addr[pos + 1 :]
        if hrp.lower() not in {"bc", "tb"} or len(data) < 6:
            return False
        charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
        vals = [charset.index(c) for c in data.lower()]
        expand = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
        return _bech32_polymod(expand + vals) in (1, 0x2BC830A3)
    except (ValueError, IndexError):
        return False


def eth_checksum(addr_hex: str) -> str:
    """EIP-55 mixed-case checksum (Keccak-256, implemented locally)."""
    from app.ml.keccak import keccak256_hex

    addr = addr_hex.lower().replace("0x", "")
    h = keccak256_hex(addr.encode())
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(addr))


def extract_wallets(text: str) -> list[Found]:
    out: list[Found] = []
    for m in _BTC_BECH32_RE.finditer(text):
        ok = _bech32_ok(m.group(0))
        out.append(Found("wallet", m.group(0).lower(), m.group(0), m.start(), m.end(),
                         0.92 if ok else 0.4, extra={"chain": "BTC", "checksum": ok}))
    for m in _BTC_LEGACY_RE.finditer(text):
        ok = _b58check_ok(m.group(0))
        if ok or len(m.group(0)) >= 26:
            out.append(Found("wallet", m.group(0), m.group(0), m.start(), m.end(),
                             0.9 if ok else 0.35, extra={"chain": "BTC", "checksum": ok}))
    for m in _ETH_RE.finditer(text):
        a = m.group(0)
        checksummed = eth_checksum(a)
        # Valid iff all-lower/all-upper or the EIP-55 mixed case matches exactly.
        good_case = a[2:] in (a[2:].lower(), a[2:].upper()) or checksummed == a
        out.append(Found("wallet", checksummed, a, m.start(), m.end(), 0.95 if good_case else 0.5,
                         extra={"chain": "ETH", "checksum": good_case}))
    for m in _TRON_RE.finditer(text):
        ok = _b58check_ok(m.group(0))
        out.append(Found("wallet", m.group(0), m.group(0), m.start(), m.end(),
                         0.9 if ok else 0.35, extra={"chain": "TRX/USDT-TRC20", "checksum": ok}))
    return _dedupe(out)


# --------------------------------------------------------------------------- usernames/links
_LINK_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?"
    r"(?:t\.me|telegram\.me|tele\.dog|instagram\.com|instagr\.am|wa\.me|api\.whatsapp\.com)"
    r"/[A-Za-z0-9_\-\./?=&%#\+]+)",
    re.I,
)
_HANDLE_RE = re.compile(r"(?<![\w.@])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
_TG_INLINE = re.compile(r"\b(?:telegram|tg|tele)\s*[:\-]?\s*@?([A-Za-z][A-Za-z0-9_]{3,31})\b", re.I)
_IG_INLINE = re.compile(r"\b(?:insta(?:gram)?|ig)\s*[:\-]?\s*@?([A-Za-z][A-Za-z0-9_.]{2,29})\b", re.I)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def extract_links(text: str) -> tuple[list[Found], list[Found]]:
    """Return (usernames, links)."""
    users: list[Found] = []
    links: list[Found] = []
    for m in _LINK_RE.finditer(text):
        raw = m.group(0)
        low = raw.lower()
        handle = raw.rstrip("/").split("/")[-1].split("?")[0]
        if "wa.me" in low or "whatsapp" in low:
            phone = normalize_phone(handle)
            if phone and re.fullmatch(r"\d+", handle):
                links.append(Found("link", raw if "://" in raw else "https://" + raw, raw, m.start(), m.end(), 0.9,
                                   extra={"kind": "whatsapp"}))
                users.append(Found("phone", phone, raw, m.start(), m.end(), 0.85, extra={"from": "wa.me"}))
                continue
        if handle in {"s", "joinchat", "+", "invite", "share"}:
            continue
        users.append(Found("username", "@" + handle.lower(), raw, m.start(), m.end(), 0.9,
                           extra={"platform": "instagram" if "instagram" in low or "instagr" in low else "telegram"}))
        links.append(Found("link", raw if "://" in raw else "https://" + raw, raw, m.start(), m.end(), 0.9,
                           extra={"kind": "telegram" if "t.me" in low or "telegram" in low else "instagram"}))
    for m in _HANDLE_RE.finditer(text):
        users.append(Found("username", "@" + m.group(1).lower(), m.group(0), m.start(), m.end(), 0.85))
    for m in _TG_INLINE.finditer(text):
        users.append(Found("username", "@" + m.group(1).lower(), m.group(0), m.start(), m.end(), 0.75,
                           extra={"platform": "telegram"}))
    for m in _IG_INLINE.finditer(text):
        users.append(Found("username", "@" + m.group(1).lower(), m.group(0), m.start(), m.end(), 0.75,
                           extra={"platform": "instagram"}))
    return _dedupe(users), _dedupe(links)


# --------------------------------------------------------------------------- locations
_LOCATIONS: dict[str, tuple[str, tuple[float, float]]] = {
    # city: (canonical, (lat, lng))
    "delhi": ("Delhi", (28.6139, 77.2090)),
    "new delhi": ("Delhi", (28.6139, 77.2090)),
    "delhi ncr": ("Delhi NCR", (28.5355, 77.3910)),
    "noida": ("Noida", (28.5355, 77.3910)),
    "greater noida": ("Greater Noida", (28.4744, 77.5040)),
    "gurgaon": ("Gurugram", (28.4595, 77.0266)),
    "gurugram": ("Gurugram", (28.4595, 77.0266)),
    "ghaziabad": ("Ghaziabad", (28.6692, 77.4538)),
    "faridabad": ("Faridabad", (28.4089, 77.3178)),
    "mumbai": ("Mumbai", (19.0760, 72.8777)),
    "bombay": ("Mumbai", (19.0760, 72.8777)),
    "thane": ("Thane", (19.2183, 72.9781)),
    "navi mumbai": ("Navi Mumbai", (19.0330, 73.0297)),
    "andheri": ("Mumbai", (19.1136, 72.8697)),
    "bandra": ("Mumbai", (19.0596, 72.8295)),
    "byculla": ("Mumbai", (18.9788, 72.8328)),
    "bengaluru": ("Bengaluru", (12.9716, 77.5946)),
    "bangalore": ("Bengaluru", (12.9716, 77.5946)),
    "blr": ("Bengaluru", (12.9716, 77.5946)),
    "whitefield": ("Bengaluru", (12.9698, 77.7499)),
    "hyderabad": ("Hyderabad", (17.3850, 78.4867)),
    "hyd": ("Hyderabad", (17.3850, 78.4867)),
    "secunderabad": ("Hyderabad", (17.4399, 78.4983)),
    "chennai": ("Chennai", (13.0827, 80.2707)),
    "madras": ("Chennai", (13.0827, 80.2707)),
    "kolkata": ("Kolkata", (22.5726, 88.3639)),
    "calcutta": ("Kolkata", (22.5726, 88.3639)),
    "howrah": ("Howrah", (22.5958, 88.2636)),
    "pune": ("Pune", (18.5204, 73.8567)),
    "puna": ("Pune", (18.5204, 73.8567)),
    "ahmedabad": ("Ahmedabad", (23.0225, 72.5714)),
    "amritsar": ("Amritsar", (31.6340, 74.8723)),
    "ambala": ("Ambala", (30.3782, 76.7767)),
    "patiala": ("Patiala", (30.3398, 76.3869)),
    "ludhiana": ("Ludhiana", (30.9010, 75.8573)),
    "jaipur": ("Jaipur", (26.9124, 75.7873)),
    "lucknow": ("Lucknow", (26.8467, 80.9462)),
    "kanpur": ("Kanpur", (26.4499, 80.3319)),
    "varanasi": ("Varanasi", (25.3176, 82.9739)),
    "banaras": ("Varanasi", (25.3176, 82.9739)),
    "patna": ("Patna", (25.5941, 85.1376)),
    "ranchi": ("Ranchi", (23.3441, 85.3096)),
    "bhopal": ("Bhopal", (23.2599, 77.4126)),
    "indore": ("Indore", (22.7196, 75.8577)),
    "nagpur": ("Nagpur", (21.1458, 79.0882)),
    "surat": ("Surat", (21.1702, 72.8311)),
    "rajkot": ("Rajkot", (22.3039, 70.8022)),
    "kochi": ("Kochi", (9.9312, 76.2673)),
    "cochin": ("Kochi", (9.9312, 76.2673)),
    "trivandrum": ("Thiruvananthapuram", (8.5241, 76.9366)),
    "guwahati": ("Guwahati", (26.1445, 91.7362)),
    "bhubaneswar": ("Bhubaneswar", (20.2961, 85.8245)),
    "chandigarh": ("Chandigarh", (30.7333, 76.7794)),
    "dehradun": ("Dehradun", (30.3165, 78.0322)),
    "shimla": ("Shimla", (31.1048, 77.1734)),
    "jammu": ("Jammu", (32.7266, 74.8570)),
    "srinagar": ("Srinagar", (34.0837, 74.7973)),
    "agra": ("Agra", (27.1767, 78.0081)),
    "meerut": ("Meerut", (28.9845, 77.7064)),
    "gaya": ("Gaya", (24.7914, 85.0002)),
    "muzaffarpur": ("Muzaffarpur", (26.1225, 85.3906)),
    "darbhanga": ("Darbhanga", (26.1542, 85.8918)),
    "siliguri": ("Siliguri", (26.7271, 88.3953)),
    "goa": ("Goa", (15.2993, 74.1240)),
    "panaji": ("Panaji", (15.4909, 73.8278)),
    "puri": ("Puri", (19.8135, 85.8312)),
    "cuttack": ("Cuttack", (20.4625, 85.8830)),
    "raipur": ("Raipur", (21.2514, 81.6296)),
    "jabalpur": ("Jabalpur", (23.1815, 79.9864)),
    "gwalior": ("Gwalior", (26.2183, 78.1828)),
    "bareilly": ("Bareilly", (28.3670, 79.4304)),
    "aligarh": ("Aligarh", (27.8974, 78.0880)),
    "moradabad": ("Moradabad", (28.8386, 78.7733)),
    "jodhpur": ("Jodhpur", (26.2389, 73.0243)),
    "udaipur": ("Udaipur", (24.5854, 73.7125)),
    "ajmer": ("Ajmer", (26.4499, 74.6399)),
    "mathura": ("Mathura", (27.4924, 77.6737)),
    "vrindavan": ("Vrindavan", (27.5820, 77.7000)),
    "sonipat": ("Sonipat", (28.9931, 77.0151)),
    "rohtak": ("Rohtak", (28.8955, 76.6066)),
    "hisar": ("Hisar", (29.1492, 75.7217)),
    "karnal": ("Karnal", (29.6857, 76.9905)),
    "panipat": ("Panipat", (29.3909, 76.9635)),
    "saharanpur": ("Saharanpur", (29.9640, 77.5460)),
    "tumkur": ("Tumkur", (13.3379, 77.1173)),
    "mysore": ("Mysuru", (12.2958, 76.6394)),
    "mysuru": ("Mysuru", (12.2958, 76.6394)),
    "mangalore": ("Mangaluru", (12.9141, 74.8560)),
    "coimbatore": ("Coimbatore", (11.0168, 76.9558)),
    "madurai": ("Madurai", (9.9252, 78.1198)),
    "vijayawada": ("Vijayawada", (16.5062, 80.6480)),
    "visakhapatnam": ("Visakhapatnam", (17.6868, 83.2185)),
    "vizag": ("Visakhapatnam", (17.6868, 83.2185)),
}
_STATES = {
    "delhi", "uttar pradesh", "up", "maharashtra", "karnataka", "telangana", "tamil nadu", "kerala",
    "west bengal", "punjab", "haryana", "rajasthan", "gujarat", "madhya pradesh", "mp", "bihar",
    "jharkhand", "odisha", "orissa", "assam", "chhattisgarh", "uttarakhand", "himachal pradesh",
    "jammu and kashmir", "goa", "manipur", "meghalaya", "nagaland", "tripura", "mizoram",
    "arunachal pradesh", "sikkim",
}
# Ambiguous single words (brands, English words, other-country cities) that need a
# location cue nearby before they are accepted as places.
_AMBIGUOUS = {
    "kota", "mau", "bhind", "sagar", "baran", "jhalawar", "shivpuri", "panna", "satna", "betul",
    "basavakalyan", "agraharam", "bangalore", "bombay", "madras", "calcutta", "chandigarh", "goa",
}
_LOCATION_CUES = re.compile(
    r"\b(in|at|near|from|to|delivery|deliver|city|area|side|me|mein|se|tak|address|add|location|"
    r"pickup|pick up|pincode|nagar|sector|nearby|around)\b", re.I
)
_LOC_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted((re.escape(k) for k in _LOCATIONS), key=len, reverse=True)) + r")(?![A-Za-z])",
    re.I,
)


def extract_locations(text: str) -> list[Found]:
    """Gazetteer match with a context rule for ambiguous names."""
    out: list[Found] = []
    for m in _LOC_RE.finditer(text):
        key = m.group(1).lower()
        if key in _AMBIGUOUS:
            window = text[max(0, m.start() - 40) : m.end() + 40]
            if not _LOCATION_CUES.search(window):
                continue
        canon, _ = _LOCATIONS[key]
        out.append(Found("location", canon, m.group(0), m.start(), m.end(), 0.7,
                         extra={"lat": _LOCATIONS[key][1][0], "lng": _LOCATIONS[key][1][1]}))
    return _dedupe(out)


# --------------------------------------------------------------------------- banks
_BANKS = ["SBI", "HDFC", "ICICI", "Axis", "Kotak", "PNB", "Bank of Baroda", "Canara", "Union Bank",
          "IDFC", "IndusInd", "Yes Bank", "RBL", "Bandhan", "AU Small Finance", "Federal Bank",
          "Bank of India", "Central Bank", "UCO Bank", "Indian Bank", "Jana Bank"]
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")


def extract_banks(text: str) -> list[Found]:
    out: list[Found] = []
    for m in _IFSC_RE.finditer(text):
        out.append(Found("ifsc", m.group(0), m.group(0), m.start(), m.end(), 0.95,
                         extra={"bank": m.group(0)[:4]}))
    for b in _BANKS:
        for m in re.finditer(rf"(?<![A-Za-z]){re.escape(b)}(?![A-Za-z])", text, re.I):
            out.append(Found("bank", b.lower(), m.group(0), m.start(), m.end(), 0.7))
    return _dedupe(out)


# --------------------------------------------------------------------------- email (non-UPI)
def extract_emails(text: str, exclude: Iterable[str] = ()) -> list[Found]:
    out = []
    excl = set(exclude)
    for m in _EMAIL_RE.finditer(text):
        if m.group(0).lower() in excl:
            continue
        out.append(Found("email", m.group(0).lower(), m.group(0), m.start(), m.end(), 0.7))
    return out


# --------------------------------------------------------------------------- facade
def _dedupe(items: list[Found]) -> list[Found]:
    seen: set[tuple[str, str]] = set()
    out: list[Found] = []
    for f in items:
        key = (f.type, f.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def extract_all(text: str, *, source: str = "text") -> list[Found]:
    """Run every extractor and return identifiers tagged with their source."""
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).translate(_DEVANAGARI_DIGITS)
    users, links = extract_links(text)
    found: list[Found] = []
    found += extract_phones(text)
    upis = extract_upis(text)
    found += upis
    found += extract_wallets(text)
    found += users
    found += links
    found += extract_locations(text)
    found += extract_banks(text)
    found += extract_emails(text, exclude={u.value for u in upis})
    for f in found:
        f.source = source
    return _dedupe(found)


def merge_found(*groups: Iterable[Found]) -> list[Found]:
    out: list[Found] = []
    for g in groups:
        out.extend(g)
    return _dedupe(out)
