"""Cross-platform launcher: build the dashboard (if needed) and serve SPECTRA.

Works on macOS, Linux and Windows by resolving the interpreter inside ``.venv``
for the current OS instead of assuming ``bin/python``.

    python scripts/run_dev.py                # API + dashboard on :8000
    python scripts/run_dev.py --dev          # + Vite dev server on :5173
    python scripts/run_dev.py --port 8080

Stopping this process (Ctrl-C) stops the API and any dev server it started.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
sys.path.insert(0, str(ROOT / "scripts"))


def child_env(port: int | None = None) -> dict:
    """Environment with all per-OS ML fixes (single source: scripts/setup.py)."""
    from setup import venv_env  # local module, same folder

    env = venv_env()
    if port:
        env["API_PORT"] = str(port)
    return env


def venv_uvicorn() -> list[str]:
    uv = ROOT / ".venv" / ("Scripts/uvicorn.exe" if IS_WINDOWS else "bin/uvicorn")
    if uv.exists():
        return [str(uv)]
    py = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not py.exists():
        raise SystemExit("No virtualenv found. Run `python scripts/setup.py` first.")
    return [str(py), "-m", "uvicorn"]


def npm() -> list[str]:
    return ["npm.cmd" if IS_WINDOWS else "npm"]


def build_frontend() -> None:
    if (ROOT / "frontend" / "dist" / "index.html").exists():
        return
    print("== building dashboard ==", flush=True)
    subprocess.run(npm() + ["run", "build"], cwd=str(ROOT / "frontend"), env=child_env(), check=True)


def _kill_previous_server() -> None:
    """Stop an API server started by an earlier run (recorded in data/api.pid)."""
    pidfile = ROOT / "data" / "api.pid"
    if not pidfile.exists():
        return
    try:
        raw = pidfile.read_text().strip()
        pid = int(json.loads(raw)["api"]) if raw.startswith("{") else int(raw)
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"[run] stopped previous API process {pid}", flush=True)
        time.sleep(1.0)
    except (ValueError, KeyError, ProcessLookupError, PermissionError):
        pass
    finally:
        pidfile.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dev", action="store_true", help="also start the Vite dev server on :5173")
    args = ap.parse_args()

    build_frontend()
    _kill_previous_server()

    children: list[subprocess.Popen] = []
    if args.dev:
        children.append(subprocess.Popen(npm() + ["run", "dev"], cwd=str(ROOT / "frontend"),
                                         env=child_env(args.port)))

    api = subprocess.Popen(
        venv_uvicorn() + ["app.main:app", "--host", args.host, "--port", str(args.port)],
        cwd=str(ROOT / "backend"), env=child_env(args.port),
    )
    children.append(api)

    pidfile = ROOT / "data" / "api.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(json.dumps({"api": api.pid, "port": args.port, "started": int(time.time())}))

    def _stop(*_: object) -> None:
        pidfile.unlink(missing_ok=True)
        for c in children:
            if c.poll() is None:
                c.terminate()
        for c in children:
            try:
                c.wait(timeout=10)
            except subprocess.TimeoutExpired:
                c.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    print(f"\nSPECTRA  →  http://localhost:{args.port}/  (API docs at /docs)"
          + ("   dev UI → http://localhost:5173/" if args.dev else ""), flush=True)
    while True:
        time.sleep(0.5)
        if api.poll() is not None:
            _stop()


if __name__ == "__main__":
    main()
