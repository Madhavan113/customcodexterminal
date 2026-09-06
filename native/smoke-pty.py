#!/usr/bin/env python3
"""Exercise only the bundled worker with temporary, unauthenticated child processes."""
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import time

WORKER = Path(__file__).resolve().parents[1] / "build/Dragon Terminal.app/Contents/MacOS/dragon-pty"


def start(code):
    return subprocess.Popen(
        [str(WORKER), "--cwd", "/private/tmp", "--cols", "100", "--rows", "30", "--", sys.executable, "-u", "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def frame(child, kind, payload=b""):
    child.stdin.write(bytes([kind]) + struct.pack(">I", len(payload)) + payload)
    child.stdin.flush()


def read_until(child, marker=None, timeout=8):
    result = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([child.stdout], [], [], max(0, deadline - time.monotonic()))
        if not readable:
            break
        data = os.read(child.stdout.fileno(), 65536)
        if not data:
            return bytes(result)
        result.extend(data)
        if marker is not None and marker in result:
            return bytes(result)
    raise AssertionError(f"Timed out waiting for {marker!r}; received {bytes(result[-200:])!r}")


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def main():
    child = start("import os,sys; print('TTY:'+str(os.isatty(0) and os.isatty(1))); print('READY'); data=sys.stdin.buffer.readline(); print('SIZE:'+str(tuple(os.get_terminal_size(0)))); sys.stdout.buffer.write(b'GOT:'+data)")
    assert b"TTY:True" in read_until(child, b"READY")
    frame(child, 2, struct.pack(">HH", 132, 43))
    encoded = "dragon 🐉 café\n".encode()
    frame(child, 1, encoded[:9])
    frame(child, 1, encoded[9:])
    result = read_until(child)
    assert b"SIZE:(132, 43)" in result, result
    assert b"GOT:" + encoded.rstrip(b"\n") in result, result
    assert child.wait(timeout=3) == 0
    print("PASS: interactive PTY, fragmented UTF-8 input, window resize, natural exit")

    child = start("import signal,sys,time; signal.signal(signal.SIGINT, lambda *args: (print('INTERRUPTED'),sys.exit(7))); print('READY'); time.sleep(60)")
    read_until(child, b"READY")
    frame(child, 1, b"\x03")
    assert b"INTERRUPTED" in read_until(child)
    assert child.wait(timeout=3) == 7
    print("PASS: Ctrl-C reaches the foreground child and exit status is preserved")

    count = 2 * 1024 * 1024
    child = start(f"import sys; sys.stdout.buffer.write(b'x'*{count})")
    time.sleep(0.25)
    assert child.poll() is None, "expected output backpressure while reader is paused"
    output = read_until(child, timeout=15)
    assert output == b"x" * count, len(output)
    assert child.wait(timeout=3) == 0
    print("PASS: two-megabyte output survives reader pause and bounded backpressure")

    for malformed in (False, True):
        child = start("import os,time; print('PID:'+str(os.getpid())); time.sleep(60)")
        output = read_until(child, b"PID:")
        pid = int(output.split(b"PID:", 1)[1].strip())
        if malformed:
            child.stdin.write(b"\x01" + struct.pack(">I", 65537)); child.stdin.flush()
        else:
            frame(child, 3)
        read_until(child)
        result = child.wait(timeout=3)
        assert result == (64 if malformed else 129), result
        assert gone(pid), f"owned child {pid} survived shutdown"
    print("PASS: explicit shutdown and malformed-frame rejection terminate only owned children")

    first = start("import os,time; print('PID:'+str(os.getpid())); time.sleep(60)")
    second = start("import os,time; print('PID:'+str(os.getpid())); time.sleep(60)")
    first_pid = int(read_until(first, b"PID:").split(b"PID:", 1)[1].strip())
    second_pid = int(read_until(second, b"PID:").split(b"PID:", 1)[1].strip())
    frame(first, 3)
    read_until(first)
    assert first.wait(timeout=3) == 129 and gone(first_pid)
    assert second.poll() is None and not gone(second_pid), "closing one PTY affected another session"
    frame(second, 3)
    read_until(second)
    assert second.wait(timeout=3) == 129 and gone(second_pid)
    print("PASS: closing one terminal session leaves an independent terminal alive")


if __name__ == "__main__":
    main()
