#!/usr/bin/env python3
"""Install the Noir Velocity WezTerm configuration and its animated wallpaper."""

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
FILES = {
    ROOT / "terminal/wezterm.lua": HOME / ".config/wezterm/wezterm.lua",
    ROOT / "assets/wallpaper-source.gif": HOME / ".config/wezterm/noir-velocity.gif",
}


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
        shutil.copy2(source, destination)
    print("Installed. Open WezTerm; the wallpaper animates behind every tab.")


if __name__ == "__main__":
    main()
