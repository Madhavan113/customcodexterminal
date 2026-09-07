"""Exercise the real terminal widgets with local state, without starting models."""

import asyncio
import copy
import importlib.util
import time
import unittest
from pathlib import Path

HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
if HAS_TEXTUAL:
    from noir_workspace.ui import AttentionDetail, NewAgent, WorkspaceApp
    from textual.widgets import Input, Select, TextArea

from noir_workspace.rpc import RpcError


def agent(agent_id, title, provider="codex", parent=None, status="working"):
    return {
        "id": agent_id,
        "title": title,
        "provider": provider,
        "parent": parent,
        "status": status,
        "detail": "Running fixture checks",
        "model": "fixture-model",
        "cwd": "/tmp/noir-example",
        "native_id": f"native-{agent_id}",
        "can_message": not parent,
        "can_interrupt": not parent,
        "created": time.time(),
        "updated": time.time(),
    }


class LocalUIClient:
    def __init__(self):
        self.agents = [
            agent("integration", "Integration"),
            agent("tests", "Tests", parent="integration"),
            agent("renderer", "Renderer", "claude", status="ready"),
        ]
        self.events = [
            {
                "id": 1,
                "agent": "integration",
                "kind": "assistant",
                "message": "The activity labels are updated. I am checking the input layout.",
                "data": {},
                "stamp": time.time(),
            }
        ]
        self.inbox = []
        self.calls = []
        self.send_error = False
        self.send_gate = None

    async def __call__(self, method, params=None):
        params = params or {}
        self.calls.append((method, copy.deepcopy(params)))
        if method == "snapshot":
            selected = params.get("selected") or (
                self.agents[0]["id"] if self.agents else None
            )
            return copy.deepcopy(
                {
                    "project": "/tmp/noir-example",
                    "revision": len(self.events),
                    "agents": self.agents,
                    "selected": selected,
                    "events": [
                        item for item in self.events if item["agent"] == selected
                    ],
                    "inbox": self.inbox,
                }
            )
        if method == "changes":
            return {
                "branch": "noir/fixture",
                "scope_label": "Since agent started · shared folder, all writers",
                "files": [
                    {
                        "path": "src/activity.rs",
                        "status": "M",
                        "added": 2,
                        "removed": 1,
                    },
                    {"path": "tests/input.rs", "status": "A", "added": 1, "removed": 0},
                ],
                "selected": params.get("selected") or "src/activity.rs",
                "diff": "--- a/src/activity.rs\n+++ b/src/activity.rs\n@@ -1 +1,2 @@\n-label = old\n+label = current\n+keep_cursor = true\n",
            }
        if method == "send":
            if self.send_gate:
                await self.send_gate.wait()
            if self.send_error:
                raise RpcError("Fixture disconnected; draft kept")
            return {"message": "Instruction delivered"}
        if method == "launch":
            self.agents.append(agent("new-agent", params["title"], params["provider"]))
            return {"agent_id": "new-agent"}
        if method == "resolve":
            self.inbox = [
                item for item in self.inbox if item["id"] != params["request_id"]
            ]
        return {"message": "Acknowledged"}


@unittest.skipUnless(
    HAS_TEXTUAL, "Install scripts/requirements-workspace.txt for UI checks"
)
class WorkspaceUITests(unittest.IsolatedAsyncioTestCase):
    async def test_background_activity_and_inbox_preserve_draft_cursor_and_focus(self):
        client = LocalUIClient()
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=client)
        async with app.run_test(size=(120, 36)) as pilot:
            app.composer.focus()
            app.composer.load_text("Keep this draft\nsecond line")
            app.composer.move_cursor((0, 5))
            client.events.append(
                {
                    "id": 2,
                    "agent": "integration",
                    "kind": "command",
                    "message": "fixture-test",
                    "data": {},
                    "stamp": time.time(),
                }
            )
            client.inbox.append(
                {
                    "id": "request-1",
                    "agent": "integration",
                    "kind": "approval",
                    "status": "pending",
                    "title": "Approve fixture command",
                    "data": {"detail": "fixture-test", "choices": ["allow", "deny"]},
                }
            )
            await app.poll()
            await pilot.pause()
            self.assertEqual(app.composer.text, "Keep this draft\nsecond line")
            self.assertEqual(app.composer.cursor_location, (0, 5))
            self.assertIs(app.focused, app.composer)
            self.assertTrue(app.inbox_widget.display)
            self.assertEqual(len(app.tree_nodes), 3)

    async def test_switching_agents_restores_each_draft(self):
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=LocalUIClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app.composer.load_text("Integration draft")
            app.composer.move_cursor((0, 4))
            app.select_agent("renderer")
            app.composer.load_text("Renderer draft")
            app.select_agent("integration")
            await pilot.pause()
            self.assertEqual(app.composer.text, "Integration draft")
            self.assertEqual(app.composer.cursor_location, (0, 4))
            app.select_agent("renderer")
            self.assertEqual(app.composer.text, "Renderer draft")

    async def test_send_failure_and_inflight_edits_do_not_erase_input(self):
        client = LocalUIClient()
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=client)
        async with app.run_test(size=(110, 32)):
            client.send_error = True
            app.composer.load_text("original instruction")
            await app.action_send_message()
            self.assertEqual(app.composer.text, "original instruction")
            client.send_error = False
            client.send_gate = asyncio.Event()
            task = asyncio.create_task(app.action_send_message())
            await asyncio.sleep(0)
            app.composer.load_text("original instruction plus new typing")
            client.send_gate.set()
            await task
            self.assertEqual(app.composer.text, "original instruction plus new typing")
            client.send_gate = None
            await app.action_send_message()
            self.assertEqual(app.composer.text, "")

    async def test_new_agent_dialog_keyboard_flow_and_changes(self):
        client = LocalUIClient()
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=client)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("ctrl+n")
            self.assertIsInstance(app.screen, NewAgent)
            app.screen.query_one("#task-name", Input).value = "Check rendering"
            app.screen.query_one("#task-prompt", TextArea).load_text(
                "Check the small terminal layout"
            )
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertTrue(
                any(
                    method == "launch" and params["title"] == "Check rendering"
                    for method, params in client.calls
                )
            )
            self.assertEqual(app.selected, "new-agent")
            await pilot.press("ctrl+g")
            await pilot.pause()
            self.assertEqual(app.tabs.active, "changes-tab")
            self.assertEqual(app.file_widget.option_count, 2)

    async def test_permission_action_requires_explicit_choice_and_modal_keeps_draft(
        self,
    ):
        client = LocalUIClient()
        entry = {
            "id": "permission",
            "agent": "integration",
            "kind": "approval",
            "status": "pending",
            "title": "Approve fixture command",
            "data": {"detail": "printf 'example'", "choices": ["allow", "deny"]},
        }
        client.inbox.append(entry)
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=client)
        async with app.run_test(size=(100, 32)) as pilot:
            app.composer.load_text("Keep me")
            await pilot.press("f6", "enter")
            self.assertIsInstance(app.screen, AttentionDetail)
            self.assertFalse(any(method == "resolve" for method, _ in client.calls))
            await pilot.press("ctrl+s")
            self.assertFalse(any(method == "send" for method, _ in client.calls))
            await pilot.click("#deny")
            await pilot.pause()
            answers = [params for method, params in client.calls if method == "resolve"]
            self.assertEqual(answers[0]["answer"], {"decision": "deny"})
            self.assertEqual(app.composer.text, "Keep me")

    async def test_question_choices_and_free_text(self):
        client = LocalUIClient()
        client.inbox.append(
            {
                "id": "question",
                "agent": "renderer",
                "kind": "question",
                "status": "pending",
                "title": "Choose a layout",
                "data": {
                    "questions": [
                        {
                            "id": "layout",
                            "question": "Which layout?",
                            "options": [{"label": "Compact"}, {"label": "Wide"}],
                        }
                    ]
                },
            }
        )
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=client)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("f6", "enter")
            app.screen.query_one("#choices-0", Select).value = "Compact"
            await pilot.click("#answer")
            await pilot.pause()
            answers = [params for method, params in client.calls if method == "resolve"]
            self.assertEqual(answers[0]["answer"], {"answers": {"layout": "Compact"}})

    async def test_resizing_keeps_input_and_controls_reachable(self):
        app = WorkspaceApp(Path("/tmp/noir-example"), None, client=LocalUIClient())
        async with app.run_test(size=(120, 36)) as pilot:
            app.composer.load_text("Resize draft")
            app.composer.move_cursor((0, 3))
            for width, height in ((80, 24), (60, 24), (40, 18), (120, 36)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                self.assertEqual(app.composer.text, "Resize draft")
                self.assertEqual(app.composer.cursor_location, (0, 3))
                self.assertLessEqual(app.composer.region.bottom, height)
                self.assertGreater(app.composer.region.height, 0)


if __name__ == "__main__":
    unittest.main()
