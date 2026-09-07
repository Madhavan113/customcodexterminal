#!/usr/bin/env python3
"""Fit a credited photograph to the native Terminal wallpaper size, colors untouched."""

import argparse
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SIZE = (3840, 2160)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--photo",
        type=Path,
        default=ROOT / "assets/wallpaper-source.jpg",
        help="Source photograph; see assets/SOURCES.md for credits",
    )
    args = parser.parse_args()
    # The photograph as shot: fitted to the display, no tint, no darkening, no overlay.
    combined = ImageOps.fit(
        Image.open(args.photo).convert("RGB"),
        SIZE,
        method=Image.Resampling.LANCZOS,
    )
    destination = ROOT / "terminal/backgrounds/noir-velocity.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    combined.save(destination, optimize=True)
    print(destination)


if __name__ == "__main__":
    main()
