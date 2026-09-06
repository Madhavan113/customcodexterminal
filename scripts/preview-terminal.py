"""Capture the real TUI against a local, inert streaming fixture."""

import argparse
import codecs
import fcntl
import http.server
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import termios
import threading
import time

import pyte
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output/terminal-preview"
PALETTE_CONFIG = json.loads((ROOT / "terminal/palette.json").read_text())
COLUMNS, ROWS = 112, 30
BG, FG = PALETTE_CONFIG["background"], PALETTE_CONFIG["foreground"]
PALETTE = {key: value for key, value in PALETTE_CONFIG.items()}
PALETTE["brown"] = PALETTE["yellow"]
PALETTE["brightbrown"] = PALETTE["brightyellow"]
BRAILLE_MASKS = {}


class Fixture(http.server.BaseHTTPRequestHandler):
    started = threading.Event()

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.started.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        event = {
            "type": "response.created",
            "response": {
                "id": "resp_noir_preview",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6-astra",
                "status": "in_progress",
                "output": [],
            },
        }
        try:
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(20)
        except (BrokenPipeError, ConnectionResetError):
            pass


def color(value, fallback):
    if value == "default":
        return "#" + fallback
    return "#" + PALETTE.get(value, value)


def render(screen, label):
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 17)
    dots = ImageFont.truetype("/System/Library/Fonts/Apple Symbols.ttf", 20)
    cell_w, cell_h, padding = 11, 24, 22
    image = Image.new(
        "RGB", (COLUMNS * cell_w + 2 * padding, ROWS * cell_h + 62), "#" + BG
    )
    wallpaper = ROOT / "terminal/backgrounds/noir-velocity.png"
    if wallpaper.is_file():
        with Image.open(wallpaper) as backdrop:
            image.paste(
                ImageOps.fit(backdrop, (COLUMNS * cell_w, ROWS * cell_h)),
                (padding, 42),
            )
    draw = ImageDraw.Draw(image)
    draw.text((padding, 12), label, font=font, fill="#" + PALETTE_CONFIG["accent"])
    for y in range(ROWS):
        for x in range(COLUMNS):
            cell = screen.buffer[y][x]
            fg, bg = color(cell.fg, FG), color(cell.bg, BG)
            if cell.reverse:
                fg, bg = bg, fg
            left, top = padding + x * cell_w, 42 + y * cell_h
            if cell.bg != "default" or cell.reverse:
                draw.rectangle(
                    (left, top, left + cell_w - 1, top + cell_h - 1), fill=bg
                )
            if cell.data.strip():
                if any(0x2800 <= ord(c) <= 0x28FF for c in cell.data):
                    if cell.data not in BRAILLE_MASKS:
                        # Terminal fits fallback fonts to its fixed cell width.
                        # Apple Symbols has a wider natural advance than Menlo.
                        mask = Image.new("L", (14, cell_h))
                        ImageDraw.Draw(mask).text(
                            (0, 1), cell.data, font=dots, fill=255
                        )
                        BRAILLE_MASKS[cell.data] = mask.resize(
                            (cell_w, cell_h), Image.Resampling.LANCZOS
                        )
                    draw.bitmap((left, top), BRAILLE_MASKS[cell.data], fill=fg)
                else:
                    draw.text((left, top), cell.data, font=font, fill=fg)
    return image


def capture(
    binary, mode, port, scene="coast", animations=True, ansi256=False, style="halftone"
):
    variant = (
        f"-{style}" + ("-still" if not animations else "") + ("-256" if ansi256 else "")
    )
    preview_dir = OUTPUT / f"{scene}-{mode}-{COLUMNS}x{ROWS}{variant}"
    expect_scene = animations and scene != "off" and COLUMNS >= 44
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / "themes").mkdir(exist_ok=True)
    shutil.copy2(
        ROOT / "terminal/noir-velocity.tmTheme",
        preview_dir / "themes/noir-velocity.tmTheme",
    )
    config = f'''model = "gpt-6-astra"
model_reasoning_effort = "{mode}"
model_provider = "preview"
check_for_update_on_startup = false
[model_providers.preview]
name = "Local inert preview"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = false
[tui]
animations = {str(animations).lower()}
show_tooltips = false
theme = "noir-velocity"
status_line_use_colors = true
status_line = ["model-with-reasoning", "fast-mode", "context-remaining"]
[projects.{json.dumps(str(preview_dir))}]
trust_level = "trusted"
'''
    (preview_dir / "config.toml").write_text(config)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    env = os.environ.copy()
    env.update(
        CODEX_HOME=str(preview_dir),
        TERM="xterm-256color",
        COLORTERM="truecolor",
        CODEX_NOIR_DRAGON="1",
        CODEX_NOIR_SCENE=scene,
        CODEX_NOIR_STYLE=style,
    )
    env.pop("NO_COLOR", None)
    env["FORCE_COLOR"] = "2" if ansi256 else "3"
    if ansi256:
        env.pop("COLORTERM", None)
        env["TERM_PROGRAM"] = "Apple_Terminal"
    proc = subprocess.Popen(
        [str(binary), "--no-alt-screen"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=preview_dir,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    screen, decoder = (
        pyte.Screen(COLUMNS, ROWS),
        codecs.getincrementaldecoder("utf-8")("replace"),
    )
    stream = pyte.Stream(screen)
    raw, frames, rail_observations, scene_observations = bytearray(), [], [], []
    draft_below_scene = []
    draft = "My draft stays readable"

    def rail_state():
        for y, row in enumerate(screen.display):
            if row.count("─") > 20 and any(
                label in row for label in (" WORKING ", " MAX ", " ULTRA ")
            ):
                return {
                    "row": y,
                    "text": row,
                    "colors": [screen.buffer[y][x].fg for x in range(COLUMNS)],
                }
        return None

    def scene_state():
        marker = " ".join(scene.upper())
        height = 10 if COLUMNS >= 112 else 8 if COLUMNS >= 80 else 6
        for y, row in enumerate(screen.display):
            if marker in row and y + height <= ROWS:
                return {
                    "top": y,
                    "bottom": y + height - 1,
                    "text": screen.display[y : y + height],
                    "colors": [
                        [screen.buffer[line][x].fg for x in range(COLUMNS)]
                        for line in range(y, y + height)
                    ],
                }
        return None

    def pump(seconds, sample=False):
        end, next_sample = time.monotonic() + seconds, time.monotonic()
        while time.monotonic() < end and proc.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.04)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                raw.extend(data)
                if b"\x1b]11;?" in data:
                    components = "/".join(
                        BG[index : index + 2] * 2 for index in (0, 2, 4)
                    )
                    os.write(master, f"\x1b]11;rgb:{components}\x1b\\".encode())
                if b"\x1b]10;?" in data:
                    components = "/".join(
                        FG[index : index + 2] * 2 for index in (0, 2, 4)
                    )
                    os.write(master, f"\x1b]10;rgb:{components}\x1b\\".encode())
                if b"\x1b[6n" in data:
                    os.write(master, b"\x1b[1;1R")
                stream.feed(decoder.decode(data))
            complete_frame = raw.rfind(b"\x1b[?2026h") <= raw.rfind(b"\x1b[?2026l")
            if sample and complete_frame and time.monotonic() >= next_sample:
                frames.append(
                    render(
                        screen,
                        f"CODEX NOIR  /  {scene.upper()}  /  actual CLI, local fixture",
                    )
                )
                rail_observations.append(rail_state())
                picture = scene_state()
                scene_observations.append(picture)
                draft_row = next(
                    (y for y, row in enumerate(screen.display) if draft in row), None
                )
                draft_below_scene.append(
                    draft_row is not None
                    and (
                        not expect_scene
                        or (picture is not None and picture["bottom"] < draft_row)
                    )
                )
                next_sample = time.monotonic() + 0.10

    try:
        Fixture.started.clear()
        pump(4)
        os.write(master, b"\x1b[200~Preview the working animation\x1b[201~")
        pump(1.2)
        os.write(master, b"\r")
        pump(3)
        if not Fixture.started.is_set():
            os.write(master, b"\r")
            pump(2)
        assert Fixture.started.is_set(), "Local UI preview prompt was not submitted"
        os.write(master, draft.encode())
        pump(0.4)
        pump(3, sample=True)
        (preview_dir / "working-screen.txt").write_text("\n".join(screen.display))
        if frames:
            frames[len(frames) // 2].save(preview_dir / "working.png")
            frames[0].save(
                preview_dir / "working.gif",
                save_all=True,
                append_images=frames[1:],
                duration=100,
                loop=0,
            )
        os.write(master, b"\x1b")
        pump(1)
        idle_scene = scene_state()
        pump(0.4)
        render(screen, f"CODEX NOIR  /  {scene.upper()}  /  idle").save(
            preview_dir / "idle.png"
        )
        (preview_dir / "idle-screen.txt").write_text("\n".join(screen.display))
        report = {
            "mode": mode,
            "scene": scene,
            "style": style,
            "animations": animations,
            "ansi256": ansi256,
            "scene_expected": expect_scene,
            "columns": COLUMNS,
            "rows": ROWS,
            "frames": len(frames),
            "rail_frames": sum(state is not None for state in rail_observations),
            "distinct_rail_frames": len(
                {json.dumps(state) for state in rail_observations if state}
            ),
            "rail_stops_when_idle": rail_state() is None,
            "scene_frames": sum(state is not None for state in scene_observations),
            "distinct_scene_frames": len(
                {json.dumps(state["text"]) for state in scene_observations if state}
            ),
            "scene_stays_above_draft": all(draft_below_scene),
            "scene_quiet_when_idle": idle_scene == scene_state()
            and (idle_scene is not None) == expect_scene,
            "draft_preserved": draft in "\n".join(screen.display),
        }
        (preview_dir / "verification.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        os.write(master, b"\x03\x03")
        pump(2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        os.close(master)
        (preview_dir / "capture.ansi").write_bytes(raw)
    print(
        json.dumps(report | {"preview": str(preview_dir / "working.gif")}), flush=True
    )
    assert report["frames"] > 5, report
    if animations:
        assert report["rail_frames"] == report["frames"], report
        assert report["distinct_rail_frames"] > 2, report
    else:
        assert report["rail_frames"] == 0, report
    assert report["rail_stops_when_idle"] and report["draft_preserved"], report
    if expect_scene:
        assert report["scene_frames"] == report["frames"], report
        assert report["distinct_scene_frames"] > 2, report
    else:
        assert report["scene_frames"] == 0, report
    assert report["scene_stays_above_draft"] and report["scene_quiet_when_idle"], report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--modes", nargs="+", default=["high"])
    parser.add_argument("--scenes", nargs="+", default=["coast", "moire"])
    parser.add_argument("--style", choices=("halftone", "ascii"), default="halftone")
    parser.add_argument("--columns", type=int, default=112)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--no-motion", action="store_true")
    parser.add_argument(
        "--ansi256",
        action="store_true",
        help="Exercise macOS Terminal color capabilities",
    )
    args = parser.parse_args()
    COLUMNS, ROWS = args.columns, args.rows
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for mode in args.modes:
            for scene in args.scenes:
                capture(
                    args.binary.resolve(),
                    mode,
                    server.server_port,
                    scene,
                    not args.no_motion,
                    args.ansi256,
                    args.style,
                )
    finally:
        server.shutdown()
