#!/usr/bin/env python3
"""Install the Noir Velocity Ghostty configuration and its still wallpaper."""

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
FILES = {
    ROOT / "terminal/ghostty.conf": HOME / ".config/ghostty/config",
    ROOT / "terminal/backgrounds/noir-velocity.png": HOME
    / ".config/ghostty/noir-velocity.png",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Install; default previews the paths"
    )
    args = parser.parse_args()
    for source, destination in FILES.items():
        if not source.is_file():
            raise RuntimeError(f"Missing source file: {source}")
        print(f"{source.relative_to(ROOT)} -> {destination}")
    if not Path("/Applications/Ghostty.app").exists():
        print("Ghostty is not installed: brew install --cask ghostty")
    if not args.apply:
        print("Preview only. Re-run with --apply to install.")
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for source, destination in FILES.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.copy2(
                destination, destination.with_name(f"{destination.name}.backup-{stamp}")
            )
        shutil.copy2(source, destination)
    print("Installed. Open Ghostty (or reload its config with Cmd+Shift+,).")


if __name__ == "__main__":
    main()
