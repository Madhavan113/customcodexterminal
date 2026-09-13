#!/usr/bin/env python3
"""Install the Noir Velocity WezTerm configuration, its animated wallpaper, and the still frame."""

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
FILES = {
    ROOT / "terminal/wezterm.lua": HOME / ".config/wezterm/wezterm.lua",
    ROOT / "assets/wallpaper-source.gif": HOME / ".config/wezterm/noir-velocity.gif",
    ROOT / "terminal/backgrounds/noir-velocity.png": HOME
    / ".config/wezterm/noir-velocity.png",
}
GIF_TRAILER = 0x3B
GIF_EXTENSION = 0x21
GIF_GRAPHIC_CONTROL = 0xF9
GIF_IMAGE = 0x2C


def gif_with_frame_delay(data, delay_ms):
    """The same GIF with every untimed frame (delay 0) given `delay_ms`; other bytes untouched.

    WezTerm plays frames without timing as fast as its frame cap, repainting the whole window
    each time, so a paced copy costs a fraction of the CPU. Frames that carry their own timing
    are left alone. Only the two delay bytes of each Graphic Control Extension change.
    """
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF file")
    delay = max(1, round(delay_ms / 10))  # GIF delays count hundredths of a second.
    out = bytearray(data)
    position = 13
    if data[10] & 0x80:  # global color table: 3 bytes per entry, 2^(n+1) entries
        position += 3 * (2 << (data[10] & 0x07))

    def skip_sub_blocks(position):
        while True:
            size = data[position]
            position += 1
            if size == 0:
                return position
            position += size

    while position < len(data):
        block = data[position]
        if block == GIF_TRAILER:
            break
        if block == GIF_EXTENSION:
            untimed = data[position + 4] == 0 and data[position + 5] == 0
            if (
                data[position + 1] == GIF_GRAPHIC_CONTROL
                and data[position + 2] == 4
                and untimed
            ):
                out[position + 4] = delay & 0xFF
                out[position + 5] = delay >> 8
            position = skip_sub_blocks(position + 2)
        elif block == GIF_IMAGE:
            local_flags = data[position + 9]
            position += 10
            if local_flags & 0x80:
                position += 3 * (2 << (local_flags & 0x07))
            position = skip_sub_blocks(position + 1)  # after the LZW minimum code size
        else:
            raise ValueError(f"unexpected GIF block 0x{block:02x} at byte {position}")
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Install; default previews the paths"
    )
    parser.add_argument(
        "--wallpaper",
        type=Path,
        default=ROOT / "assets/wallpaper-source.gif",
        help="Animated GIF, WebP, or PNG to play behind the terminal",
    )
    parser.add_argument(
        "--wallpaper-fps",
        type=int,
        default=10,
        help="Pace for GIF frames that carry no timing (default 10; 0 keeps them untimed)",
    )
    args = parser.parse_args()
    files = dict(FILES)
    files[args.wallpaper] = files.pop(ROOT / "assets/wallpaper-source.gif")
    for source, destination in files.items():
        if not source.is_file():
            raise RuntimeError(f"Missing source file: {source}")
        print(f"{source} -> {destination}")
    if not Path("/Applications/WezTerm.app").exists() and not shutil.which("wezterm"):
        print("WezTerm is not installed: brew install --cask wezterm")
    if not args.apply:
        print("Preview only. Re-run with --apply to install.")
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for source, destination in files.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.copy2(
                destination, destination.with_name(f"{destination.name}.backup-{stamp}")
            )
        if (
            source == args.wallpaper
            and source.suffix.lower() == ".gif"
            and args.wallpaper_fps > 0
        ):
            destination.write_bytes(
                gif_with_frame_delay(source.read_bytes(), 1000 // args.wallpaper_fps)
            )
            print(f"Untimed GIF frames paced at {args.wallpaper_fps} per second.")
        else:
            shutil.copy2(source, destination)
    print("Installed. Open WezTerm; the wallpaper animates behind every tab.")


if __name__ == "__main__":
    main()
