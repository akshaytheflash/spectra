"""One-off Telegram login for the read-only collector.

Run:  make telegram-login   (needs TELEGRAM_API_ID / TELEGRAM_API_HASH in .env)
This creates `.telegram_session/spectra.session`. The collector never sends
messages, never joins private groups and only reads public broadcast channels.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402


async def main() -> None:
    if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first "
                         "(https://my.telegram.org -> API development tools).")
    from telethon import TelegramClient  # type: ignore

    session_dir = ROOT / ".telegram_session"
    session_dir.mkdir(exist_ok=True)
    client = TelegramClient(str(session_dir / "spectra"), int(settings.TELEGRAM_API_ID), settings.TELEGRAM_API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"Logged in as {getattr(me, 'username', None) or me.id}. Session stored in {session_dir}/spectra.session")
    print("SPECTRA will only read public channel history.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
