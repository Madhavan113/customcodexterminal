#!/usr/bin/env python3
"""Send a task to a dedicated, resumable Claude Code peer in this repository."""

import argparse
import fcntl
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".agents-local"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt", nargs="?", help="Task text; stdin is used when omitted"
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Allow editing project files; default is review only",
    )
    parser.add_argument(
        "--new", action="store_true", help="Start a separate peer conversation"
    )
    args = parser.parse_args()
    executable = shutil.which("claude")
    if not executable:
        parser.error("Claude Code must be installed and logged in first.")
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        parser.error("Supply a task as an argument or on stdin.")
    STATE.mkdir(exist_ok=True, mode=0o700)
    with (STATE / "claude.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(
                "The Claude peer is already running a task in this repository."
            )
        path = STATE / "claude-session.json"
        existing = (
            json.loads(path.read_text()) if path.exists() and not args.new else None
        )
        session = existing["session_id"] if existing else str(uuid.uuid4())
        permissions = "Read,Glob,Grep,Edit,Write" if args.edit else "Read,Glob,Grep"
        command = [
            executable,
            "-p",
            "--output-format",
            "json",
            "--permission-prompts",
            "none",
            "--permission-mode",
            "acceptEdits" if args.edit else "dontAsk",
            "--tools",
            permissions,
            "--allowedTools",
            permissions,
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-chrome",
            "--resume" if existing else "--session-id",
            session,
        ]
        prompt = (
            "Read README.md for the current terminal project. "
            "If CLAUDE.md exists locally, read it for additional context.\n\n" + prompt
        )
        result = subprocess.run(
            command, input=prompt, text=True, capture_output=True, cwd=ROOT, check=False
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        try:
            response = json.loads(result.stdout)
        except ValueError:
            print(result.stdout, end="")
            return result.returncode or 1
        (STATE / "claude-last-result.json").write_text(
            json.dumps(response, indent=2) + "\n"
        )
        if response.get("session_id"):
            path.write_text(
                json.dumps({"session_id": response["session_id"]}, indent=2) + "\n"
            )
        print(response.get("result", result.stdout))
        if response.get("permission_denials"):
            print(
                "Some requested tools were outside the peer's assigned access; see .agents-local/claude-last-result.json.",
                file=sys.stderr,
            )
        return result.returncode or int(bool(response.get("is_error")))


if __name__ == "__main__":
    raise SystemExit(main())
