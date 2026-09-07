#!/usr/bin/env python3
"""Install the claude-noir launcher, mascot, hook pulse, and tmux options."""

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
FILES = {
    ROOT / "bin/claude-noir": HOME / ".local/bin/claude-noir",
    ROOT / "scripts/claude-dancer.py": HOME / ".local/share/claude-noir/dancer.py",
    ROOT / "scripts/claude-pulse.py": HOME / ".local/share/claude-noir/pulse.py",
    ROOT / "terminal/claude-noir.tmux.conf": HOME
    / ".local/share/claude-noir/tmux.conf",
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
    if not shutil.which("tmux"):
        print("tmux is not installed; claude-noir will fall back to plain claude.")
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
        destination.chmod(0o755 if destination.name == "claude-noir" else 0o644)
    print("Installed. Start Claude Code with: claude-noir")


if __name__ == "__main__":
    main()
