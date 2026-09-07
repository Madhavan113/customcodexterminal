#!/usr/bin/env python3
"""Convert a photograph or short local video into bundled ASCII source frames."""

import argparse
import io
import json
import math
import re
import struct
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent


def run_ffmpeg(arguments):
    try:
        return subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *arguments],
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
    except FileNotFoundError as error:
        raise RuntimeError("Video conversion requires ffmpeg on PATH.") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.decode("utf-8", errors="replace")) from error


def normalize(frames):
    """Use one contrast curve for the entire clip to avoid exposure flicker."""
    histogram = Counter()
    for frame in frames:
        histogram.update(frame)
    count = sum(histogram.values())
    trim = count * 0.008
    cumulative = 0
    low, high = 0, 255
    for value in range(256):
        cumulative += histogram[value]
        if cumulative > trim:
            low = value
            break
    cumulative = 0
    for value in range(255, -1, -1):
        cumulative += histogram[value]
        if cumulative > trim:
            high = value
            break
    if high <= low:
        return frames
    table = bytes(
        max(0, min(255, round((value - low) * 255 / (high - low))))
        for value in range(256)
    )
    return [frame.translate(table) for frame in frames]


def loop_frames(frames, blend_count):
    """Blend the end into the beginning, then omit the overlapped prefix."""
    blend_count = min(blend_count, len(frames) // 3)
    if blend_count < 1:
        return frames
    result = frames[blend_count:]
    for index in range(blend_count):
        weight = (index + 1) / blend_count
        result[-blend_count + index] = bytes(
            round(old * (1 - weight) + new * weight)
            for old, new in zip(result[-blend_count + index], frames[index])
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local photograph or video")
    parser.add_argument(
        "--name", required=True, help="Study identifier, such as flight or transit"
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-credit", required=True)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=4)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=90)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "assets", help="Output directory"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.name):
        parser.error("Name must be a short lowercase identifier.")
    if not args.input.is_file():
        parser.error("Input must be an existing local file.")
    if not (16 <= args.width <= 256 and 16 <= args.height <= 144):
        parser.error("Use a grid between 16x16 and 256x144.")
    if not (1 <= args.fps <= 24 and 0 < args.duration <= 8 and args.start >= 0):
        parser.error("Video limits: 1–24 FPS, up to 8 seconds, nonnegative start.")

    size = (args.width, args.height)
    frame_size = math.prod(size)
    if args.video:
        filter_graph = (
            f"fps={args.fps},scale={args.width}:{args.height}:force_original_aspect_ratio=increase,"
            f"crop={args.width}:{args.height},format=gray"
        )
        raw = run_ffmpeg(
            [
                "-ss",
                str(args.start),
                "-i",
                str(args.input.resolve()),
                "-t",
                str(args.duration),
                "-an",
                "-vf",
                filter_graph,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            ]
        )
        if not raw or len(raw) % frame_size:
            raise RuntimeError("The video did not produce complete grayscale frames.")
        frames = [
            raw[start : start + frame_size] for start in range(0, len(raw), frame_size)
        ]
        if len(frames) > args.fps * args.duration + 2:
            raise RuntimeError("Unexpectedly large frame sequence.")
        frames = loop_frames(normalize(frames), round(args.fps * 0.5))
        poster_bytes = run_ffmpeg(
            [
                "-ss",
                str(args.start + min(0.5, args.duration / 2)),
                "-i",
                str(args.input.resolve()),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]
        )
        poster = Image.open(io.BytesIO(poster_bytes)).convert("RGB")
        fps = args.fps
    else:
        with Image.open(args.input) as original:
            poster = ImageOps.exif_transpose(original).convert("RGB")
        sampled = ImageOps.fit(poster, size, method=Image.Resampling.LANCZOS).convert(
            "L"
        )
        frames = normalize([sampled.tobytes()])
        fps = 0

    args.output.mkdir(parents=True, exist_ok=True)
    poster_path = args.output / f"{args.name}.jpg"
    poster = ImageOps.fit(poster, (1280, 720), method=Image.Resampling.LANCZOS)
    poster.save(poster_path, quality=88, optimize=True)
    metadata = {
        "title": args.title,
        "width": args.width,
        "height": args.height,
        "fps": fps,
        "frameCount": len(frames),
        "format": "NOIR grayscale frames v1, 16-byte little-endian header",
        "poster": poster_path.name,
        "sourceUrl": args.source_url,
        "sourceCredit": args.source_credit,
    }
    # No image decoder or video runtime is needed in the terminal. The bounded
    # header describes raw grayscale pixels; the Rust renderer chooses glyphs.
    data = struct.pack("<4sHHHHI", b"NOIR", 1, *size, fps, len(frames)) + b"".join(
        frames
    )
    frame_path = args.output / f"{args.name}.nrf"
    temporary = frame_path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(frame_path)
    (args.output / f"{args.name}.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "study": args.name,
                "frames": len(frames),
                "grid": size,
                "fps": fps,
                "output": str(frame_path),
            }
        )
    )


if __name__ == "__main__":
    main()
