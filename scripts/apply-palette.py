#!/usr/bin/env python3
"""Write terminal/palette.json into every terminal profile this project ships.

Targets: the macOS Terminal profile (NSColor archives via Foundation), the
WezTerm config, the Ghostty config, and Cursor's integrated-terminal colors.
"""

import argparse
import base64
import json
import plistlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PALETTE = ROOT / "terminal/palette.json"
TERMINAL_PROFILE = ROOT / "terminal/Noir Velocity.terminal"
WEZTERM = ROOT / "terminal/wezterm.lua"
GHOSTTY = ROOT / "terminal/ghostty.conf"
CURSOR_SETTINGS = Path.home() / "Library/Application Support/Cursor/User/settings.json"
ANSI_NAMES = ["Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White"]


def rgb(hex_color):
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def nscolor_archives(colors):
    """Archive every color at once so Foundation runs a single time."""
    script = """ObjC.import('AppKit');
    function run(argv) {
        return argv.map(function (hex) {
            var r = parseInt(hex.substr(1, 2), 16) / 255, g = parseInt(hex.substr(3, 2), 16) / 255,
                b = parseInt(hex.substr(5, 2), 16) / 255;
            var color = $.NSColor.colorWithSRGBRedGreenBlueAlpha(r, g, b, 1);
            var data = $.NSKeyedArchiver.archivedDataWithRootObjectRequiringSecureCodingError(color, false, null);
            return ObjC.unwrap(data.base64EncodedStringWithOptions(0));
        }).join('\\n');
    }"""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script, *colors],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [base64.b64decode(line) for line in result.stdout.strip().split("\n")]


def write_terminal_profile(palette):
    keys = {
        "BackgroundColor": palette["background"],
        "TextColor": palette["foreground"],
        "TextBoldColor": palette["bold"],
        "CursorColor": palette["cursor"],
        "SelectionColor": palette["selection"],
    }
    for name, color in zip(ANSI_NAMES, palette["ansi"]):
        keys[f"ANSI{name}Color"] = color
    for name, color in zip(ANSI_NAMES, palette["brights"]):
        keys[f"ANSIBright{name}Color"] = color
    profile = plistlib.load(TERMINAL_PROFILE.open("rb"))
    for key, data in zip(keys, nscolor_archives(list(keys.values()))):
        profile[key] = data
    TERMINAL_PROFILE.write_bytes(plistlib.dumps(profile))


def replace_block(text, start_marker, end_marker, body):
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    return text[:start] + body + text[end:]


def write_wezterm(palette):
    lua = ", ".join(f'"{color}"' for color in palette["ansi"])
    brights = ", ".join(f'"{color}"' for color in palette["brights"])
    body = f"""config.colors = {{
\tforeground = "{palette["foreground"]}",
\tbackground = "{palette["background"]}",
\tcursor_bg = "{palette["cursor"]}",
\tcursor_fg = "{palette["background"]}",
\tcursor_border = "{palette["cursor"]}",
\tselection_bg = "{palette["selection"]}",
\tselection_fg = "{palette["bold"]}",
\tansi = {{ {lua} }},
\tbrights = {{ {brights} }},
}}"""
    text = WEZTERM.read_text()
    WEZTERM.write_text(replace_block(text, "config.colors = {", "\n}", body))
    text = WEZTERM.read_text()
    WEZTERM.write_text(
        re.sub(
            r'source = \{ Color = "#[0-9A-Fa-f]{6}" \}',
            f'source = {{ Color = "{palette["background"]}" }}',
            text,
        )
    )


def write_ghostty(palette):
    lines = [
        f"background = {palette['background']}",
        f"foreground = {palette['foreground']}",
        f"cursor-color = {palette['cursor']}",
        f"cursor-text = {palette['background']}",
        f"selection-background = {palette['selection']}",
        f"selection-foreground = {palette['bold']}",
    ]
    for index, color in enumerate(palette["ansi"] + palette["brights"]):
        lines.append(f"palette = {index}={color}")
    body = "# palette-begin\n" + "\n".join(lines) + "\n# palette-end"
    text = GHOSTTY.read_text()
    GHOSTTY.write_text(replace_block(text, "# palette-begin", "# palette-end", body))


def write_cursor(palette):
    if not CURSOR_SETTINGS.is_file():
        print(f"Cursor settings not found, skipped: {CURSOR_SETTINGS}")
        return
    settings = json.loads(CURSOR_SETTINGS.read_text())
    colors = settings.setdefault("workbench.colorCustomizations", {})
    colors.update(
        {
            "terminal.background": palette["background"],
            "terminal.foreground": palette["foreground"],
            "terminalCursor.foreground": palette["cursor"],
            "terminalCursor.background": palette["background"],
            "terminal.selectionBackground": palette["selection"] + "99",
            "terminal.inactiveSelectionBackground": palette["selection"] + "55",
            "terminal.selectionForeground": palette["bold"],
        }
    )
    for name, color in zip(ANSI_NAMES, palette["ansi"]):
        colors[f"terminal.ansi{name}"] = color
    for name, color in zip(ANSI_NAMES, palette["brights"]):
        colors[f"terminal.ansiBright{name}"] = color
    CURSOR_SETTINGS.write_text(
        json.dumps(settings, indent=4, ensure_ascii=False) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--palette", type=Path, default=PALETTE)
    parser.add_argument(
        "--skip-cursor", action="store_true", help="Leave Cursor's settings.json alone"
    )
    args = parser.parse_args()
    palette = json.loads(args.palette.read_text())
    write_terminal_profile(palette)
    write_wezterm(palette)
    write_ghostty(palette)
    if not args.skip_cursor:
        write_cursor(palette)
    print(f"Applied {palette['name']} to Terminal, WezTerm, Ghostty, and Cursor.")


if __name__ == "__main__":
    main()
