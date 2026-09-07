#!/usr/bin/env python3
"""Claude Code's mascot dancing across a small terminal pane, with a shiba and a rail.

Runs in the top pane that `claude-noir` reserves above the real `claude`. The
mascot bounces along the pane, bobs, hops, flips, and leaves a short trail of
lavender dither dots. A shiba runs beside it, bounding a fraction of a beat
behind and jumping when it does. The bottom row is a rail naming what Claude
is doing and at which effort, read from the state file that `claude-pulse.py`
maintains from Claude Code's hooks; its colors follow the effort. Everything
is deterministic from the clock and that state; the loop exits cleanly on
SIGTERM/SIGINT and follows pane resizes.
"""

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time

FPS = 12
# Claude Code's mascot as three text rows; body cells are painted orange.
MASCOT = [
    " ▐▛███▜▌ ",
    "▝▜█████▛▘",
    "  ▘▘ ▝▝  ",
]
# Squashed and stretched variants keep the bounce lively.
SQUASH = [
    "         ",
    "▐▛█████▜▌",
    "▝▘▘▘ ▝▝▝▘",
]
STEP_LEFT = [
    " ▐▛███▜▌ ",
    "▝▜█████▛▘",
    " ▘▘  ▝▝  ",
]
STEP_RIGHT = [
    " ▐▛███▜▌ ",
    "▝▜█████▛▘",
    "  ▘▘  ▝▝ ",
]
ORANGE = 209
ORANGE_DIM = 173
LAVENDER = [183, 147, 104, 61]
LABEL = "C L A U D E"
TRAIL = "⠁⠂⠄⠈⠐⠠"

# The shiba, facing right: tail curled over the back, two pointed ears, an eye
# as a dark notch in the head, a cream muzzle ending in a black nose, cream legs.
SHIBA_TROT = [
    "▗▖  ▟▙▟▙  ",
    "▝██████▟▀▘",
    " ▘▘  ▝▝   ",
]
SHIBA_STRIDE = [
    "▗▘  ▟▙▟▙  ",
    "▝██████▟▀▘",
    "▘▘    ▝▝  ",
]
SHIBA_LEAP = [
    "▐▌  ▟▙▟▙  ",
    "▝██████▟▀▘",
    " ▝▘   ▝▘  ",
]
SHIBA_LAND = [
    "          ",
    "▗███████▀▘",
    "▘▘▘   ▝▝▝ ",
]
SHIBA_GAP = 2
SHIBA_LAG = 0.3
RED_COAT = 215
CREAM = 230
CREAM_DIM = 223
NOSE = 240
# Quadrant glyphs swapped left for right, so an asymmetric sprite can face left.
MIRROR = str.maketrans("▘▝▖▗▌▐▛▜▙▟▞▚", "▝▘▗▖▐▌▜▛▟▙▚▞")

# The rail: a line under the scene, colored by effort, with a centered label.
RAIL = "─"
# Color ramps from dim to bright; the rail's glow picks along them.
RAMPS = {
    "cruise": [60, 61, 67, 74, 80, 87, 123, 159],
    "overdrive": [89, 125, 161, 197, 204, 208, 214, 220],
    "ultra": [54, 55, 56, 93, 99, 135, 141, 177],
    "slate": [59, 60, 61, 62, 103, 104, 110, 111],
    "waiting": [94, 130, 136, 172, 178, 214, 220, 221],
}
RAINBOW = [
    196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 48, 50,
    51, 45, 39, 33, 27, 57, 93, 129, 165, 201, 199, 197,
]  # fmt: skip
EFFORT_DRIVES = {
    "low": "slate",
    "medium": "slate",
    "high": "cruise",
    "xhigh": "overdrive",
    "max": "ultra",
}
DIVIDER = 60

running = True


def stop(_signum, _frame):
    global running
    running = False


def size():
    try:
        columns, lines = shutil.get_terminal_size(fallback=(80, 8))
    except OSError:
        columns, lines = 80, 8
    return max(columns, 20), max(lines, 3)


def mirror(rows):
    return [row.translate(MIRROR)[::-1] for row in rows]


def shiba_color(row_index, column_index, width):
    if row_index == 2:
        return CREAM_DIM
    if row_index == 1 and column_index == width - 1:
        return NOSE
    if row_index == 1 and column_index == width - 2:
        return CREAM
    return RED_COAT


def shiba_pose(beat, hop):
    lift = math.sin(beat * math.pi)
    if hop or lift > 0.6:
        return SHIBA_LEAP
    if lift < -0.9:
        return SHIBA_LAND
    return SHIBA_TROT if int(beat * 3) % 2 == 0 else SHIBA_STRIDE


def paint(canvas, colors, rows, x, y, color_at):
    lines = len(canvas)
    columns = len(canvas[0]) if canvas else 0
    for row_index, row in enumerate(rows):
        for column_index, glyph in enumerate(row):
            cx, cy = x + column_index, y + row_index
            if glyph != " " and 0 <= cx < columns and 0 <= cy < lines:
                canvas[cy][cx] = glyph
                colors[cy][cx] = color_at(row_index, column_index)


class State:
    """The rail's view of Claude's activity, re-read only when the file changes."""

    def __init__(self, path):
        self.path = path
        self.stamp = None
        self.data = None

    def refresh(self):
        if not self.path:
            return None
        try:
            info = os.stat(self.path)
        except OSError:
            self.stamp = None
            self.data = None
            return None
        stamp = (info.st_mtime_ns, info.st_size)
        if stamp != self.stamp:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                return self.data
            self.stamp = stamp
            self.data = data if isinstance(data, dict) else None
        return self.data


def rail_drive(state):
    """The color ramp key and effort label for a state; (None, None) without one."""
    if state is None:
        return None, None
    if state.get("ultracode"):
        return "rainbow", "ULTRACODE"
    if state.get("ultrathink"):
        return "ultra", "ULTRATHINK"
    effort = state.get("effort")
    if effort in EFFORT_DRIVES:
        return EFFORT_DRIVES[effort], effort.upper()
    return "cruise", None


def tool_label(tool):
    name = (tool or "").rsplit("__", 1)[-1].strip().upper()
    if len(name) > 14:
        name = name[:13] + "…"
    return name or "WORKING"


def rail_label(state, effort_label):
    parts = ["CLAUDE"]
    phase = state.get("phase") if state else "idle"
    if phase == "thinking":
        parts.append("THINKING")
    elif phase == "tool":
        parts.append(tool_label(state.get("tool")))
    elif phase == "waiting":
        parts.append("WAITING FOR YOU")
    if effort_label:
        parts.append(effort_label)
    return f" {' · '.join(parts)} "


def rail_color(drive, phase, position, seconds):
    """Palette index for one rail cell: a beam, pulse, or wave while Claude is busy."""
    if drive is None:
        return LAVENDER[3]
    if drive == "rainbow":
        if phase == "idle":
            return RAINBOW[int(position * (len(RAINBOW) - 1))]
        return RAINBOW[int((position - seconds / 3.0) * len(RAINBOW)) % len(RAINBOW)]
    if phase == "idle":
        return RAMPS[drive][1]
    if phase == "waiting":
        ramp = RAMPS["waiting"]
        strength = 0.3 + 0.7 * (0.5 + 0.5 * math.cos(math.tau * seconds / 1.2))
    elif drive == "overdrive":
        ramp = RAMPS[drive]
        pulse = math.cos(math.tau * (seconds / 2.8 - position * 0.22))
        strength = 0.28 + 0.72 * (0.5 + 0.5 * pulse)
    elif drive == "ultra":
        ramp = RAMPS[drive]
        wave = math.sin(math.tau * (position * 1.3 - seconds / 4.8))
        glow = math.cos(math.tau * (position * 0.7 + seconds / 3.2))
        strength = 0.4 + 0.35 * (0.5 + 0.5 * glow) + 0.25 * (0.5 + 0.5 * wave)
    else:
        ramp = RAMPS[drive]
        center = (seconds / 2.4) % 1.0
        distance = abs(position - center)
        strength = 0.2 + 0.8 * math.exp(-70.0 * distance * distance)
    return ramp[min(len(ramp) - 1, int(strength * len(ramp)))]


def label_color(drive, phase):
    if drive is None:
        return LAVENDER[1]
    if phase == "waiting":
        return RAMPS["waiting"][-2]
    if drive == "rainbow":
        return 231 if phase != "idle" else 252
    return RAMPS[drive][-2] if phase != "idle" else RAMPS[drive][3]


def divider_color(state):
    """The tmux divider under the rail follows the effort so the bar reaches Claude's pane."""
    drive, _effort_label = rail_drive(state)
    phase = state.get("phase", "idle") if state else "idle"
    if drive is None:
        return DIVIDER
    if phase == "waiting":
        return RAMPS["waiting"][4]
    if drive == "rainbow":
        return 213 if phase != "idle" else 97
    return RAMPS[drive][4] if phase != "idle" else RAMPS[drive][1]


def tint_divider(color):
    if not os.environ.get("TMUX") or not shutil.which("tmux"):
        return
    for option in ("pane-border-style", "pane-active-border-style"):
        try:
            subprocess.run(
                ["tmux", "set-option", "-w", option, f"fg=colour{color}"],
                check=False,
                timeout=1,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def paint_rail(canvas, colors, bold, row, state, seconds):
    columns = len(canvas[row])
    drive, effort_label = rail_drive(state)
    phase = state.get("phase", "idle") if state else "idle"
    label = rail_label(state, effort_label)
    start = (columns - len(label)) // 2 if columns >= len(label) + 4 else None
    for column in range(columns):
        index = column - start if start is not None else -1
        if 0 <= index < len(label):
            canvas[row][column] = label[index]
            colors[row][column] = label_color(drive, phase)
            bold[row][column] = True
        else:
            position = column / max(columns - 1, 1)
            canvas[row][column] = RAIL
            colors[row][column] = rail_color(drive, phase, position, seconds)


def frame(seconds, columns, lines, state=None):
    width = len(MASCOT[0])
    height = len(MASCOT)
    shiba_width = len(SHIBA_TROT[0])
    # The rail takes the bottom row whenever the runners still fit above it.
    rail_row = lines - 1 if lines > height else None
    scene = lines - 1 if rail_row is not None else lines
    left = 14 if columns >= 44 else 0
    # The shiba runs on the mascot's right when the pane leaves both room to travel.
    with_shiba = columns - left - width - SHIBA_GAP - shiba_width - 1 >= 8
    group = width + (SHIBA_GAP + shiba_width if with_shiba else 0)
    span = max(columns - left - group - 1, 1)
    # Ping-pong across the pane, bob vertically, hop every few beats, flip at the turns.
    cycle = (seconds * 6.0) % (2 * span)
    x = left + int(cycle if cycle < span else 2 * span - cycle)
    facing_left = cycle >= span
    beat = seconds * 2.4
    amplitude = max(scene - height - 1, 0)
    bob = int((math.sin(beat * math.pi) + 1) * 0.5 * amplitude)
    hop = int(beat) % 8 == 3 and (beat % 1.0) < 0.5
    y = max(0, min(scene - height, scene - height - bob - (1 if hop else 0)))
    if (beat % 1.0) < 0.15 and not hop:
        rows = SQUASH
    elif int(beat * 2) % 2 == 0:
        rows = STEP_LEFT
    else:
        rows = STEP_RIGHT
    if facing_left:
        rows = ["".join(reversed(row)) for row in rows]

    # The shiba bounds a fraction of a beat behind and jumps with the mascot, then once more.
    shiba_x = x + width + SHIBA_GAP
    shiba_beat = beat - SHIBA_LAG
    shiba_bob = int((math.sin(shiba_beat * math.pi) + 1) * 0.5 * amplitude)
    shiba_hop = int(shiba_beat) % 8 in (3, 4) and (shiba_beat % 1.0) < 0.5
    shiba_y = max(
        0, min(scene - height, scene - height - shiba_bob - (1 if shiba_hop else 0))
    )
    shiba_rows = shiba_pose(shiba_beat, shiba_hop)
    if facing_left:
        shiba_rows = mirror(shiba_rows)

    def shiba_paint(row_index, column_index):
        if facing_left:
            column_index = shiba_width - 1 - column_index
        return shiba_color(row_index, column_index, shiba_width)

    canvas = [[" "] * columns for _ in range(lines)]
    colors = [[None] * columns for _ in range(lines)]
    bold = [[False] * columns for _ in range(lines)]
    # Trail: a few fading dots behind whichever runner is at the rear.
    group_right = shiba_x + shiba_width if with_shiba else x + width
    rear_y = shiba_y if with_shiba and facing_left else y
    for index, shade in enumerate(LAVENDER):
        back = (index + 1) * 3
        tx = x - back if not facing_left else group_right + back - 1
        ty = min(scene - 1, rear_y + height - 1 - (index % 2))
        if 0 <= tx < columns and 0 <= ty < scene:
            canvas[ty][tx] = TRAIL[(int(seconds * 12) + index) % len(TRAIL)]
            colors[ty][tx] = shade
    # Painting through a slice of the rows keeps the runners above the rail.
    if with_shiba:
        paint(canvas[:scene], colors, shiba_rows, shiba_x, shiba_y, shiba_paint)
    paint(
        canvas[:scene],
        colors,
        rows,
        x,
        y,
        lambda row_index, _column_index: ORANGE if row_index < 2 else ORANGE_DIM,
    )
    if left and scene >= 2:
        for column_index, glyph in enumerate(LABEL):
            if glyph != " ":
                canvas[0][2 + column_index] = glyph
                colors[0][2 + column_index] = LAVENDER[0]
        credit = "claude code"
        for column_index, glyph in enumerate(credit):
            canvas[1][2 + column_index] = glyph
            colors[1][2 + column_index] = LAVENDER[2]
    if rail_row is not None:
        paint_rail(canvas, colors, bold, rail_row, state, seconds)

    out = ["\x1b[?2026h\x1b[H"]
    for row_index in range(lines):
        current = (None, False)
        for column_index in range(columns):
            style = (colors[row_index][column_index], bold[row_index][column_index])
            if style != current:
                color, heavy = style
                foreground = "39" if color is None else f"38;5;{color}"
                out.append(f"\x1b[{1 if heavy else 22};{foreground}m")
                current = style
            out.append(canvas[row_index][column_index])
        out.append("\x1b[0m")
        if row_index < lines - 1:
            out.append("\r\n")
    out.append("\x1b[?2026l")
    return "".join(out)


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)
    write = sys.stdout.write
    write("\x1b[?25l\x1b[2J")
    sys.stdout.flush()
    state = State(os.environ.get("CLAUDE_NOIR_STATE"))
    tint = None
    start = time.monotonic()
    try:
        while running:
            columns, lines = size()
            current = state.refresh()
            if state.path:
                color = divider_color(current)
                if color != tint:
                    tint_divider(color)
                    tint = color
            write(frame(time.monotonic() - start, columns, lines, current))
            sys.stdout.flush()
            time.sleep(1 / FPS)
    finally:
        write("\x1b[0m\x1b[2J\x1b[H\x1b[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    if os.environ.get("CLAUDE_NOIR_DANCER") == "off":
        sys.exit(0)
    main()
