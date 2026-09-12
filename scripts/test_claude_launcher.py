"""Exercise launcher argument forwarding and high-effort detection without a model call."""

import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ClaudeLauncherTests(unittest.TestCase):
    def test_effort_flags_reach_the_hook_without_changing_claude_arguments(self):
        with tempfile.TemporaryDirectory(prefix="noir-launcher-") as temporary:
            base = Path(temporary)
            commands, share = base / "commands", base / "share with spaces"
            commands.mkdir()
            share.mkdir()
            for name in ("dancer.py", "pulse.py", "tmux.conf"):
                (share / name).write_text("# Inert test companion\n")
            for name, source in {
                "claude": "#!/bin/sh\nexit 0\n",
                "tput": "#!/bin/sh\nprintf '80\\n'\n",
                "tmux": (
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "with open(os.environ['NOIR_LAUNCH_LOG'], 'a') as output:\n"
                    "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                ),
            }.items():
                path = commands / name
                path.write_text(source)
                path.chmod(0o755)
            log = base / "tmux.jsonl"
            env = {
                **os.environ,
                "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                "CLAUDE_NOIR_SHARE": str(share),
                "XDG_STATE_HOME": str(base / "state"),
                "NOIR_LAUNCH_LOG": str(log),
            }
            env.pop("TMUX", None)
            env.pop("CLAUDE_NOIR_DANCER", None)
            for arguments, effort, ultracode in (
                (["--effort", "max", "a 'quoted' draft; still text"], "max", "0"),
                (["--effort=xhigh"], "xhigh", "0"),
                (["--effort", "ultracode", "--effort", "high"], "high", "0"),
                (["--effort=ultracode"], "", "1"),
                (["--", "--effort=max"], "", "0"),
            ):
                with self.subTest(arguments=arguments):
                    log.write_text("")
                    subprocess.run(
                        [str(ROOT / "bin/claude-noir"), *arguments],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    launch = next(call for call in calls if "new-session" in call)
                    words = shlex.split(launch[-1].rsplit("; tmux -L ", 1)[0])
                    client = words.index("claude")
                    assigned = dict(word.split("=", 1) for word in words[:client])
                    self.assertEqual(assigned["CLAUDE_NOIR_EFFORT"], effort)
                    self.assertEqual(assigned["CLAUDE_NOIR_ULTRACODE"], ultracode)
                    self.assertEqual(words[client + 1], "--settings")
                    settings = json.loads(words[client + 2])
                    self.assertIn("UserPromptSubmit", settings["hooks"])
                    self.assertEqual(words[client + 3 :], arguments)


if __name__ == "__main__":
    unittest.main()
