"""Blur a poster locally to create a realistic 'leaked screenshot' variant.

Stand-alone helper (no cloud calls): upscales, applies a soft blur and mild
noise, and saves the result next to the original. Used by the media-use skill
and by ``scripts/make_demo_media.py`` to show OCR robustness on degraded input.

Usage:
    python scripts/media_tools.py blur data/generated/images/img_005.png --sigma 2.2
    python scripts/media_tools.py blur IMAGE --out data/uploads/
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def blur_image(src: Path, dst: Path | None = None, sigma: float = 2.0, noise: float = 6.0,
               scale: float = 1.5) -> Path:
    """Blur + degrade an image and write it to ``dst`` (default: ``<stem>_blur.png``)."""
    import cv2

    raw = np.fromfile(str(src), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image: {src}")
    h, w = img.shape[:2]
    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma)
    if noise > 0:
        img = np.clip(img.astype(np.float32) + np.random.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    out = dst or src.with_name(f"{src.stem}_blur{src.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blur")
    b.add_argument("src", type=Path)
    b.add_argument("--out", type=Path)
    b.add_argument("--sigma", type=float, default=2.0)
    b.add_argument("--noise", type=float, default=6.0)
    args = ap.parse_args()
    if args.cmd == "blur":
        print(blur_image(args.src, args.out, sigma=args.sigma, noise=args.noise))


if __name__ == "__main__":
    main()
