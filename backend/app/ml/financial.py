"""Financial-ad detector for mule-account recruitment.

Classifies an ad into:
  type   : bank_account | atm_debit_card | credit_card | net_banking_kit | sim
  intent : BUY | SELL | RENT
  risk   : HIGH (sale/rent + commission/payment + contact) |
           MEDIUM (explicit offer without contact or payment) |
           LOW (vague/ambiguous)

Every decision carries the supporting evidence phrases (original text spans),
which are rendered in the alert detail view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS_BUY = [
    r"\bbuy(ing)?\b", r"\bneed(ed)?\b", r"\bchahiye\b", r"\bchahie\b", r"\bwant(ed)?\b", r"\brequired?\b",
    r"\bkhareed", r"\bkharidna\b", r"\bchahiye\s+hai\b", r"\bमुझे\b", r"\bचाहिए\b", r"\blena hai\b",
    r"\bखरीद", r"\brequirement\b",
]
INTENTS_SELL = [
    r"\bsell(ing)?\b", r"\bsale\b", r"\bbechna\b", r"\bavailable\b", r"\bgiving away\b", r"\bmil raha\b",
    r"\bbechenge\b", r"\bदेना\b", r"\bबेचना\b", r"\bबेच\b", r"\bde rahe\b", r"\bde\s+raha\b", r"\bde\s+do\b",
    r"\bsupply\b", r"\bproviding\b",
]
INTENTS_RENT = [
    r"\brent(ing|al|ed)?\b", r"\bkiraye\b", r"\bkiraya\b", r"\bkraye\b", r"\bkirye\b", r"\bकिराये\b",
    r"\bकिराए\b", r"\bon rent\b", r"\brent pe\b", r"\brent par\b", r"\brent\s+mein\b", r"\bhire\b",
    r"\blease\b", r"\bbook(ing)?\b",
]
COMMISSION = [
    r"\bdaily\s+(?:commission|comission|payout|earning)s?\b", r"\bcommission\s+(?:rs\.?\s*)?\d{2,5}\b",
    r"\b(?:rs\.?|₹)\s*\d{2,5}\s*(?:per|/)\s*day\b", r"\b\d{2,5}\s*(?:rupees|rs|₹)\s*(?:daily|per day)\b",
    r"\bdaily\s+(?:rs\.?|₹)?\s*\d{3,5}\b", r"\bcommission\b", r"\bcomission\b", r"\bpayout\b",
    r"\bकमीशन\b", r"\bprofit\s+shar(e|ing)\b", r"\bearning\b", r"\bwe pay\b",
]
IDENTIFIER_HINTS = [
    r"\+?91[\s\-]?[6-9]\d{4}[\s\-]?\d{5}", r"\b[6-9]\d{9}\b", r"\b9\s\d\s\d\s\d",
    r"\b[a-z0-9._\-]+@[a-z]{2,}\b", r"@[A-Za-z][A-Za-z0-9_]{3,}",
]
OFFER_STRENGTH = [
    r"\bkyc\s*done\b", r"\bfull\s*kit\b", r"\bwith\s+otp\b", r"\botp\s*(linked|access)\b",
    r"\bpassbook\b", r"\bcheque\s*book\b", r"\bwith\s+pin\b", r"\bfresh\b", r"\bactive\b",
]

TYPE_PATTERNS: dict[str, list[str]] = {
    "net_banking_kit": [r"net\s*bank(ing)?\s*(kit)?", r"internet\s*banking", r"netbanking",
                        r"\bkit\b", r"\botp\s*(access|link)", r"\bनेट\s*बैंकिंग\b"],
    "atm_debit_card": [r"\batm\s*card\b", r"\bdebit\s*card\b", r"\batm\b", r"\bcard\s*(with\s*)?pin\b",
                       r"\bpassbook\s*\+?\s*atm\b", r"\bएटीएम\b"],
    "credit_card": [r"\bcredit\s*card\b", r"\bक्रेडिट\s*कार्ड\b"],
    "sim": [r"\bsim\b", r"\botp\s*sim\b", r"\besim\b", r"\bsim\s*card\b", r"\bसिम\b"],
    "bank_account": [r"\b(?:bank\s*|saving[s]?\s*|current\s*|khata\s*|jan\s*dhan\s*)account\b",
                     r"\baccount\s*kit\b", r"\bkhata\b", r"\bखाता\b", r"\bpassbook\b", r"\bचेक\s*बुक\b"],
}
MULE_CONTEXT = [
    r"\brent\b", r"\bkiraye\b", r"\bsale\b", r"\bsell\b", r"\bcommission\b", r"\bکمیشن\b", r"\bकमीशन\b",
    r"\bdaily\b", r"\baccount\b", r"\bखाता\b", r"\bkyc\b", r"\botp\b", r"\bkit\b", r"\bsim\b",
    r"\batm\b", r"\bcard\b", r"\bpassbook\b", r"\bsalary\b", r"\bmoney\s*transfer\b",
]


# Legitimate retail / service phrasing. When one of these patterns is present and
# there is no mule signal (rent/sale/commission/kit/otp) the ad is not mule
# recruitment -- it is a store, a bank or an official service.
LEGIT_PATTERNS = [
    r"\b(buy|get|apply for|order|upgrade) (a |an |the |your )?(new )?(sim|esim|sim card)\b",
    r"\bsim (recharge|activation|activated plan|portability|port|upgrade)\b",
    r"\b(online|nearest) (store|outlet|branch|retailer|shop|dealer)\b",
    r"\b(instant|digital|aadhaar|ekyc) (ekyc|kyc)\b.*\b(store|online|branch|sim)\b",
    r"\b(open|opening) (a |an |your )?(new )?(savings|current|salary|zero balance) account\b",
    r"\bcredit card (offer|offers|apply|application|bill|emi)\b|\bapply for (a )?credit card\b",
    r"\bdebit card (blocked|activation|replacement|apply|new|request)\b",
    r"\b(visit|at) (your |our )?(nearest )?(branch|store|outlet|dealer)\b",
    r"\b(plan|tariff|offer|scheme|loan emi|interest rate)\b",
]
_MULE_SIGNAL = [
    r"\bkiraye?\b", r"\bkraye\b", r"\brent\b", r"\bon rent\b", r"\bsale\b", r"\bsell\b",
    r"\bcommission\b", r"\bcomission\b", r"\bdaily (payout|earning)", r"\bkit\b", r"\botp\b",
    r"\bpassbook\b", r"\bcheque book\b", r"\bकिराये\b", r"\bबेचना\b",
]


@dataclass
class FinAdResult:
    is_financial: bool
    types: list[str] = field(default_factory=list)
    type: str | None = None
    intent: str | None = None
    risk: str = "LOW"
    evidence: list[dict] = field(default_factory=list)
    commission: str | None = None
    has_contact: bool = False
    has_offer: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "is_financial": self.is_financial,
            "type": self.type,
            "types": self.types,
            "intent": self.intent,
            "risk": self.risk,
            "commission": self.commission,
            "has_contact": self.has_contact,
            "evidence": self.evidence,
            "notes": self.notes,
        }


def _find(patterns: list[str], text: str) -> list[tuple[int, int, str]]:
    out = []
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            out.append((m.start(), m.end(), m.group(0)))
    return sorted(set(out))


def detect(text: str, identifiers: list[str] | None = None) -> FinAdResult:
    """Classify a post as a financial/mule ad with type, intent and risk."""
    if not text:
        return FinAdResult(is_financial=False)
    types_found: list[str] = []
    ev: list[dict] = []
    type_scores: dict[str, int] = {}
    for t, patterns in TYPE_PATTERNS.items():
        hits = _find(patterns, text)
        if hits:
            type_scores[t] = len(hits)
            types_found.append(t)
            for s, e, raw in hits[:3]:
                ev.append({"kind": "type", "type": t, "phrase": raw, "start": s, "end": e})
    mule_ctx = _find(MULE_CONTEXT, text)
    has_contact = bool(identifiers) or bool(_find(IDENTIFIER_HINTS, text))
    rent = _find(INTENTS_RENT, text)
    sell = _find(INTENTS_SELL, text)
    buy = _find(INTENTS_BUY, text)
    comm = _find(COMMISSION, text)
    offer = _find(OFFER_STRENGTH, text)

    # Intent priority: explicit rent > sell > buy; "available" + "chahiye" is an offer.
    intent = None
    if rent and any(w in text.lower() for w in ("rent", "kiray", "kraye", "kirye", "hire", "lease", "किरा")):
        intent = "RENT"
    elif len(sell) >= len(buy) and sell:
        intent = "SELL"
    elif buy:
        intent = "BUY"
    elif rent:
        intent = "RENT"

    for lst, kind in ((rent, "rent"), (sell, "sell"), (buy, "buy"), (comm, "commission"), (offer, "offer")):
        for s, e, raw in lst[:3]:
            ev.append({"kind": kind, "phrase": raw, "start": s, "end": e})

    commission_val = None
    if comm:
        commission_val = comm[0][2]

    # A financial ad must reference an account/card/SIM/kit AND either a rent/sale
    # intent or a commission/payout promise. Otherwise it is just banking talk.
    is_fin_pre = bool(type_scores) and bool(intent or comm)
    is_financial = is_fin_pre and len(mule_ctx) >= 1
    if not is_financial:
        return FinAdResult(
            is_financial=False, types=types_found,
            type=max(type_scores, key=type_scores.get) if type_scores else None,
            intent=intent, risk="LOW", evidence=ev[:6], has_contact=has_contact,
            notes=["No mule-recruitment signal (needs account/card/SIM + rent/sale intent or commission)."],
        )

    primary = None
    if type_scores:
        # Kits are the highest-value signal; prefer them when present with account.
        order = ["net_banking_kit", "atm_debit_card", "credit_card", "sim", "bank_account"]
        primary = sorted(type_scores, key=lambda k: (-type_scores[k], order.index(k)))[0]
    if len(types_found) > 1:
        ev.append({"kind": "combo", "phrase": " + ".join(types_found), "start": 0, "end": 0})

    # Legitimate store/bank/service phrasing without any mule signal -> not an ad.
    legit = any(re.search(p, text, re.I) for p in LEGIT_PATTERNS)
    mule_signal = any(re.search(p, text, re.I) for p in _MULE_SIGNAL)
    if legit and not mule_signal:
        return FinAdResult(
            is_financial=False, types=types_found,
            type=max(type_scores, key=type_scores.get) if type_scores else None,
            intent=intent, risk="LOW", evidence=ev[:6], has_contact=has_contact,
            notes=["Legitimate store / bank / service phrasing (no mule signal)."],
        )

    has_payment = bool(comm) or bool(re.search(r"(?i)\b\d{3,5}\s*(?:per day|/day|daily|rs|₹|rupees)\b", text))
    if (intent in ("SELL", "RENT", "BUY")) and has_payment and has_contact:
        risk = "HIGH"
    elif intent and not has_contact and not has_payment and (offer or mule_signal):
        risk = "MEDIUM"
    elif intent or has_payment:
        risk = "LOW" if not has_contact else "MEDIUM"
    else:
        risk = "LOW"
    notes = []
    if risk == "HIGH":
        notes.append("Explicit " + intent.lower() + " intent + payment/commission + contact identifier.")
    elif risk == "MEDIUM":
        missing = "contact identifier" if not has_contact else "payment/commission"
        notes.append(f"Explicit offer but missing {missing}.")
    else:
        notes.append("Vague or ambiguous offer.")
    return FinAdResult(
        is_financial=True, types=types_found, type=primary, intent=intent, risk=risk,
        evidence=ev[:12], commission=commission_val, has_contact=has_contact, has_offer=bool(offer),
        notes=notes,
    )
