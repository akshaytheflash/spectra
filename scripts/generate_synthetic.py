"""Generate a fully synthetic SPECTRA dataset (~2000 posts, 12 fake channels).

Nothing here is real personal data: phone numbers use the ``90000xxxxx``
range, UPI handles use ``@fakebank``, wallets/QR payloads are generated from
fake keys, and images/audio are rendered locally.

Outputs ``data/generated/``:
  posts.json          - posts with ground-truth labels (text, media refs)
  images/*.png        - posters, chat screenshots, UPI QR codes (+ noisy/rotated
                        and near-duplicate pHash variants)
  audio/*.wav         - synthetic voice notes (offline TTS) with transcripts
  lexicon_holdout.json- paraphrases for the "unseen slang" evaluation
  contacts.json       - the reused identifier pool (for coordination checks)

Run: ``python scripts/generate_synthetic.py --n 2000 --out data/generated``
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

LABELS = ("drugs", "arms", "fake_docs", "mule_fin", "benign")

CHANNELS = [
    ("@delhi_night_things", "Delhi Night Things", 18420),
    ("@mumbai_snow_zone", "Mumbai Snow Zone", 9320),
    ("@ncr_maal_supply", "NCR Maal Supply", 22140),
    ("@chicha_corner", "Chicha Corner", 4100),
    ("@katta_kings_ambala", "Katta Kings Ambala", 7600),
    ("@tamancha_baazar", "Tamancha Baazar", 3150),
    ("@docs_quick_ncr", "Docs Quick NCR", 5120),
    ("@aadhaar_ready_hub", "Aadhaar Ready Hub", 2890),
    ("@mule_khata_rent", "Khata Rent Deals", 15600),
    ("@account_kiraye", "Account Kiraye", 8840),
    ("@netbanking_kits", "Net Banking Kits", 6470),
    ("@sim_otp_store", "SIM OTP Store", 3990),
    ("@city_ki_gully", "City Ki Gully", 20110),
    ("@lok_mela_up", "Lok Mela UP", 12250),
]

CITY_POOL = [
    "Delhi", "Delhi NCR", "Noida", "Gurgaon", "Gurugram", "Ghaziabad", "Faridabad", "Mumbai",
    "Thane", "Andheri", "Bandra", "Bengaluru", "Whitefield", "Hyderabad", "Chennai", "Kolkata",
    "Howrah", "Pune", "Jaipur", "Lucknow", "Kanpur", "Patna", "Varanasi", "Amritsar", "Ludhiana",
    "Chandigarh", "Indore", "Bhopal", "Nagpur", "Surat", "Ahmedabad", "Ranchi", "Guwahati",
    "Muzaffarpur", "Meerut", "Agra", "Dehradun", "Srinagar", "Kochi",
]

PHONE_DIGITS = [str(d) for d in range(10)]


# --------------------------------------------------------------------------- helpers
def fake_phone(rng: random.Random) -> str:
    return "9000" + "".join(str(rng.randint(0, 9)) for _ in range(6))


def fake_upi(rng: random.Random) -> str:
    names = ["ravi.k", "sunil99", "mule.raj", "kittu.pay", "armanx", "deepak.s", "nitin", "shivam.k",
             "fskart", "payzzy", "realdeal", "bhai.khata", "totamz", "tariq.k", "sahil", "monu"]
    return f"{rng.choice(names)}{rng.randint(10, 9999)}@fakebank"


def fake_handle(rng: random.Random, prefix: str) -> str:
    return f"@{prefix}{rng.randint(10, 999)}_{rng.choice(['admin', 'deal', 'supply', 'india', 'ncr', 'boss', 'xx'])}"


def fake_btc(rng: random.Random) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return "1" + "".join(rng.choice(alphabet) for _ in range(33))


def fake_eth(rng: random.Random) -> str:
    return "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))


def fake_tron(rng: random.Random) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return "T" + "".join(rng.choice(alphabet) for _ in range(33))


def obfuscate_phone(rng: random.Random, phone: str) -> str:
    style = rng.choice(["spaced", "dashed", "spoken", "dev", "plus", "zero", "at-format"])
    if style == "spaced":
        return " ".join(phone[i : i + 2] for i in range(0, 10, 2))
    if style == "dashed":
        return phone[:5] + "-" + phone[5:]
    if style == "spoken":
        return " ".join({"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
                         "6": "six", "7": "seven", "8": "eight", "9": "nine"}[c] for c in phone)
    if style == "dev":
        return phone.translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    if style == "plus":
        return f"+91-{phone[:5]}-{phone[5:]}"
    if style == "zero":
        return "0" + phone
    return f"{phone[:4]}...{phone[6:]}"


def leetify(rng: random.Random, text: str) -> str:
    table = {"a": "@", "e": "3", "o": "0", "i": "1", "s": "$", "t": "7"}
    chars = list(text)
    for i, c in enumerate(chars):
        if c in table and rng.random() < 0.25:
            chars[i] = table[c]
    return "".join(chars)


def typoize(rng: random.Random, text: str) -> str:
    words = text.split()
    for i, w in enumerate(words):
        if len(w) > 5 and rng.random() < 0.15:
            j = rng.randrange(1, len(w) - 1)
            words[i] = w[:j] + w[j + 1] if rng.random() < 0.5 else w[:j] + w[j] + w[j:]
    return " ".join(words)


# --------------------------------------------------------------------------- templates
# Each template: (category, [text templates]). Placeholders: {city} {phone} {upie} {handle}
# {price} {w1} {w2} etc.
DRUG_T = [
    "Fresh {d1} available {city}. Pure quality, home delivery. Rate {w1} per 10g. DM {handle}",
    "{d1} maal aaya hai bhai 🔌 quality best, delivery {city} only. Contact {phone}",
    "🍁🔌💨",
    "stock ready hai {d1}...{d2} bhi hai. nasha nahi to paisa kyu? {city} se {city2} tak delivery. {phone}",
    "{D1} {d2} supplies available. 1 tola {w1}k. fast delivery 24x7. tg {handle}",
    "snow ❄️ premium quality direct farm price. only serious buyers plez. {city} {phone}",
    "Weed {d1} {d2} aur {d3} available hai. {city} me delivery free. telegram {handle}",
    "काला माल आ गया है. {d1} बहुत बढ़िया क्वालिटी. डिलीवरी {city}. {phone}",
    "MDMA crystals & LSD tabs {city} {price} per strip. discreet packing. dm me",
    "brownie aur cookies bhi mil jayega 😶‍🌫️🍪 {city} \n{phone}",
]

ARM_T = [
    "Desi katta 9mm with 6 cartridges available. price {w1}k. {city} only. {phone}",
    "Tamancha .32 bore and desi pistol in stock 🔫 DM {handle}",
    "pistol and revolver ka full stock hai. desi katta lowest price {city}. contact {phone}",
    "🔫💣",
    "Bandook aur goliyan available. licensed nahi chahiye, direct deal. {city} NCR {phone}",
    "country made pistol + cartridges kit {price}. delivery all india. telegram {handle}",
    "बंदूक और कारतूस उपलब्ध है . {city} मे छुप कर डिलीवरी . {phone}",
    "sutli bomb and crude bomb materials available for {price} per set. dm me",
]

DOCS_T = [
    "Ready made Aadhaar and PAN card available. same day delivery. {city} {phone}",
    "fake DL, marksheet, passport banwa lo 100% original look. {city} me contact {phone}",
    "Aadhaar + PAN + driving licence ready made documents. price {price}. DM {handle}",
    "आधार कार्ड और पैन कार्ड बनवा लो उसी दिन . {city} {phone}",
    "Marksheet, degree certificate aur experience certificate ready made. verified look. {phone}",
    "fake id proof available, voter id ghotala documentation. {city} only. telegram {handle}",
]

MULE_T = [
    "Account kiraye pe available. KYC done account with SIM, ATM card + passbook. Daily commission {w2}. DM {handle}",
    "Bank account rent pe chahiye urgent. daily 3000 commission. contact {phone}",
    "Saving account + ATM card + passbook + SIM full set. per day {w2} rupees commission. {city} me {phone}",
    "net banking kit with otp available, full access. rent or sale. {price} one time. {handle}",
    "खाता किराये पर उपलब्ध है . केवाईसी डन . डेली कमीशन . {phone}",
    "old saving account for sale, corporate account with cheque book. {phone} DM fast",
    "ATM card with pin + net banking kit + OTP linked SIM. rent pe le lo. {city} {handle}",
    "otp linked sim available, fresh sim card with full otp access. daily rent {w2}. {phone}",
    "account sell karna hai mujhe, no risk work. daily commission {w2} rupees. {handle}",
    "credit card and debit card kit with pin for sale. {city} {phone}",
    "netbanking kit rent pe chahiye with token, immediate. {phone}",
]

# Hard negatives: legitimate content that shares surface vocabulary.
BENIGN_T = [
    "Welcome to our channel. Daily market updates and news at 9pm.",
    "Weed killer spray 500ml available in our garden store. Call {phone} for orders.",
    "Best cookies recipe: mix flour, butter and sugar, bake at 180C for 12 minutes.",
    "Open a savings account with 4% interest and zero balance. Visit your nearest branch.",
    "Credit card offer: apply online and get 1000 cashback. Terms apply.",
    "Buy a new SIM online with instant eKYC at your nearest store.",
    "How to apply for a PAN card online - step by step guide for students.",
    "Aadhaar update: change your address online in 5 minutes with UIDAI portal.",
    "Car insurance renewal is now online. Compare quotes and save up to 30%.",
    "Nerf gun and water gun sale for kids. Safe and fun for the whole family.",
    "PUBG and COD Mobile tournament this weekend. Prize pool 5000 rupees.",
    "Gaming laptop sale: RTX graphics, 16GB RAM. {price} only this festive season.",
    "Jan Aushadhi Kendra: generic medicines available at 50% lower prices.",
    "SBI account opening with instant debit card activation. Visit branch today.",
    "Bank holidays in November: list of dates when branches are closed.",
    "Recipe: hash browns and scrambled eggs for breakfast, quick and easy.",
    "Mobile recharge plan: unlimited data with jio sim card at full speed.",
    "GST filing help available for small businesses. Contact us for a free demo.",
    "Pharmacy: new stock of medicines arrived. Ask chemist for prescription drugs.",
    "बाजार भाव आज: सोना और चांदी के रेट देखें",
    "स्वास्थ्य विभाग की सलाह: मौसमी बुखार से बचाव के उपाय",
    "गणपति उत्सव की तैयारी शुरू, इलाके में मिठाइयों की दुकानें सजी",
    "मेडिकल स्टोर पर हर तरह की दवाइयां उपलब्ध हैं",
    "हमारे स्कूल में दाखिला शुरू, बच्चों के लिए विशेष छूट",
    "होटल में बुकिंग खुली, परिवार के साथ आएं",
    "Eid Mubarak to all. Free food distribution at community centre {city}.",
    "Summer camp for kids: art, music and sports. Register before 30th May.",
    "Diwali offer: 20% off on home decor and lights. Shop online now.",
    "New chai point opened in {city}. Masala chai and samosa available at 20 rupees.",
    "Lost one black wallet near {city} metro. Please contact if found. Thank you.",
    "IPL match today at 7:30 PM. Who will win? Comment your prediction.",
    "Free yoga classes in the park every Sunday at 6 AM. All are welcome.",
    "Announcement: office closed on account of public holiday. Back on Monday.",
    "Looking for a roommate in {city}, 2BHK, budget 12000. DM for details.",
    "Selling my old cycle in {city}, good condition, 2500 rupees negotiable.",
    "Stock market: Nifty ends higher, top gainers and losers of the day.",
    "Free tuition for class 10 students by volunteers. Join the community.",
    "Sr. Sales Manager opening at a fintech. Apply through the website.",
    "Cooking classes for beginners: learn to make paneer butter masala.",
    "Happy Diwali! May this festival bring joy to your family.",
]

# Paraphrased "unseen slang" (embedding head) - semantically illegal, lexically novel.
UNSEEN_DEV_T = [
    "Premium garden treats available in the usual way, farm price, discreet drop, inbox",
    "Fresh papers ready for any name you want, same-day service, ping on telegram",
    "Working bank profiles for hire, all codes active, weekly payout",
]
UNSEEN_T = [
    "New collection landed at the usual spot, discreet packing, ping me on telegram {handle}",
    "Party supplies are back in town, hidden parcel delivery tonight {city} {phone}",
    "Premium garden treats ready for the season, direct farm price, inbox only",
    "Paper work service ready in one day, any identity document of your choice, call {phone}",
    "Empty bank profiles available with all codes working, weekly payout, ping {handle}",
    "We buy ready to use money access documents, fair deal, no questions, dm",
    "Hardware for protection available, brand new condition, city delivery {city}",
]

# Deliberately trigger-word-heavy *benign* ads: the embedding head must learn
# that these are legitimate commerce, not coded crime.
FERTILIZER_LOATHS = [
    "We hate spam and fake documents. Only genuine seeds and fertiliser at our store.",
    "No fake products in our store: only original brand hardware and tools.",
    "Bank account safety tips: never share OTP or rent your account to anyone.",
    "Report mule account ads: our helpline is open 24x7 for fraud complaints.",
    "Our pharmacy sells only prescription medicines with a valid doctor's note.",
    "Document pickup centre: collect your verified Aadhaar print after online update.",
    "Police advisory: do not buy SIM cards from strangers, buy at authorised stores.",
    "Account statement printing service at the cyber cafe, bring your ID proof.",
]

BENIGN_PARAPHRASE_T = [
    "Premium organic fertiliser available at our farm store, discreet packaging on request",
    "Same-day document service for passport applications, apply online with identity proof",
    "Small business loans at low interest, apply at the branch for quick approval",
    "Working from home? New bank customer offers are live, open online in minutes",
    "Fresh garden seeds and potting mix in stock at the nursery",
    "Book a flight to Goa, best fares of the season, direct booking",
    "New phone plans with unlimited data, nearest store has stock",
    "Kids birthday party supplies and balloon decorations in stock",
    "Old furniture collection service, free pickup this weekend",
    "Packing boxes and moving supplies now available at low prices",
    "Trekking gear rental in Rishikesh, pick up and return same day",
    "Home delivery of groceries within 30 minutes, order online",
    "Funimation: new episodes drop every Friday, subscribe for updates",
    "Hardware store: tools and PVC pipes available at wholesale rates",
    "Call for a free demo of our accounting software, no advance needed",
]

# Devanagari variants
DEV_MAP = {
    "maal": "माल", "ganja": "गांजा", "charas": "चरस", "nasha": "नशा", "bhang": "भांग",
    "chahiye": "चाहिए", "available": "उपलब्ध", "delivery": "डिलीवरी", "account": "खाता",
    "bank": "बैंक", "kiraye": "किराये", "aadhaar": "आधार", "katta": "कट्टा", "bandook": "बंदूक",
}

DRUG_NAMES = ["weed", "ganja", "charas", "MDMA", "LSD", "snow", "cocaine", "heroin", "shrooms", "brown sugar"]
DRUG_SLANG = ["maal", "stuff", "snow", "cookies", "chitta", "garda", "afim"]


def render_text(rng: random.Random, template: str, post: dict) -> str:
    w1 = rng.choice([1500, 2000, 2500, 3000, 500, 800])
    return template.format(
        city=rng.choice(CITY_POOL),
        city2=rng.choice(CITY_POOL),
        phone=post.get("phone", ""),
        upie=post.get("upi", ""),
        handle=post.get("handle", ""),
        price=rng.choice(["500", "800", "1200", "1500", "2000", "2500", "3000"]),
        w1=w1 // 1000 if w1 >= 1000 else w1,
        w2=rng.choice(["500", "800", "1000", "1500", "2000", "2500", "3000"]),
        d1=rng.choice(DRUG_NAMES),
        d2=rng.choice(DRUG_SLANG),
        d3=rng.choice(DRUG_NAMES),
        D1=rng.choice(DRUG_NAMES).capitalize(),
    )


# --------------------------------------------------------------------------- image rendering
# Font search is platform-agnostic: Linux distro paths, macOS system fonts and
# Windows fonts (C:\Windows\Fonts) are all tried; PIL's bundled DejaVu is the
# final fallback so posters render everywhere.
_LINUX_SANS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans{}.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans{}.ttf",
]
_MAC_SANS = [
    "/System/Library/Fonts/Supplemental/Arial{}.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial{}.ttf",
]
_WIN_SANS = [
    "C:/Windows/Fonts/arial{}.ttf",
    "C:/Windows/Fonts/segoeui{}.ttf",
    "C:/Windows/Fonts/calibri{}.ttf",
]
_DEVANAGARI = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/sahadeva/Sahadeva.ttf",
    "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",      # macOS
    "/System/Library/Fonts/ITFDevanagari.ttc",                  # macOS
    "C:/Windows/Fonts/Nirmala.ttf",                             # Windows
    "C:/Windows/Fonts/mangal.ttf",
]


def _bundled_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | None:
    try:  # Pillow ships DejaVuSans on every platform
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(str(Path(__import__("PIL").__file__).parent / name), size)
    except Exception:
        return None


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    suffix = "-Bold" if bold else ""
    for table in (_LINUX_SANS, _MAC_SANS, _WIN_SANS):
        for pattern in table:
            c = pattern.format(suffix)
            if Path(c).exists():
                try:
                    return ImageFont.truetype(c, size)
                except OSError:
                    continue
    bundled = _bundled_font(size, bold)
    if bundled is not None:
        return bundled
    return ImageFont.load_default()


def devanagari_font(size: int) -> ImageFont.FreeTypeFont | None:
    for c in _DEVANAGARI:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return None


def make_poster(rng: random.Random, title: str, lines: list[str], phone: str, upi: str) -> Image.Image:
    w, h = rng.choice([(900, 1200), (1080, 1080), (800, 1000)])
    bg = rng.choice([(12, 12, 16), (18, 6, 28), (24, 8, 8), (8, 16, 24), (30, 24, 10)])
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    accent = rng.choice([(255, 196, 0), (0, 200, 255), (255, 64, 64), (64, 255, 128), (255, 128, 0)])
    d.rectangle([0, 0, w, 14], fill=accent)
    d.ellipse([w - 220, h - 220, w + 60, h + 60], fill=tuple(min(255, c + 30) for c in bg))
    big = _font(int(h * 0.075), bold=True)
    d.multiline_text((40, 50), title, font=big, fill=(245, 245, 245), spacing=10)
    y = int(h * 0.24)
    med = _font(int(h * 0.042))
    for ln in lines:
        d.text((40, y), ln, font=med, fill=(230, 230, 220))
        y += int(h * 0.065)
    if any(("\u0900" <= ch <= "\u097F") for ln in lines for ch in ln):
        dev = devanagari_font(int(h * 0.045))
        if dev:
            d.text((40, y + 10), "आज ही ऑर्डर करें - डिलीवरी उपलब्ध", font=dev, fill=accent)
            y += int(h * 0.075)
    d.rectangle([30, h - 210, w - 30, h - 30], outline=accent, width=3)
    d.text((50, h - 195), f"CALL / WHATSAPP: {phone}", font=_font(int(h * 0.036), bold=True), fill=accent)
    d.text((50, h - 137), f"UPI: {upi}", font=_font(int(h * 0.032)), fill=(220, 220, 220))
    d.text((50, h - 90), f"TG {rng.choice(['@', ''])}{rng.choice(['fastdeal', 'boss_delivery', 'night_supply'])}",
           font=_font(int(h * 0.028)), fill=(160, 160, 160))
    return img


def _ascii_only(text: str) -> str:
    """Drop emojis / non-renderable glyphs for screenshot rendering."""
    out = []
    for ch in text:
        if ord(ch) < 0x2500 or "\u0900" <= ch <= "\u097F":
            out.append(ch)
        elif ch in "\n":
            out.append(ch)
        else:
            out.append("")
    return "".join(out)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap text into lines, preferring to break at the nearest space."""
    text = _ascii_only(text).replace("\n", " ")
    lines, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + 1 > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def make_chat_screenshot(rng: random.Random, messages: list[tuple[str, str]]) -> Image.Image:
    w, h = 820, 100 + 150 * len(messages)
    img = Image.new("RGB", (w, h), (14, 22, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 70], fill=(22, 34, 48))
    d.text((24, 24), "Public Channel - sample", font=_font(22, True), fill=(200, 210, 220))
    y = 95
    f = _font(21)
    for who, msg in messages:
        mine = who == "buyer"
        x0 = w - 640 if mine else 24
        color = (28, 60, 52) if mine else (30, 42, 58)
        lines = _wrap(msg, 46)
        bh = 34 * len(lines) + 26
        d.rounded_rectangle([x0, y, x0 + 590, y + bh], radius=14, fill=color)
        for i, ln in enumerate(lines):
            d.text((x0 + 16, y + 12 + i * 34), ln, font=f, fill=(230, 235, 240))
        y += bh + 14
    return img


def make_upi_qr(vpa: str, payee: str, amount: str | None = None) -> Image.Image:
    """Render a scannable UPI QR (uses ``qrcode`` when available, else cv2 markers)."""
    payload = f"upi://pay?pa={vpa}&pn={payee.replace(' ', '%20')}&cu=INR"
    if amount:
        payload += f"&am={amount}"
    try:
        import qrcode

        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=7, border=3)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return img
    except Exception:  # pragma: no cover
        arr = np.zeros((300, 300), dtype=np.uint8)
        rr = random.Random(payload)
        for _ in range(220):
            x, y = rr.randrange(0, 290), rr.randrange(0, 290)
            arr[y : y + 8, x : x + 8] = 255
        return Image.fromarray(arr).convert("RGB")


def qr_payload_for(vpa: str, payee: str, amount: str | None = None) -> str:
    payload = f"upi://pay?pa={vpa}&pn={payee.replace(' ', '%20')}&cu=INR"
    if amount:
        payload += f"&am={amount}"
    return payload


def degrade(img: Image.Image, rng: random.Random, level: str) -> Image.Image:
    if level == "clean":
        return img
    ang = rng.uniform(-18, 18) if level in {"rotated", "harsh"} else 0
    out = img
    if ang:
        out = out.rotate(ang, expand=True, fillcolor=(10, 10, 10))
    arr = np.array(out)
    if level in {"noisy", "lowres", "harsh"}:
        noise = np.random.normal(0, 28 if level != "harsh" else 45, arr.shape)
        arr = np.clip(arr.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr)
    if level in {"lowres", "harsh"}:
        im = im.resize((max(160, im.width // 3), max(160, im.height // 3)), Image.BILINEAR)
        im = im.resize(img.size, Image.BILINEAR)
    if level == "harsh":
        im = im.filter(ImageFilter.GaussianBlur(1.4))
        im = Image.blend(im, Image.new("RGB", im.size, (90, 80, 70)), 0.18)
    return im


# --------------------------------------------------------------------------- voice
_PIPER: dict[str, object] = {}


def _piper_voice(lang: str):
    """Lazy-load a Piper voice (hi_IN-pratham / en_US-lessac) from .models/piper."""
    if lang in _PIPER:
        return _PIPER[lang]
    model = ROOT / ".models" / "piper" / ("hi_IN-pratham-medium.onnx" if lang == "hi" else "en_US-lessac-medium.onnx")
    if not model.exists():
        _PIPER[lang] = None
        return None
    try:
        from piper import PiperVoice

        _PIPER[lang] = PiperVoice.load(str(model))
    except Exception:
        _PIPER[lang] = None
    return _PIPER[lang]


def _tts(text: str, path: Path) -> bool:
    """Offline neural TTS (Piper) -> wav; falls back to espeak-ng, then noise."""
    import wave

    if any("\u0900" <= ch <= "\u097F" for ch in text):
        langs = ["hi"]
    else:
        langs = ["hi", "en"] if any(w in text.lower() for w in ("bhai", "karo", "hai", "maal", "katta")) else ["en"]
    for lang in langs:
        voice = _piper_voice(lang)
        if voice is None:
            continue
        try:
            with wave.open(str(path), "wb") as w:
                voice.synthesize_wav(text, w)
            return True
        except Exception:
            continue
    for cmd in (["espeak-ng"], ["espeak"]):
        try:
            subprocess.run(cmd + ["-v", "hi", "-s", "140", "-w", str(path), text],
                           check=True, capture_output=True, timeout=60)
            return True
        except Exception:
            continue
    return False


def _synth_fallback_wav(path: Path, seconds: float = 3.0, seed: int = 0) -> None:
    import wave

    rate = 16000
    rng = random.Random(seed)
    frames = bytearray()
    for i in range(int(rate * seconds)):
        # speech-like modulated formants + noise
        t = i / rate
        env = 0.4 + 0.4 * math.sin(2 * math.pi * 3.1 * t)
        s = env * (math.sin(2 * math.pi * 140 * t) + 0.5 * math.sin(2 * math.pi * 320 * t))
        s += rng.uniform(-0.05, 0.05)
        frames += int(max(-1, min(1, s * 0.5)) * 32767).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


VOICE_T = [
    "Bhai account kiraye pe available hai, kyc done hai, daily commission دو ہزار rupees. Call karo {spoken}",
    "Fresh maal aa gaya hai, {city} me delivery free hai. Phone number {spoken}",
    "Desi katta available hai with cartridges, price twenty five thousand, contact {spoken}",
    "Ready made aadhaar card aur pan card ban jayega ek din me, call {spoken}",
    "Net banking kit with otp available hai, full access, rent pe le lo, {spoken}",
    "नमस्ते भाई, खाता किराये पर उपलब्ध है, डेली कमीशन मिलेगा, फोन {spoken}",
]


# --------------------------------------------------------------------------- main
def generate(n: int, out: Path, seed: int = 42) -> dict:
    rng = random.Random(seed)
    np.random.seed(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "images").mkdir(exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)

    # --- identifier pools (reused across channels to create coordination) ----
    sellers = []
    for i in range(28):
        sellers.append(
            {
                "phone": fake_phone(rng),
                "upi": fake_upi(rng),
                "handle": fake_handle(rng, rng.choice(["ravi", "aman", "sunil", "kittu", "monu", "shiv"])),
                "city": rng.choice(CITY_POOL),
            }
        )
    # at least one identifier used in 3+ channels
    star = {"phone": "9000011111", "upi": "ravi.k1111@fakebank", "handle": "@ravisupply_ncr",
            "city": "Delhi NCR"}
    sellers.insert(0, star)

    posts: list[dict] = []
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    # 50% benign, 50% illegal spread: drugs 15%, arms 10%, fake_docs 8%, mule 17%
    mix = [("drugs", 0.15), ("arms", 0.10), ("fake_docs", 0.08), ("mule_fin", 0.17), ("benign", 0.50)]
    labels = [l for l, _ in mix for _ in range(int(n * _))]
    while len(labels) < n:
        labels.append("benign")
    rng.shuffle(labels)

    campaign_specs = [
        {"label": "mule_fin", "base": "Account kiraye pe available, KYC done with ATM card + passbook + SIM. Daily commission {w2}. DM {handle}",
         "channels": ["@mule_khata_rent", "@account_kiraye", "@netbanking_kits", "@sim_otp_store", "@city_ki_gully"],
         "seller": sellers[0], "count": 9},
        {"label": "drugs", "base": "Fresh maal available {city}. Best quality, home delivery, rate {w1}. DM {handle}",
         "channels": ["@delhi_night_things", "@ncr_maal_supply", "@mumbai_snow_zone", "@chicha_corner"],
         "seller": sellers[1], "count": 8},
        {"label": "drugs", "base": "🍁 premium stuff landed, discreet packing, {city} delivery, {phone}",
         "channels": ["@delhi_night_things", "@chicha_corner", "@city_ki_gully", "@mumbai_snow_zone", "@lok_mela_up"],
         "seller": sellers[0], "count": 7},
        {"label": "mule_fin", "base": "net banking kit with otp available, full access, rent pe le lo {phone}",
         "channels": ["@netbanking_kits", "@account_kiraye", "@sim_otp_store", "@mule_khata_rent"],
         "seller": sellers[2], "count": 6},
    ]

    def mutate(text: str, strength: float = 0.35) -> str:
        words = text.split()
        for i, w in enumerate(words):
            if rng.random() < strength:
                op = rng.random()
                if op < 0.3 and len(w) > 4:
                    words[i] = w[:-1] + "s"
                elif op < 0.55:
                    words[i] = w.upper() if rng.random() < 0.5 else w
                elif op < 0.75:
                    words[i] = w + rng.choice(["!!", "••", "🔥", "✅", "", " bhai"])
                else:
                    words[i] = w.replace("a", "@") if "a" in w else w
        return " ".join(words)

    idx = 0
    # --- coordinated campaigns ---
    for spec in campaign_specs:
        for k in range(spec["count"]):
            ch = spec["channels"][k % len(spec["channels"])]
            seller = spec["seller"]
            text = render_text(rng, spec["base"], dict(seller))
            text = mutate(text, strength=0.25 + 0.05 * k)
            ts = start + timedelta(days=k * 0.8, hours=rng.randint(0, 10))
            posts.append(
                {
                    "post_id": f"gen_{idx:05d}",
                    "platform": "telegram",
                    "channel": ch,
                    "timestamp": ts.isoformat(),
                    "text": text,
                    "label": spec["label"],
                    "campaign": f"camp_{campaign_specs.index(spec) + 1}",
                    "seller": seller,
                }
            )
            idx += 1

    # paraphrase (unseen-slang style) training rows: replaced below by generation loop
    templates = {"drugs": DRUG_T, "arms": ARM_T, "fake_docs": DOCS_T, "mule_fin": MULE_T, "benign": BENIGN_T}
    while idx < n:
        label = labels[idx] if idx < len(labels) else "benign"
        ch = rng.choice(CHANNELS)[0]
        seller = rng.choice(sellers)
        tmpl = rng.choice(templates[label])
        post = {"phone": seller["phone"], "upi": seller["upi"], "handle": seller["handle"]}
        if label in {"drugs", "arms"} and rng.random() < 0.12:
            text = tmpl
        elif label == "benign" and rng.random() < 0.15:
            text = rng.choice(UNSEEN_T).replace("{handle}", seller["handle"]).format(
                city=seller["city"], phone=seller["phone"], handle=seller["handle"]
            )
        elif label != "benign" and rng.random() < 0.08:
            text = rng.choice([t for t in UNSEEN_T]).replace("{handle}", seller["handle"]).format(
                city=seller["city"], phone=seller["phone"], handle=seller["handle"]
            )
        elif label == "benign":
            text = render_text(rng, tmpl, post)
        else:
            text = render_text(rng, tmpl, post)
        # obfuscation / noise
        if label != "benign" and rng.random() < 0.45:
            style = rng.random()
            if style < 0.3:
                text = text.replace(seller["phone"], obfuscate_phone(rng, seller["phone"]))
            elif style < 0.45:
                text = leetify(rng, text)
            elif style < 0.6:
                text = typoize(rng, text)
            elif style < 0.72 and label == "drugs":
                text = text + "\n" + rng.choice(["🍁🔌💨", "❄️💊🍄", "🚬🔌"])
        if rng.random() < 0.08:
            text = text + " " + rng.choice(["#delhi", "#noida", "#trending", "#offer"])
        ts = start + timedelta(days=rng.uniform(0, 45), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        posts.append(
            {
                "post_id": f"gen_{idx:05d}",
                "platform": "telegram",
                "channel": ch,
                "timestamp": ts.isoformat(),
                "text": text,
                "label": label,
                "seller": seller,
            }
        )
        idx += 1

    # --- paraphrased training rows (teach the embedding head negotiated slang) --
    para_rows = [
        ("Premium garden treats available in the usual way, farm price, discreet drop, inbox", "drugs"),
        ("Party supplies are back in town, hidden parcel delivery tonight", "drugs"),
        ("New collection landed at the usual spot, discreet packing, ping me on telegram", "drugs"),
        ("Paper work service ready in one day, any identity document of your choice", "fake_docs"),
        ("Documents for any identity, delivered to your door by evening", "fake_docs"),
        ("Empty bank profiles available with all codes working, weekly payout", "mule_fin"),
        ("We buy ready to use money access documents, fair deal, no questions", "mule_fin"),
        ("Working bank profiles for hire, all codes active, weekly payout", "mule_fin"),
        ("Hardware for protection available, brand new condition, city delivery", "arms"),
        ("Fresh papers ready for any name you want, same-day service", "fake_docs"),
    ]
    benign_para = [(t, "benign") for t in BENIGN_PARAPHRASE_T]
    para_all = para_rows + benign_para * 6 + [(t, "benign") for t in FERTILIZER_LOATHS]
    rng.shuffle(para_all)
    para_idx = 0
    for text, label in para_all:
        seller = rng.choice(sellers)
        text = text.format(city=seller["city"], phone=seller["phone"], handle=seller["handle"])
        if label != "benign" and rng.random() < 0.7:
            text = f"{text} {obfuscate_phone(rng, seller['phone'])}"
        ts = start + timedelta(days=rng.uniform(0, 45), hours=rng.randint(0, 23))
        posts.append({
            "post_id": f"para_{para_idx:04d}",
            "platform": "telegram",
            "channel": rng.choice(CHANNELS)[0],
            "timestamp": ts.isoformat(),
            "text": text,
            "label": label,
            "seller": seller,
        })
        para_idx += 1

    # --- media: posters, screenshots, QRs, voice notes ---------------------
    media_posts = [p for p in posts if p["label"] != "benign"]
    rng.shuffle(media_posts)
    image_sets: list[dict] = []
    for i, p in enumerate(media_posts[:190]):
        kind = rng.random()
        s = p["seller"]
        if kind < 0.55:
            title = {"drugs": "PREMIUM MAAL", "arms": "DESI KATTA", "fake_docs": "READY MADE DOCS",
                     "mule_fin": "ACCOUNT RENT"}[p["label"]]
            lines = {
                "drugs": ["Fresh stock, pure quality", "Home delivery 24x7", "Cash / UPI accepted"],
                "drugs_bulk": ["1 tola - 25k, 10g - 3k", "Delivery all India", "TG fastdeal 24x7"],
                "arms": ["9mm + cartridges available", "Lowest price, NO ID", "Meet point: " + s["city"]],
                "fake_docs": ["Aadhaar / PAN / DL", "100% original look", "Same day delivery"],
                "mule_fin": ["KYC done account", "ATM + passbook + SIM", "Daily commission milta hai"],
            }[p["label"]]
            img = make_poster(rng, title, lines, s["phone"], s["upi"])
        elif kind < 0.8:
            seller_lines = render_text(rng, rng.choice(DRUG_T + MULE_T + DOCS_T + ARM_T), dict(s))
            img = make_chat_screenshot(
                rng,
                [("seller", seller_lines), ("buyer", "rate kya hai bhai?"),
                 ("seller", f"contact karo {s['phone']} / {s['upi']}")],
            )
        else:
            amount = rng.choice([None, "500", "1500", "5000"])
            img = make_upi_qr(s["upi"], rng.choice(["Ravi Kumar", "Sunil S", "Aman K", "Mule Raj"]), amount)
            p["qr_payload"] = qr_payload_for(s["upi"], "Ravi Kumar", amount)
        level = rng.choices(["clean", "noisy", "rotated", "lowres", "harsh"], weights=[5, 2, 2, 1, 1])[0]
        out_img = degrade(img, rng, level)
        fname = f"images/img_{i:03d}.png"
        out_img.save(out / fname)
        p["image_path"] = fname
        p["image_kind"] = "qr" if kind >= 0.8 else "poster"
        p["media_variant"] = level
        # near-duplicate pHash variant (same poster, light crop/brightness shift)
        if rng.random() < 0.35:
            dup = img.copy()
            w, h = dup.size
            dup = dup.crop((int(w * 0.03), int(h * 0.03), int(w * 0.97), int(h * 0.97))).resize((w, h))
            dup = Image.eval(dup, lambda v: min(255, int(v * 1.06)))
            dup_name = f"images/img_{i:03d}_dup.png"
            dup.save(out / dup_name)
            image_sets.append({"original": fname, "duplicate": dup_name})

    voice_src = media_posts[:26]
    tts_ok = False
    for i, p in enumerate(voice_src):
        s = p["seller"]
        spoken = " ".join(
            {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
             "6": "six", "7": "seven", "8": "eight", "9": "nine"}[c] for c in s["phone"]
        )
        tpl = VOICE_T[i % len(VOICE_T)]
        text = tpl.replace("{spoken}", spoken).replace("{city}", s["city"]).replace("دو ہزار", "do hazaar")
        fname = f"audio/voice_{i:03d}.wav"
        ok = _tts(text, out / fname)
        tts_ok = tts_ok or ok
        if not ok:
            _synth_fallback_wav(out / fname, seconds=rng.uniform(2.5, 4.0), seed=i)
        p["audio_path"] = fname
        p["audio_transcript_truth"] = text

    # --- lexicon holdout (unseen paraphrases) ------------------------------
    holdout = []
    for t in UNSEEN_T + ["Bhai pure garden produce available, ping {handle}"] * 2:
        holdout.append({"text": render_text(rng, t, {"handle": "@dealer_x", "phone": "9000012345"}).replace(
            "{handle}", "@dealer_x"), "label": "illicit"})

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "n_posts": len(posts),
        "label_counts": {l: sum(1 for p in posts if p["label"] == l) for l in LABELS},
        "channels": len({p["channel"] for p in posts}),
        "images": len(list((out / "images").glob("*.png"))),
        "audio": len(list((out / "audio").glob("*.wav"))),
        "tts": "espeak-ng" if tts_ok else "synthetic-fallback",
        "image_sets": image_sets,
    }
    with open(out / "posts.json", "w", encoding="utf-8") as fh:
        json.dump(posts, fh, ensure_ascii=False, indent=1)
    with open(out / "meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    with open(out / "lexicon_holdout.json", "w", encoding="utf-8") as fh:
        json.dump(holdout, fh, ensure_ascii=False, indent=1)
    with open(out / "contacts.json", "w", encoding="utf-8") as fh:
        json.dump(sellers, fh, indent=1)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the synthetic SPECTRA dataset")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "generated")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    meta = generate(args.n, args.out, args.seed)
    print(json.dumps(meta, indent=1)[:1200])


if __name__ == "__main__":
    main()
