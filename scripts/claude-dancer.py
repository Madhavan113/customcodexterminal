#!/usr/bin/env python3
"""Claude Code's mascot dancing across a small terminal pane.

Runs in the top pane that `claude-noir` reserves above the real `claude`. The
mascot bounces along the pane, bobs, hops, flips, and leaves a short trail of
lavender dither dots. Everything is deterministic from the clock; the loop
exits cleanly on SIGTERM/SIGINT and follows pane resizes.
"""

import math
import os
import shutil
import signal
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

running = True


def stop(_signum, _frame):
    global running
    running = False


def size():
    try:
        columns, lines = shutil.get_terminal_size(fallback=(80, 7))
    except OSError:
        columns, lines = 80, 7
    return max(columns, 20), max(lines, 3)


def frame(seconds, columns, lines):
    width = len(MASCOT[0])
    height = len(MASCOT)
    left = 14 if columns >= 44 else 0
    span = max(columns - left - width - 1, 1)
    # Ping-pong across the pane, bob vertically, hop every few beats, flip at the turns.
    cycle = (seconds * 6.0) % (2 * span)
    x = left + int(cycle if cycle < span else 2 * span - cycle)
    facing_left = cycle >= span
    beat = seconds * 2.4
    bob = int((math.sin(beat * math.pi) + 1) * 0.5 * max(lines - height - 1, 0))
    hop = int(beat) % 8 == 3 and (beat % 1.0) < 0.5
    y = max(0, min(lines - height, lines - height - bob - (1 if hop else 0)))
    if (beat % 1.0) < 0.15 and not hop:
        rows = SQUASH
    elif int(beat * 2) % 2 == 0:
        rows = STEP_LEFT
    else:
        rows = STEP_RIGHT
    if facing_left:
        rows = ["".join(reversed(row)) for row in rows]

    canvas = [[" "] * columns for _ in range(lines)]
    colors = [[None] * columns for _ in range(lines)]
    # Trail: a few fading dots behind the mascot along its recent path.
    for index, shade in enumerate(LAVENDER):
        back = (index + 1) * 3
        tx = x - back if not facing_left else x + width + back - 1
        ty = min(lines - 1, y + height - 1 - (index % 2))
        if 0 <= tx < columns and 0 <= ty < lines:
            canvas[ty][tx] = TRAIL[(int(seconds * 12) + index) % len(TRAIL)]
            colors[ty][tx] = shade
    for row_index, row in enumerate(rows):
        for column_index, glyph in enumerate(row):
            cx, cy = x + column_index, y + row_index
            if glyph != " " and 0 <= cx < columns and 0 <= cy < lines:
                canvas[cy][cx] = glyph
                colors[cy][cx] = ORANGE if row_index < 2 else ORANGE_DIM
    if left and lines >= 2:
        for column_index, glyph in enumerate(LABEL):
            if glyph != " ":
                canvas[0][2 + column_index] = glyph
                colors[0][2 + column_index] = LAVENDER[0]
        credit = "claude code"
        for column_index, glyph in enumerate(credit):
            canvas[1][2 + column_index] = glyph
            colors[1][2 + column_index] = LAVENDER[2]

    out = ["\x1b[?2026h\x1b[H"]
    for row_index in range(lines):
        current = None
        for column_index in range(columns):
            color = colors[row_index][column_index]
            if color != current:
                out.append("\x1b[39m" if color is None else f"\x1b[38;5;{color}m")
                current = color
            out.append(canvas[row_index][column_index])
        out.append("\x1b[39m")
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
    start = time.monotonic()
    try:
        while running:
            columns, lines = size()
            write(frame(time.monotonic() - start, columns, lines))
            sys.stdout.flush()
            time.sleep(1 / FPS)
    finally:
        write("\x1b[0m\x1b[2J\x1b[H\x1b[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    if os.environ.get("CLAUDE_NOIR_DANCER") == "off":
        sys.exit(0)
    main()
