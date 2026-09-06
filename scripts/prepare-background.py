#!/usr/bin/env python3
"""Build the quiet native Terminal wallpaper from the credited coast photo."""

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SIZE = (3840, 2160)
BASE = (16, 15, 24)


def main():
    photo = ImageOps.fit(
        Image.open(ROOT / "assets/coast.jpg").convert("L"),
        SIZE,
        method=Image.Resampling.LANCZOS,
    )
    # A low-contrast photographic silhouette gives the background structure.
    photo = ImageOps.colorize(photo, (10, 10, 17), (28, 24, 40))
    pattern = Image.new("RGB", SIZE, BASE)
    draw = ImageDraw.Draw(pattern)
    step = 22
    for y in range(-step, SIZE[1] + step, step):
        for x in range(-step, SIZE[0] + step, step):
            i, j = x / step, y / step
            u = i + 2.7 * math.sin(j * 0.13) + 1.1 * math.sin((i + j) * 0.075)
            v = j + 1.8 * math.sin(i * 0.1)
            wave = math.sin(u * 0.28) * math.cos(v * 0.31)
            radius = 1.2 + 6.8 * (wave + 1) / 2
            offset = 3.0 * math.sin(j * 0.15)
            color = (30, 25, 46) if wave < 0.6 else (37, 31, 51)
            box = (x + offset - radius, y - radius, x + offset + radius, y + radius)
            draw.rounded_rectangle(box, radius=max(1, radius * 0.3), fill=color)
            if wave > 0.2 and radius > 4:
                inset = radius * 0.42
                draw.rounded_rectangle(
                    (x + offset - inset, y - inset, x + offset + inset, y + inset),
                    radius=1,
                    fill=BASE,
                )

    # Fade the pattern through the center, keeping regular text easy to read.
    mask = Image.new("L", (240, 135))
    pixels = []
    for y in range(mask.height):
        for x in range(mask.width):
            nx, ny = x / (mask.width - 1), y / (mask.height - 1)
            edge = min(1, ((nx - 0.46) * 2) ** 2 + ((ny - 0.48) * 1.8) ** 2)
            pixels.append(round(255 * (0.15 + 0.65 * edge)))
    mask.putdata(pixels)
    mask = mask.resize(SIZE, Image.Resampling.BICUBIC)
    quiet = Image.blend(Image.new("RGB", SIZE, BASE), photo, 0.45)
    combined = Image.composite(Image.blend(quiet, pattern, 0.65), quiet, mask)

    # A fine stationary print grain, deterministic and entirely generated offline.
    grain = Image.new("L", (960, 540))
    grain.putdata(
        [
            ((x * 73 + y * 151 + (x ^ y) * 19) % 5)
            for y in range(540)
            for x in range(960)
        ]
    )
    grain = grain.resize(SIZE, Image.Resampling.NEAREST).convert("RGB")
    combined = ImageChops.add(combined, grain, scale=1, offset=-2)
    destination = ROOT / "terminal/backgrounds/noir-velocity.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    combined.save(destination, optimize=True)
    print(destination)


if __name__ == "__main__":
    main()
