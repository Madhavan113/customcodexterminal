"""Measure the real Codex TUI against a local fixture, including keyboard echo."""

import argparse
import codecs
import contextlib
import fcntl
import http.server
import json
import os
import pty
import resource
import select
import statistics
import struct
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path

import pyte

ROOT = Path(__file__).resolve().parent.parent
FRAME_START = b"\x1b[?2026h"
FRAME_END = b"\x1b[?2026l"


class LocalModel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_event(self, event):
        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.started.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        event = {
            "type": "response.created",
            "response": {
                "id": "resp_noir_benchmark",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6-astra",
                "status": "in_progress",
                "output": [],
            },
        }
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.send_event(event)
            if not self.server.streaming:
                self.server.release.wait(40)
                return
            self.send_event(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "msg_noir_benchmark",
                        "type": "message",
                        "role": "assistant",
                        "status": "in_progress",
                        "content": [],
                    },
                }
            )
            self.send_event(
                {
                    "type": "response.content_part.added",
                    "item_id": "msg_noir_benchmark",
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }
            )
            prefix = "".join(
                f"Fixture row {index}: " + "bounded streaming output " * 4 + "\n"
                for index in range(500)
            )
            chunks = []
            for index in range(1000):
                delta = prefix if index == 0 else f"Streaming fixture update {index}.\n"
                chunks.append(delta)
                self.send_event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_noir_benchmark",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": delta,
                    }
                )
                if self.server.release.wait(0.04):
                    break
            chunks.append("Noir benchmark complete.\n")
            self.send_event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_noir_benchmark",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": chunks[-1],
                }
            )
            item = {
                "id": "msg_noir_benchmark",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "".join(chunks),
                        "annotations": [],
                    }
                ],
            }
            self.send_event(
                {"type": "response.output_item.done", "output_index": 0, "item": item}
            )
            self.send_event(
                {
                    "type": "response.completed",
                    "response": {
                        **event["response"],
                        "status": "completed",
                        "output": [item],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 16000,
                            "total_tokens": 16010,
                        },
                    },
                }
            )


def run_case(binary, name, output, columns, rows, ansi256, streaming, effort, light):
    scene, drive, animations = {
        "cruise": ("dither", "cruise", True),
        "overdrive": ("dither", "overdrive", True),
        "warp": ("dither", "warp", True),
        "scene-off": ("off", "cruise", True),
        "motion-off": ("dither", "cruise", False),
    }[name]
    case = output / name
    case.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="noir-tui-bench-") as temporary:
        root = Path(temporary)
        project, home = root / "project", root / "codex"
        project.mkdir()
        home.mkdir()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
        server.started, server.release = threading.Event(), threading.Event()
        server.streaming = streaming
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (home / "config.toml").write_text(
            f'model = "gpt-6-astra"\nmodel_reasoning_effort = "{effort}"\n'
            'model_provider = "local_benchmark"\ncheck_for_update_on_startup = false\n'
            '[model_providers.local_benchmark]\nname = "Local fixture"\n'
            f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
            'wire_api = "responses"\nrequires_openai_auth = false\n'
            f'[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
            f"[tui]\nanimations = {str(animations).lower()}\nshow_tooltips = false\n"
        )
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        env = os.environ.copy()
        env.update(
            CODEX_HOME=str(home),
            TERM="xterm-256color",
            COLORTERM="truecolor",
            FORCE_COLOR="2" if ansi256 else "3",
            CODEX_NOIR_DRAGON="1",
            CODEX_NOIR_SCENE=scene,
            CODEX_NOIR_STYLE="dither",
            CODEX_NOIR_DRIVE=drive,
        )
        env.pop("NO_COLOR", None)
        if ansi256:
            env.pop("COLORTERM", None)
            env["TERM_PROGRAM"] = "Apple_Terminal"
        usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        proc = subprocess.Popen(
            [str(binary), "--no-alt-screen"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=project,
            env=env,
            start_new_session=True,
        )
        os.close(slave)
        screen = pyte.Screen(columns, rows)
        stream = pyte.Stream(screen)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        raw, pending = bytearray(), b""

        def complete():
            return raw.rfind(FRAME_START) <= raw.rfind(FRAME_END)

        def pump(duration, predicate=None):
            nonlocal pending
            deadline = time.monotonic() + duration
            while proc.poll() is None:
                now = time.monotonic()
                if now >= deadline and (complete() or now >= deadline + 1):
                    break
                ready, _, _ = select.select([master], [], [], 0.005)
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    raw.extend(data)
                    pending += data
                    for query, reply in (
                        (
                            b"\x1b]11;?",
                            b"\x1b]11;rgb:f7f7/f3f3/ecec\x1b\\"
                            if light
                            else b"\x1b]11;rgb:1414/1313/2020\x1b\\",
                        ),
                        (
                            b"\x1b]10;?",
                            b"\x1b]10;rgb:2020/1919/3333\x1b\\"
                            if light
                            else b"\x1b]10;rgb:ecec/eaea/f5f5\x1b\\",
                        ),
                        (b"\x1b[6n", b"\x1b[1;1R"),
                    ):
                        while query in pending:
                            pending = pending.replace(query, b"", 1)
                            os.write(master, reply)
                    pending = pending[-32:]
                    stream.feed(decoder.decode(data))
                if predicate and complete() and predicate():
                    return True
            return False

        def art():
            return sorted(
                (y, x, cell.data, cell.fg)
                for y, line in screen.buffer.items()
                for x, cell in line.items()
                if any("\u2800" <= char <= "\u28ff" for char in cell.data)
            )

        def composer_has(text):
            return any(
                line.lstrip().startswith((f"› {text}", f"» {text}"))
                for line in screen.display
            )

        def warp_bar():
            for y, line in enumerate(screen.display):
                for label in ("EXTRA THINKING", "ULTRA"):
                    if label in line and "━" in line:
                        return {"label": label, "row": y, "column": line.index(label)}
            return None

        result = None
        try:
            if not pump(10, lambda: composer_has("")):
                raise RuntimeError("The composer did not become ready")
            prompt = "Hold this local benchmark"
            os.write(master, b"\x1b[200~" + prompt.encode() + b"\x1b[201~")
            if not pump(3, lambda: composer_has(prompt)):
                raise RuntimeError("Initial input did not reach the composer")
            pump(1.2)
            os.write(master, b"\r")
            if not pump(3, server.started.is_set):
                os.write(master, b"\r")
                if not pump(7, server.started.is_set):
                    raise RuntimeError("The local fixture request did not start")
            if streaming and not pump(
                10, lambda: any("Fixture row" in line for line in screen.display)
            ):
                raise RuntimeError(
                    "The streaming response did not reach the transcript"
                )
            pump(0.5)
            before_bytes = len(raw)
            before_frames = raw.count(FRAME_END)
            pump(2)
            working_bytes = len(raw) - before_bytes
            working_frames = raw.count(FRAME_END) - before_frames
            warp_before = warp_bar()
            warp_columns = {warp_before["column"]} if warp_before else set()
            expected, latencies = "", []
            for character in "draft-input-latency":
                expected += character
                sent = time.perf_counter()
                os.write(master, character.encode())
                if not pump(2, lambda text=expected: composer_has(text)):
                    raise RuntimeError(
                        f"Input stalled after {len(expected)} characters"
                    )
                latencies.append((time.perf_counter() - sent) * 1000)
                pump(0.08)
                if active_bar := warp_bar():
                    warp_columns.add(active_bar["column"])
            for _ in range(5):
                os.write(master, b"\x1b[D")
                pump(0.08)
            cursor = (screen.cursor.x, screen.cursor.y)
            pump(0.6)
            cursor_after = (screen.cursor.x, screen.cursor.y)
            cursor_preserved = cursor == cursor_after
            warp_after = warp_bar()
            (case / "working.txt").write_text("\n".join(screen.display))
            (case / "working.ansi").write_bytes(raw)
            if streaming:
                server.release.set()
                idle_marker = "Noir benchmark complete."
            else:
                os.write(master, b"\x1b")
                idle_marker = "Conversation interrupted"
            if not pump(
                10,
                lambda: (
                    any(idle_marker in line for line in screen.display)
                    and not any(
                        "WORKING" in line or "Working (" in line
                        for line in screen.display
                    )
                    and warp_bar() is None
                ),
            ):
                raise RuntimeError("The local fixture turn did not become idle")
            pump(0.2)
            idle_art = art()
            pump(0.6)
            result = {
                "case": name,
                "columns": columns,
                "rows": rows,
                "ansi256": ansi256,
                "streaming": streaming,
                "effort": effort,
                "light": light,
                "warp_before": warp_before,
                "warp_after": warp_after,
                "warp_columns": sorted(warp_columns),
                "warp_hidden_while_idle": warp_bar() is None,
                "input_median_ms": round(statistics.median(latencies), 3),
                "input_max_ms": round(max(latencies), 3),
                "working_bytes_per_second": working_bytes // 2,
                "working_frames_per_second": working_frames / 2,
                "draft_preserved": composer_has(expected),
                "cursor_preserved_during_animation": cursor_preserved,
                "cursor_before": cursor,
                "cursor_after": cursor_after,
                "idle_decoration_stable": idle_art == art(),
                "idle_braille_cells": len(idle_art),
            }
            os.write(master, b"\x03\x03")
            pump(2)
        finally:
            server.release.set()
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            finally:
                os.close(master)
                server.shutdown()
                server.server_close()
                (case / "terminal.ansi").write_bytes(raw)
                (case / "final.txt").write_text("\n".join(screen.display))
        usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        result["cpu_seconds"] = round(
            usage_after.ru_utime
            + usage_after.ru_stime
            - usage_before.ru_utime
            - usage_before.ru_stime,
            3,
        )
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (case / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        assert result["draft_preserved"] and cursor_preserved, result
        assert result["idle_decoration_stable"], result
        assert bool(idle_art) == (animations and scene != "off"), result
        if effort in ("xhigh", "max", "ultra") and animations:
            label = "ULTRA" if effort == "ultra" else "EXTRA THINKING"
            assert warp_before and warp_after, result
            assert warp_before["label"] == warp_after["label"] == label, result
            assert len(warp_columns) > 1, result
        else:
            assert warp_before is None and warp_after is None, result
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/terminal-benchmark"
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=["cruise", "overdrive", "warp", "scene-off", "motion-off"],
        choices=["cruise", "overdrive", "warp", "scene-off", "motion-off"],
    )
    parser.add_argument("--columns", type=int, default=120)
    parser.add_argument("--rows", type=int, default=36)
    parser.add_argument("--ansi256", action="store_true")
    parser.add_argument("--light", action="store_true")
    parser.add_argument(
        "--effort", choices=["high", "xhigh", "max", "ultra"], default="high"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Type while a 500-line response and continuing updates stream",
    )
    args = parser.parse_args()
    results = []
    for name in args.cases:
        result = run_case(
            args.binary.resolve(),
            name,
            args.output,
            args.columns,
            args.rows,
            args.ansi256,
            args.stream,
            args.effort,
            args.light,
        )
        results.append(result)
        print(json.dumps(result), flush=True)
    (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
