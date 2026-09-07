"""The noir command: terminal workspace plus scriptable agent controls."""

import argparse
import asyncio
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .git_view import project_root
from .rpc import RpcError, local_call
from .store import clean, private_directory, socket_path, state_path

ENTRY = Path(__file__).resolve().parent.parent / "noir-workspace.py"


async def ensure_daemon(project, directory):
    path = socket_path(directory)
    try:
        return await local_call(path, "ping", timeout=1)
    except (OSError, TimeoutError):
        pass
    private_directory(directory)
    log_path = directory / "daemon.log"
    with log_path.open("a") as log:
        log_path.chmod(0o600)
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                str(ENTRY),
                "--project",
                str(project),
                "--state-dir",
                str(directory.parent),
                "serve",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    for _ in range(60):
        try:
            return await local_call(path, "ping", timeout=0.5)
        except (OSError, TimeoutError):
            if process.poll() not in (None, 0):
                raise RpcError(
                    f"Session manager could not start. See {log_path}"
                ) from None
            await asyncio.sleep(0.1)
    raise RpcError(f"Session manager did not start. See {log_path}")


def parser():
    result = argparse.ArgumentParser(
        description="Noir: agents, changes and controls inside your terminal."
    )
    result.add_argument(
        "--project",
        default=".",
        help="Project directory (defaults to the current project)",
    )
    result.add_argument(
        "--state-dir", help="Private state base; defaults to ~/.local/state/noir"
    )
    commands = result.add_subparsers(dest="command")
    commands.add_parser("ui", help="Open the workspace (default)")
    commands.add_parser(
        "doctor", help="Check local dependencies without starting agents"
    )
    commands.add_parser("serve", help=argparse.SUPPRESS)
    listing = commands.add_parser("list", help="List agents in this workspace")
    listing.add_argument("--json", action="store_true")
    new = commands.add_parser("new", help="Start an agent on a task")
    new.add_argument("provider", choices=["codex", "claude"])
    new.add_argument(
        "prompt", nargs="?", help="Task instructions; read stdin when omitted"
    )
    new.add_argument("--name", help="Short task label")
    new.add_argument("--model", help="Override the provider's configured model")
    new.add_argument(
        "--worktree",
        action="store_true",
        help="Use a separate Git worktree starting at HEAD",
    )
    send = commands.add_parser(
        "send", help="Send an instruction to an agent (or its parent)"
    )
    send.add_argument("agent_id")
    send.add_argument("prompt", nargs="?", help="Message; read stdin when omitted")
    interrupt = commands.add_parser("interrupt", help="Interrupt an active run")
    interrupt.add_argument("agent_id")
    changes = commands.add_parser("changes", help="Show live task or Git changes")
    changes.add_argument("agent_id")
    changes.add_argument(
        "--git",
        action="store_true",
        help="Inspect staged, unstaged and untracked files",
    )
    changes.add_argument("--file", help="File whose diff to show")
    inbox = commands.add_parser(
        "inbox", help="Show questions, approvals, errors and results"
    )
    inbox.add_argument("--json", action="store_true")
    respond = commands.add_parser("respond", help="Answer a pending attention item")
    respond.add_argument("request_id")
    decisions = respond.add_mutually_exclusive_group()
    decisions.add_argument(
        "--allow", action="store_true", help="Allow this request once"
    )
    decisions.add_argument("--deny", action="store_true")
    decisions.add_argument("--dismiss", action="store_true")
    respond.add_argument("--answer", action="append", default=[], metavar="ID=TEXT")
    shutdown = commands.add_parser(
        "shutdown", help="Stop this workspace's session manager"
    )
    shutdown.add_argument(
        "--interrupt", action="store_true", help="Also stop active agent processes"
    )
    return result


def doctor():
    okay = True
    print(f"Python: {sys.version.split()[0]}")
    for package in ("textual", "claude-agent-sdk"):
        try:
            print(f"{package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            print(f"{package}: missing (install scripts/requirements-workspace.txt)")
            okay = False
    providers = []
    for name in ("codex-noir", "codex", "claude"):
        executable = shutil.which(name)
        print(f"{name}: {executable or 'not installed'}")
        if executable:
            providers.append(name)
    if not providers:
        okay = False
    print("Uses your existing CLI sign-in and settings. No agent was started.")
    return 0 if okay else 1


def prompt_text(args):
    if args.prompt is not None:
        return args.prompt
    if sys.stdin.isatty():
        raise ValueError("Supply instructions as an argument or on stdin")
    return sys.stdin.read(64_001)


async def command(args, project, directory):
    path = socket_path(directory)
    if args.command == "shutdown":
        result = await local_call(path, "shutdown", {"interrupt": args.interrupt})
        print(result["message"])
        return
    await ensure_daemon(project, directory)
    if args.command == "new":
        prompt = prompt_text(args)
        result = await local_call(
            path,
            "launch",
            {
                "provider": args.provider,
                "title": args.name or prompt.strip().split("\n")[0][:60],
                "prompt": prompt,
                "model": args.model,
                "isolated": args.worktree,
            },
        )
        print(result["agent_id"])
    elif args.command == "send":
        result = await local_call(
            path,
            "send",
            {"agent_id": args.agent_id, "prompt": prompt_text(args)},
            timeout=60,
        )
        print(result["message"])
    elif args.command == "interrupt":
        result = await local_call(path, "interrupt", {"agent_id": args.agent_id})
        print(result["message"])
    elif args.command == "changes":
        result = await local_call(
            path,
            "changes",
            {
                "agent_id": args.agent_id,
                "scope": "git" if args.git else "task",
                "selected": args.file,
            },
            timeout=60,
        )
        print(result["scope_label"])
        for item in result["files"]:
            print(f"{item['status']:2} {clean(item['path'])}")
        print(result["diff"])
    elif args.command == "respond":
        answer = {
            "decision": "allow"
            if args.allow
            else "deny"
            if args.deny
            else "dismiss"
            if args.dismiss
            else None
        }
        if args.answer:
            answers = {}
            for pair in args.answer:
                key, separator, text = pair.partition("=")
                if not separator:
                    raise ValueError("Use --answer ID=TEXT")
                answers[key] = text
            answer = {"answers": answers}
        result = await local_call(
            path, "resolve", {"request_id": args.request_id, "answer": answer}
        )
        print(result["message"])
    else:
        snapshot = await local_call(path, "snapshot")
        values = snapshot["inbox"] if args.command == "inbox" else snapshot["agents"]
        if args.json:
            print(json.dumps(values, indent=2))
        elif not values:
            print(
                "Nothing needs your attention."
                if args.command == "inbox"
                else "No agents yet. Open noir and press Ctrl+N."
            )
        else:
            for item in values:
                print(
                    f"{item['id']}  {item.get('provider', item.get('kind', '')):9} {item['status']:13} {clean(item['title'])}"
                )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor()
        project = project_root(args.project)
        directory = state_path(project, args.state_dir)
        if args.command == "serve":
            from .daemon import serve

            asyncio.run(serve(project, directory))
        elif args.command in (None, "ui"):
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError(
                    "Open noir in an interactive terminal; use noir list for scriptable status"
                )
            from .ui import WorkspaceApp

            asyncio.run(ensure_daemon(project, directory))
            WorkspaceApp(project, socket_path(directory)).run()
        else:
            asyncio.run(command(args, project, directory))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RpcError, TimeoutError, ImportError) as error:
        print(f"noir: {clean(error)}", file=sys.stderr)
        return 1
    return 0
