"""Install OS-level tools that Python packages cannot provide.

  * ffmpeg   (voice notes: any audio -> 16 kHz wav before Whisper)
  * libzbar  (optional extra QR decoder; OpenCV's detector works without it)
  * tesseract + hin (OCR fallback when easyocr is unavailable)

Linux: uses apt (Debian/Ubuntu) and prints the equivalent dnf/pacman line.
macOS: uses Homebrew (installs it on request if missing? no - just instructs).
Windows: instructs winget commands and checks PATH.

Run:  python scripts/install_system_deps.py          # check + install where safe
      python scripts/install_system_deps.py --check  # report only
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str]) -> bool:
    print("  $", " ".join(cmd), flush=True)
    try:
        subprocess.run(cmd, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  ! failed: {exc}", flush=True)
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, do not install")
    args = ap.parse_args()
    system = platform.system()

    print(f"system tools on {system}:")
    print(f"  ffmpeg    : {'ok' if have('ffmpeg') else 'MISSING (voice notes need it)'}")
    print(f"  tesseract : {'ok' if have('tesseract') else 'missing (optional: easyocr is primary)'}")
    print(f"  zbar      : {'ok' if have('zbarimg') or system == 'Windows' else 'missing (optional: OpenCV QR works)'}")

    if args.check:
        return

    if system == "Linux" and have("apt-get") and not have("ffmpeg"):
        print("\nInstalling ffmpeg (sudo password may be required):")
        run(["sudo", "apt-get", "update"])
        run(["sudo", "apt-get", "install", "-y", "ffmpeg", "libzbar0", "tesseract-ocr", "tesseract-ocr-hin"])
        print("\n(dnf/Fedora: sudo dnf install ffmpeg zbar tesseract tesseract-langpack-hin)")
        print("(pacman/Arch: sudo pacman -S ffmpeg zbar tesseract tesseract-data-hin)")
    elif system == "Darwin":
        if not have("ffmpeg"):
            print("\nmacOS: install ffmpeg with Homebrew:")
            print("  brew install ffmpeg zbar tesseract tesseract-lang")
    elif system == "Windows":
        if not have("ffmpeg"):
            print("\nWindows: install ffmpeg with winget (then reopen the terminal):")
            print("  winget install Gyan.FFmpeg")
            print("  winget install UB-Mannheim.TesseractOCR   (optional OCR fallback)")
    else:
        print("\nInstall ffmpeg with your OS package manager.")

    if not have("ffmpeg"):
        print("\nNOTE: everything else (text/OCR/QR, tags, graph, dashboard) works "
              "without ffmpeg; only voice-note ingestion needs it.")


if __name__ == "__main__":
    sys.exit(main())
