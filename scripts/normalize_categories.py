"""Normalise legacy financial sub-type categories to canonical UI categories."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models import Alert, Campaign  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402

MAP = {
    "bank_account": "mule_account",
    "atm_debit_card": "atm_card",
    "sim": "sim_card",
    "net_banking": "net_banking_kit",
}


def main() -> None:
    init_db()
    with session_scope() as db:
        changed = 0
        for row in db.execute(select(Alert)).scalars().all():
            new = MAP.get(row.category)
            if new:
                row.category = new
                changed += 1
        for camp in db.execute(select(Campaign)).scalars().all():
            new = MAP.get(camp.category)
            if new:
                camp.category = new
        print(f"[normalize] alerts updated: {changed}")


if __name__ == "__main__":
    main()
