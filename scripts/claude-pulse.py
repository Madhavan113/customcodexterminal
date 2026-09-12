#!/usr/bin/env python3
"""Record Claude Code's activity and effort for the claude-noir rail.

`claude-noir` registers this script as an asynchronous command hook for the
session it launches. Every hook event arrives as JSON on stdin and is folded
into a small state file named by CLAUDE_NOIR_STATE, which the dancer pane
reads to label and color its rail. Without that variable the script does
nothing, and it always exits 0 so it can never hold up Claude.
"""

import json
import os
import sys
import time
from pathlib import Path

EFFORTS = ("low", "medium", "high", "xhigh", "max")
WAITING_NOTIFICATIONS = {
    "permission_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def merged_settings(cwd):
    """Claude Code's user and project settings, later files overriding earlier keys."""
    files = [Path.home() / ".claude/settings.json"]
    if cwd:
        files.append(Path(cwd) / ".claude/settings.json")
        files.append(Path(cwd) / ".claude/settings.local.json")
    settings = {}
    for path in files:
        settings.update(read_json(path))
    return settings


def saved_effort(settings, model):
    per_model = settings.get("modelSettings")
    if model and isinstance(per_model, dict) and isinstance(per_model.get(model), dict):
        entry = per_model[model]
        level = entry.get("effortLevel", entry.get("effort"))
        if isinstance(level, str):
            return level
    level = settings.get("effortLevel")
    return level if isinstance(level, str) else None


def update(state, event, environ, now):
    """Fold one hook event into the state dictionary."""
    name = event.get("hook_event_name", "")
    state.setdefault("phase", "idle")
    if isinstance(event.get("session_id"), str):
        state["session_id"] = event["session_id"]
    model = event.get("to_model") or event.get("model")
    if isinstance(model, str) and model:
        state["model"] = model
    tool = event.get("tool_name") if isinstance(event.get("tool_name"), str) else None

    if name == "SessionStart":
        state.update(phase="idle", tool=None, ultrathink=False)
    elif name == "UserPromptSubmit":
        prompt = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
        state.update(
            phase="thinking", tool=None, ultrathink="ultrathink" in prompt.lower()
        )
    elif name == "PreToolUse":
        state.update(phase="tool", tool=tool)
    elif name in ("PostToolUse", "PostToolUseFailure"):
        state.update(phase="thinking", tool=None)
    elif name == "PermissionRequest":
        state.update(phase="waiting", tool=tool)
    elif name == "Notification":
        kind = event.get("notification_type")
        if kind in WAITING_NOTIFICATIONS:
            state["phase"] = "waiting"
        elif kind == "idle_prompt":
            state.update(phase="idle", tool=None)
    elif name in ("Stop", "StopFailure"):
        state.update(phase="idle", tool=None, ultrathink=False)
    elif name == "SessionEnd":
        state.update(phase="idle", tool=None, ultrathink=False, ended=True)

    settings = merged_settings(event.get("cwd"))
    reported = event.get("effort") if isinstance(event.get("effort"), dict) else {}
    level = (
        environ.get("CLAUDE_EFFORT")
        or reported.get("level")
        or environ.get("CLAUDE_CODE_EFFORT_LEVEL")
        or environ.get("CLAUDE_NOIR_EFFORT")
        or saved_effort(settings, state.get("model"))
    )
    if isinstance(level, str) and level.lower() in EFFORTS:
        state["effort"] = level.lower()
    state["ultracode"] = (
        environ.get("CLAUDE_NOIR_ULTRACODE") == "1" or settings.get("ultracode") is True
    )
    state["updated"] = now
    return state


def write_state(path, state):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(temporary, target)


def main():
    path = os.environ.get("CLAUDE_NOIR_STATE")
    if not path:
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    try:
        write_state(path, update(read_json(path), event, os.environ, time.time()))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
