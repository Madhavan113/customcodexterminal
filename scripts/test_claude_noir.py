"""Regression tests for the claude-noir companion: the dancer pane and its hook pulse."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SGR = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"claude-{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dancer = load("dancer")
pulse = load("pulse")


def rows(seconds, columns, lines, state=None):
    return SGR.sub("", dancer.frame(seconds, columns, lines, state)).split("\r\n")


class DancerTests(unittest.TestCase):
    def test_frames_fill_the_pane_exactly(self):
        states = (
            None,
            {"phase": "idle", "effort": "high"},
            {"phase": "waiting", "effort": "max"},
            {"phase": "thinking", "effort": "xhigh"},
            {"phase": "tool", "effort": "max", "tool": "Read"},
        )
        for columns in (20, 28, 44, 60, 120):
            for lines in (3, 4, 8, 12):
                for state in states:
                    for tick in range(0, 60, 7):
                        painted = rows(tick / 12, columns, lines, state)
                        self.assertEqual(len(painted), lines)
                        self.assertTrue(all(len(row) == columns for row in painted))

    def test_painter_sends_only_the_cells_that_changed(self):
        state = {"phase": "tool", "effort": "xhigh", "tool": "Bash"}
        painter = dancer.Painter()
        first = painter.paint(0.0, 120, 14, state)
        self.assertTrue(first.startswith("\x1b[2J\x1b[?2026h\x1b[H"))
        self.assertEqual(first[len("\x1b[2J") :], dancer.frame(0.0, 120, 14, state))
        # The same instant again changes nothing, so nothing is sent.
        self.assertEqual(painter.paint(0.0, 120, 14, state), "")
        # A later instant repaints only the rows that moved, each addressed by position.
        delta = painter.paint(1 / 12, 120, 14, state)
        self.assertIn("H", delta)
        self.assertNotIn("\x1b[H", delta)
        self.assertLess(len(delta), len(dancer.frame(1 / 12, 120, 14, state)))
        self.assertEqual(painter.rows, dancer.compose(1 / 12, 120, 14, state))
        # The idle rail is still, so idle frames cost far less than a full repaint.
        idle = {"phase": "idle", "effort": "medium"}
        painter.paint(0.0, 120, 14, idle)
        self.assertLess(
            len(painter.paint(1 / 12, 120, 14, idle)),
            len(dancer.frame(1 / 12, 120, 14, idle)) / 2,
        )
        # A resize repaints the whole pane from a cleared screen.
        resized = painter.paint(2.0, 90, 10, idle)
        self.assertEqual(resized, "\x1b[2J" + dancer.frame(2.0, 90, 10, idle))

    def test_shiba_runs_beside_the_mascot_when_there_is_room(self):
        wide = "".join(rows(3.0, 80, 8))
        self.assertIn("▟▙▟▙", wide)
        self.assertIn("▝██████▟▀▘", wide)
        narrow = "".join(rows(3.0, 20, 3))
        self.assertNotIn("▟▙▟▙", narrow)

    def test_shiba_mirrors_when_facing_left(self):
        # Half a cycle later the pair returns, so the sprite must be mirrored, not reversed.
        painted = "".join(rows(8.6, 80, 8))
        self.assertIn("▟▙▟▙  ▐▌", painted)
        self.assertIn("▝▀▙██████▘", painted)

    def test_rail_names_the_phase_tool_and_effort(self):
        cases = {
            ("thinking", None, "xhigh"): " CLAUDE · EXTRA THINKING ",
            (
                "tool",
                "mcp__github__list_pull_requests",
                "max",
            ): " CLAUDE · LIST_PULL_REQ… · ULTRA ",
            ("waiting", "Bash", "high"): " CLAUDE · WAITING FOR YOU · HIGH ",
            ("idle", None, "low"): " CLAUDE · LOW ",
        }
        for (phase, tool, effort), expected in cases.items():
            state = {"phase": phase, "tool": tool, "effort": effort}
            self.assertIn(expected, rows(1.0, 80, 8, state)[-1])
        self.assertIn(" CLAUDE · ULTRACODE ", rows(1.0, 80, 8, {"ultracode": True})[-1])
        ultrathink = {"phase": "thinking", "effort": "xhigh", "ultrathink": True}
        self.assertIn(" CLAUDE · EXTRA THINKING ", rows(1.0, 80, 8, ultrathink)[-1])
        self.assertIn(" CLAUDE ", rows(1.0, 80, 8, None)[-1])

    def test_rail_is_still_while_idle_and_moves_while_busy(self):
        idle = {"phase": "idle", "effort": "high"}
        busy = {"phase": "thinking", "effort": "high"}
        rail = lambda seconds, state: dancer.frame(seconds, 80, 8, state).split("\r\n")[
            -1
        ]
        self.assertEqual(rail(0.0, idle), rail(1.0, idle))
        self.assertNotEqual(rail(0.0, busy), rail(1.0, busy))

    def test_rail_colors_follow_the_effort(self):
        for effort in ("high", "xhigh", "max"):
            rail = dancer.frame(
                0.7, 80, 8, {"phase": "thinking", "effort": effort}
            ).split("\r\n")[-1]
            colors = {int(code) for code in re.findall(r"38;5;(\d+)", rail)}
            if effort == "high":
                self.assertTrue(colors <= set(dancer.RAMPS["cruise"]), colors)
            else:
                backgrounds = {int(code) for code in re.findall(r"48;5;(\d+)", rail)}
                self.assertTrue(backgrounds)
                self.assertTrue(
                    backgrounds <= set(dancer.WARP_BACKGROUNDS), backgrounds
                )
                self.assertIn(231, colors)

    def test_warp_bar_moves_only_during_work_and_stays_inside_its_row(self):
        for effort in ("xhigh", "max"):
            state = {"phase": "thinking", "effort": effort}
            title = "ULTRA" if effort == "max" else "EXTRA THINKING"
            offsets = []
            for seconds in (0.0, 0.5, 1.3):
                raw = dancer.frame(seconds, 80, 8, state).split("\r\n")
                self.assertTrue(all("48;5;" not in row for row in raw[:-1]))
                offsets.append(SGR.sub("", raw[-1]).index(title))
            self.assertGreater(len(set(offsets)), 1)
            for phase in ("idle", "waiting"):
                stopped = {**state, "phase": phase}
                self.assertIsNone(dancer.warp_label(stopped))
                self.assertNotIn("48;5;", dancer.frame(0.0, 80, 8, stopped))
            idle = {**state, "phase": "idle"}
            self.assertEqual(
                dancer.frame(0.0, 80, 8, idle).split("\r\n")[-1],
                dancer.frame(2.0, 80, 8, idle).split("\r\n")[-1],
            )
        for feature in ("ultrathink", "ultracode"):
            state = {"phase": "thinking", feature: True}
            self.assertIn("48;5;", dancer.frame(0.0, 80, 8, state))

    def test_rail_yields_when_the_runners_would_not_fit(self):
        painted = rows(1.0, 30, 3)
        self.assertNotIn("─", "".join(painted))


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        settings = Path(self.home.name) / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text(
            json.dumps(
                {
                    "effortLevel": "medium",
                    "modelSettings": {"claude-fable-5-1": {"effortLevel": "xhigh"}},
                }
            )
        )

    def tearDown(self):
        os.environ["HOME"] = self.previous_home
        self.home.cleanup()

    def test_events_fold_into_phases(self):
        state = {}
        env = {}
        pulse.update(
            state,
            {"hook_event_name": "SessionStart", "model": "claude-fable-5-1"},
            env,
            1,
        )
        self.assertEqual((state["phase"], state["effort"]), ("idle", "xhigh"))
        pulse.update(
            state,
            {"hook_event_name": "UserPromptSubmit", "prompt": "ULTRATHINK it"},
            env,
            2,
        )
        self.assertEqual((state["phase"], state["ultrathink"]), ("thinking", True))
        pulse.update(
            state, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, env, 3
        )
        self.assertEqual((state["phase"], state["tool"]), ("tool", "Bash"))
        pulse.update(
            state, {"hook_event_name": "PostToolUse", "tool_name": "Bash"}, env, 4
        )
        self.assertEqual((state["phase"], state["tool"]), ("thinking", None))
        pulse.update(
            state, {"hook_event_name": "PermissionRequest", "tool_name": "Edit"}, env, 5
        )
        self.assertEqual((state["phase"], state["tool"]), ("waiting", "Edit"))
        pulse.update(
            state,
            {"hook_event_name": "Notification", "notification_type": "auth_success"},
            env,
            6,
        )
        self.assertEqual(state["phase"], "waiting")
        pulse.update(state, {"hook_event_name": "Stop"}, env, 7)
        self.assertEqual(
            (state["phase"], state["ultrathink"], state["tool"]), ("idle", False, None)
        )

    def test_effort_prefers_the_hook_environment_then_the_event_then_settings(self):
        state = {}
        pulse.update(state, {"hook_event_name": "SessionStart"}, {}, 1)
        self.assertEqual(state["effort"], "medium")
        pulse.update(
            state, {"hook_event_name": "Stop", "effort": {"level": "max"}}, {}, 2
        )
        self.assertEqual(state["effort"], "max")
        pulse.update(
            state,
            {"hook_event_name": "Stop", "effort": {"level": "max"}},
            {"CLAUDE_EFFORT": "high"},
            3,
        )
        self.assertEqual(state["effort"], "high")
        pulse.update(state, {"hook_event_name": "Stop"}, {"CLAUDE_EFFORT": "bogus"}, 4)
        self.assertEqual(state["effort"], "high")

    def test_ultracode_comes_from_the_launcher_or_settings(self):
        state = {}
        pulse.update(state, {"hook_event_name": "Stop"}, {}, 1)
        self.assertFalse(state["ultracode"])
        pulse.update(
            state, {"hook_event_name": "Stop"}, {"CLAUDE_NOIR_ULTRACODE": "1"}, 2
        )
        self.assertTrue(state["ultracode"])
        Path(self.home.name, ".claude/settings.json").write_text(
            json.dumps({"ultracode": True})
        )
        pulse.update(state, {"hook_event_name": "ConfigChange"}, {}, 3)
        self.assertTrue(state["ultracode"])

    def test_launcher_effort_is_a_fallback_to_current_native_hook_state(self):
        state = {}
        env = {"CLAUDE_NOIR_EFFORT": "max"}
        pulse.update(state, {"hook_event_name": "SessionStart"}, env, 1)
        self.assertEqual(state["effort"], "max")
        pulse.update(
            state,
            {"hook_event_name": "UserPromptSubmit", "effort": {"level": "xhigh"}},
            env,
            2,
        )
        self.assertEqual(state["effort"], "xhigh")
        pulse.update(
            state,
            {"hook_event_name": "PreToolUse"},
            {**env, "CLAUDE_EFFORT": "high"},
            3,
        )
        self.assertEqual(state["effort"], "high")
        pulse.update(
            state,
            {"hook_event_name": "UserPromptSubmit"},
            {**env, "CLAUDE_CODE_EFFORT_LEVEL": "low"},
            4,
        )
        self.assertEqual(state["effort"], "low")

    def test_script_writes_the_state_file_and_never_fails(self):
        target = Path(self.home.name, "state", "session.json")
        env = {**os.environ, "CLAUDE_NOIR_STATE": str(target), "CLAUDE_EFFORT": "xhigh"}
        run = lambda payload, env: subprocess.run(
            [sys.executable, str(SCRIPTS / "claude-pulse.py")],
            input=payload,
            text=True,
            env=env,
            capture_output=True,
            check=False,
        )
        result = run(
            json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}), env
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        state = json.loads(target.read_text())
        self.assertEqual((state["phase"], state["effort"]), ("thinking", "xhigh"))
        result = run("not json", env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(target.read_text())["phase"], "thinking")
        self.assertEqual([p for p in target.parent.iterdir() if p.suffix == ".tmp"], [])
        without = {
            key: value for key, value in env.items() if key != "CLAUDE_NOIR_STATE"
        }
        self.assertEqual(run("{}", without).returncode, 0)


if __name__ == "__main__":
    unittest.main()
