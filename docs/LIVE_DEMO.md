# Live demo: credentials and deployment

Two pieces deploy separately:

| Piece | Where | Why |
|---|---|---|
| Dashboard (`frontend/`) | **Vercel** | static React build, fast CDN |
| API + collector + OCR + bot (`backend/`) | **Render / Railway / Fly / a VPS** | needs a persistent process, disk and a Telethon session; Vercel functions cannot keep the bot or auto-pilot alive |

The dashboard is already wired to call any backend through `VITE_API_URL`. CORS allows `*.vercel.app`, `*.trycloudflare.com`, `*.ngrok-free.app` and `*.loca.lt`.

---

## 1. What goes in `.env` (repo root)

Copy `.env.example` to `.env`, then fill these in:

| Key | Where to get it |
|---|---|
| `BHARATCODE_API_KEY` | <https://bharatcode.ai/account> → Developer API. Starts with `bc_live_…`. Keep it server-side only. |
| `BHARATCODE_MODEL` | `glm-5.3-flash` (verify with `GET /models`). |
| `TELEGRAM_API_ID` | <https://my.telegram.org> → API development tools → create an app. Numeric id. |
| `TELEGRAM_API_HASH` | Same page, the 32-character hash. |
| `TELEGRAM_SESSION` | **Leave empty.** `python scripts/telegram_login.py` creates `.telegram_session/spectra.session` after you enter your phone + OTP + 2FA. |
| `TELEGRAM_BOT_TOKEN` | Telegram → **@BotFather** → `/newbot` → token like `123456:ABC…`. |
| `TELEGRAM_BOT_CHAT_ID` | Optional. Send `/start` to your bot; the bot stores subscribers itself. Get your id from **@userinfobot** if you want it pinned. |
| `DATABASE_URL` | Optional. Supabase → Project settings → Database → connection URI. Leave empty for SQLite. |
| `SUPABASE_URL` / `SUPABASE_KEY` / `SUPABASE_SERVICE_KEY` | Optional, only if you want Supabase Postgres/Storage. |

Everything else in `.env.example` already has a working default.

---

## 2. First run (local, proves every feature)

```powershell
# from spectra/
python scripts/setup.py                 # deps + data + model + seed + build
python scripts/telegram_login.py        # one-time: phone, OTP, 2FA
python scripts/run_dev.py               # http://localhost:8000
```

Then open **Channels** in the dashboard:

* **Join & watch** — paste `@channel` or `t.me/channel`. The Telethon session joins it, stores it in the watchlist, and the auto-pilot starts sweeping it every 15 minutes.
* **Discover** — search by keyword (`ganja`, `katta`, `mule account`) or press **Auto-discover & join**.
* **Scan** — pulls the last 50 messages, runs the hybrid classifier + GLM + OCR/QR, and creates evidence-hashed alerts.

Bot commands: `/start`, `/status`, `/alerts`, `/channels`, `/join @channel`, `/scan @channel`, `/stop`.

---

## 3. Deploy the dashboard to Vercel

The repo already contains `frontend/vercel.json`.

```powershell
cd frontend
npx vercel login
npx vercel --prod
```

When Vercel asks for the project root, deploy from `frontend/` (framework: Vite, output: `dist`).

Set the API URL **before** the production build:

```powershell
npx vercel env add VITE_API_URL production
# paste: https://<your-public-backend-host>
npx vercel --prod
```

If you only have a laptop for the demo, expose the local API with a tunnel and use that URL:

```powershell
npx localtunnel --port 8000
# copy the https://….loca.lt URL into VITE_API_URL, then redeploy
```

> Vercel cannot host the collector, the Telegram session, OCR models or the bot. If you deploy only the frontend, the dashboard will load but show **API offline**.

---

## 4. Deploy the backend (persistent host)

Render / Railway / Fly, or any Linux VPS:

* Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir backend`
* Python 3.12, **2 GB RAM** minimum (torch + EasyOCR).
* System packages: `ffmpeg`, `libzbar0`, `tesseract-ocr`, `tesseract-ocr-hin`.
* Persistent disk mounted at `data/` (SQLite, uploads, evidence) **and** `.telegram_session/` (login session).
* Environment: everything from `.env`. Set `CORS_ORIGINS` to include your Vercel URL (the regex already covers `*.vercel.app`).
* Run `python scripts/telegram_login.py` once on the server (or copy the `.session` file to the persistent disk).

Health check: `GET /health` returns model, database, LLM, Telegram and auto-pilot status.

---

## 5. Demo checklist (2 minutes before judging)

1. `GET /health` → `status: ok`, `model: spectra-hybrid-v1`, `llm.configured: true`.
2. Dashboard → **Watchfloor** shows alerts, emoji signals and GLM-confirmed flags.
3. **Channels** → **Scan** one channel → progress bar → new alerts appear.
4. **Alerts** → open a record → evidence text, emoji signals, GLM verdict, QR/UPI identity, **Verify integrity**.
5. **Identifier Registry** → click a UPI/phone → cross-channel identity profile.
6. Send `/status` to the bot → counts return.
7. Toggle light/dark in the top bar.

## Notes

* The LLM endpoint can return `503` under load. The scanner retries, then degrades gracefully; the local engine still flags. Use **Re-scan with GLM** on an alert when capacity returns.
* Rotate the BharatCode key after the hackathon.
* Never commit `.env` or `.telegram_session/`.
