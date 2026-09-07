#!/usr/bin/env python3
"""Install the Noir shell prompt and terminal profile with timestamped backups."""

import argparse
import base64
import json
import os
import plistlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHELL_LINE = (
    '[[ -r "$HOME/.config/zsh/noir.zsh" ]] && source "$HOME/.config/zsh/noir.zsh"'
)
OLD_LINE = '[[ -r "$HOME/.config/zsh/overdrive.zsh" ]] && source "$HOME/.config/zsh/overdrive.zsh"'


def background_bookmark(path):
    """Use Foundation's native file bookmark, as Terminal's profile expects."""
    script = """ObjC.import('Foundation');
    function run(argv) {
        var url = $.NSURL.fileURLWithPath(argv[0]);
        var error = Ref();
        var bookmark = url.bookmarkDataWithOptionsIncludingResourceValuesForKeysRelativeToURLError(0, $(), $(), error);
        if (!bookmark) throw new Error('Could not create the background image bookmark');
        // Terminal expects the bookmark wrapped in a keyed archive; raw bookmark bytes make it
        // silently refuse to import the profile.
        var archive = $.NSKeyedArchiver.archivedDataWithRootObject(bookmark);
        return ObjC.unwrap(archive.base64EncodedStringWithOptions(0));
    }"""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return base64.b64decode(result.stdout.strip(), validate=True)


def activate_profile(profile):
    previous_name = datetime.now(UTC).strftime("Noir Velocity backup %Y%m%dT%H%M%S%fZ")
    # Terminal imports an existing name as a second profile. Rename our prior
    # profile first so the newly imported file becomes the selected version.
    preserve = """on run argv
        tell application "Terminal"
            if exists settings set "Noir Velocity" then
                set name of settings set "Noir Velocity" to item 1 of argv
            end if
        end tell
    end run"""
    subprocess.run(["osascript", "-e", preserve, previous_name], check=True, timeout=30)
    subprocess.run(["open", "-g", "-a", "Terminal", str(profile)], check=True)
    script = """on run argv
        tell application "Terminal"
            repeat 20 times
                if exists settings set "Noir Velocity" then exit repeat
                delay 0.1
            end repeat
            set noirSettings to settings set "Noir Velocity"
            set default settings to noirSettings
            set startup settings to noirSettings
            repeat with terminalWindow in windows
                repeat with terminalTab in tabs of terminalWindow
                    set previousName to name of current settings of terminalTab
                    if previousName is "Overdrive Noir" or previousName is "Noir Velocity" or previousName is item 1 of argv then
                        set current settings of terminalTab to noirSettings
                    end if
                end repeat
            end repeat
        end tell
    end run"""
    subprocess.run(["osascript", "-e", script, previous_name], check=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--background",
        choices=("moire", "plain"),
        default="moire",
        help="Use the subtle photographic/pattern wallpaper or a solid color",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Install; default previews the affected paths",
    )
    parser.add_argument(
        "--activate-profile",
        action="store_true",
        help="Import the profile into macOS Terminal and make it the default",
    )
    args = parser.parse_args()
    user_dir = Path.home()
    shell_rc = (
        (Path(os.environ.get("ZDOTDIR", user_dir)) / ".zshrc").expanduser().resolve()
    )
    codex_dir = Path(os.environ.get("CODEX_HOME", user_dir / ".codex")).expanduser()
    profile = user_dir / ".config/terminal/Noir Velocity.terminal"
    wallpaper = user_dir / ".config/terminal/backgrounds/noir-velocity.png"
    content = shell_rc.read_text() if shell_rc.exists() else ""
    lines = content.splitlines()
    if OLD_LINE in lines:
        lines = [SHELL_LINE if line == OLD_LINE else line for line in lines]
    if SHELL_LINE not in lines:
        lines += ["", "# Codex Noir terminal prompt.", SHELL_LINE]
    files = {
        wallpaper: (ROOT / "terminal/backgrounds/noir-velocity.png").read_bytes(),
        user_dir / ".config/zsh/noir.zsh": (ROOT / "terminal/noir.zsh").read_bytes(),
        profile: (ROOT / "terminal/Noir Velocity.terminal").read_bytes(),
        codex_dir / "themes/noir-velocity.tmTheme": (
            ROOT / "terminal/noir-velocity.tmTheme"
        ).read_bytes(),
        shell_rc: ("\n".join(lines) + "\n").encode(),
    }
    for path in files:
        print(path)
    print(f"Terminal background: {args.background}")
    if not args.apply:
        print(
            "Preview only. Add --apply to install; --activate-profile also updates macOS Terminal."
        )
        return
    backup = (
        user_dir
        / ".config/terminal/backups"
        / datetime.now(UTC).strftime("noir-velocity-%Y%m%dT%H%M%S%fZ")
    )
    backup.mkdir(parents=True)
    saved = {}
    for index, destination in enumerate(files):
        if destination.is_symlink() or (
            destination.exists() and not destination.is_file()
        ):
            raise RuntimeError(
                f"Refusing to replace an unexpected destination type: {destination}"
            )
        previous = backup / f"{index}-{destination.name}"
        if destination.exists():
            shutil.copy2(destination, previous)
            saved[str(destination)] = str(previous)
        else:
            saved[str(destination)] = None
    (backup / "files.json").write_text(json.dumps(saved, indent=2) + "\n")
    changed = []
    try:
        for destination, data in files.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination == profile:
                settings = plistlib.loads(data)
                settings.pop("BackgroundImagePath", None)
                settings.pop("BackgroundImageBookmark", None)
                if args.background == "moire":
                    settings["BackgroundImageBookmark"] = background_bookmark(wallpaper)
                data = plistlib.dumps(settings)
            temporary = destination.with_name(destination.name + ".noir-tmp")
            temporary.write_bytes(data)
            temporary.chmod(
                destination.stat().st_mode & 0o777 if destination.exists() else 0o644
            )
            temporary.replace(destination)
            changed.append(destination)
    except BaseException:
        for destination in reversed(changed):
            previous = saved[str(destination)]
            if previous:
                shutil.copy2(previous, destination)
            else:
                destination.unlink()
        raise
    print(f"Installed. Backup: {backup}")
    if args.activate_profile:
        subprocess.run(
            [
                "defaults",
                "export",
                "com.apple.Terminal",
                str(backup / "terminal-before.plist"),
            ],
            check=True,
            capture_output=True,
        )
        activate_profile(profile)
        print("Noir Velocity is the default Terminal profile.")
    print("Open a new shell or source ~/.config/zsh/noir.zsh to load the prompt.")


if __name__ == "__main__":
    main()
