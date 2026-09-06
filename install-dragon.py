#!/usr/bin/env python3
"""Install the staged Dragon Terminal app only after explicit --apply."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
APP = ROOT / "build/Dragon Terminal.app"
DESTINATION = Path.home() / "Applications/Dragon Terminal.app"
LAUNCHER = Path.home() / ".local/bin/dragon-terminal"


def exists(path):
    return path.exists() or path.is_symlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Keep timestamped backups before replacing existing paths")
    args = parser.parse_args()
    required = [APP / "Contents/MacOS/DragonTerminal", APP / "Contents/MacOS/dragon-pty", APP / "Contents/Resources/web/index.html", ROOT / "dragon-terminal"]
    for path in required:
        if not path.is_file():
            parser.error(f"Missing build artifact: {path}")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    for path in (DESTINATION, LAUNCHER):
        if exists(path) and not args.replace:
            parser.error(f"Destination exists; use --replace to preserve a backup: {path}")
    if LAUNCHER.is_dir() and not LAUNCHER.is_symlink():
        parser.error("Launcher destination is a directory")
    print(f"App: {DESTINATION}\nLauncher: {LAUNCHER}")
    if not args.apply:
        print("Preview only. Complete UI QA, then rerun with --apply.")
        return
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    backups = []
    installed = []
    with tempfile.TemporaryDirectory(prefix=".dragon-stage-", dir=DESTINATION.parent) as temporary:
        staged_app = Path(temporary) / "Dragon Terminal.app"
        shutil.copytree(APP, staged_app, symlinks=True)
        descriptor, launcher_name = tempfile.mkstemp(prefix=".dragon-stage-", dir=LAUNCHER.parent)
        os.close(descriptor)
        staged_launcher = Path(launcher_name)
        try:
            shutil.copy2(ROOT / "dragon-terminal", staged_launcher)
            staged_launcher.chmod(0o755)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            for path in (DESTINATION, LAUNCHER):
                if exists(path):
                    if not args.replace:
                        raise RuntimeError(f"Destination appeared during staging: {path}")
                    backup = path.with_name(f"{path.name}.backup-{stamp}")
                    if exists(backup):
                        raise RuntimeError(f"Backup already exists: {backup}")
                    path.rename(backup)
                    backups.append((path, backup))
            staged_app.rename(DESTINATION); installed.append(DESTINATION)
            staged_launcher.rename(LAUNCHER); installed.append(LAUNCHER)
        except BaseException:
            for path in reversed(installed):
                if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
                else: path.unlink()
            for path, backup in reversed(backups): backup.rename(path)
            raise
        finally:
            if exists(staged_launcher): staged_launcher.unlink()
    for path, backup in backups:
        print(f"Backup: {path} -> {backup}")
    print("Installed. No app or terminal session was started.")


if __name__ == "__main__":
    main()
