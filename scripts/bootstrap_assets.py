"""Download the small offline TTS voices used to synthesise voice notes.

Piper voices (~60 MB each) are stored in ``.models/piper``. If the network is
unavailable the generator falls back to espeak-ng and then to a synthetic wav,
so this script never blocks the demo.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".models" / "piper"
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = [
    ("hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx", "hi_IN-pratham-medium.onnx"),
    ("hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx.json", "hi_IN-pratham-medium.onnx.json"),
    ("en/en_US/lessac/medium/en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"),
    ("en/en_US/lessac/medium/en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json"),
]


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    ok = True
    for remote, name in VOICES:
        target = DEST / name
        if target.exists() and target.stat().st_size > 1000:
            print(f"[piper] {name} present")
            continue
        try:
            print(f"[piper] downloading {name} …")
            urllib.request.urlretrieve(f"{BASE}/{remote}", target)
        except Exception as exc:
            ok = False
            print(f"[piper] {name} unavailable ({exc}) - voice notes will use fallback TTS")
    print("[piper] ready" if ok else "[piper] partial - fallback TTS will be used for missing voices")


if __name__ == "__main__":
    main()
