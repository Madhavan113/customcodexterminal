"""Workspace behaviour against disposable Git repositories and inert providers."""

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noir_workspace.engine import Engine
from noir_workspace.git_view import FILE_LIMIT, GitView, git
from noir_workspace.rpc import RpcError, local_call
from noir_workspace.store import Store, clean, socket_path

FIXTURE = Path(__file__).parent / "fixtures/noir_codex_server.py"


def repository(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    git(root, "config", "user.name", "Noir Test")
    git(root, "config", "user.email", "noir@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "code.txt").write_text("original\n")
    (root / ".gitignore").write_text("ignored/\n")
    git(root, "add", ".")
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Fixture baseline")


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


class GitBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="noir-git-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        repository(self.root)
        self.view = GitView(self.base / "state")

    def test_dirty_start_staged_and_untracked_are_preserved(self):
        (self.root / "code.txt").write_text("existing user edit\n")
        git(self.root, "add", "code.txt")
        (self.root / "draft.txt").write_text("existing untracked draft\n")
        index_before = git(self.root, "diff", "--cached").stdout
        self.view.capture(self.root, "task")
        self.assertEqual(self.view.inspect(self.root, "task")["files"], [])
        (self.root / "code.txt").write_text("existing user edit\nagent addition\n")
        result = self.view.inspect(self.root, "task")
        self.assertEqual([file["path"] for file in result["files"]], ["code.txt"])
        self.assertIn("+agent addition", result["diff"])
        self.assertNotIn("+existing user edit", result["diff"])
        self.assertEqual(git(self.root, "diff", "--cached").stdout, index_before)
        self.assertEqual(
            (self.root / "draft.txt").read_text(), "existing untracked draft\n"
        )
        full = self.view.inspect(self.root, "task", "git")
        self.assertEqual(
            {file["path"] for file in full["files"]}, {"code.txt", "draft.txt"}
        )

    def test_task_comparison_survives_commits_and_covers_add_delete_binary(self):
        (self.root / "binary").write_bytes(b"\0old")
        self.view.capture(self.root, "task")
        (self.root / "binary").write_bytes(b"\0new")
        (self.root / "code.txt").unlink()
        (self.root / "new file.txt").write_text("new text\n")
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "Agent changes",
        )
        result = self.view.inspect(self.root, "task")
        self.assertEqual(
            {file["path"]: file["status"] for file in result["files"]},
            {"binary": "M", "code.txt": "D", "new file.txt": "A"},
        )
        self.assertIn(
            "binary file",
            self.view.inspect(self.root, "task", selected="binary")["diff"],
        )

    def test_ignored_files_and_outward_symlinks_are_not_read(self):
        (self.root / "ignored").mkdir()
        (self.root / "ignored/private.txt").write_text("private")
        (self.base / "outside").mkdir()
        (self.base / "outside/private.txt").write_text("outside contents")
        (self.root / "link").symlink_to(self.base / "outside", target_is_directory=True)
        self.view.capture(self.root, "task")
        baseline = (self.view.directory / "task.json").read_text()
        self.assertNotIn("outside contents", baseline)
        self.assertNotIn("ignored/private.txt", baseline)
        self.assertEqual(
            self.view.read(self.root, "link/private.txt")["kind"], "unavailable"
        )

    def test_large_files_and_mode_only_changes_are_visible(self):
        (self.root / "large.bin").write_bytes(b"x" * (FILE_LIMIT + 1))
        self.view.capture(self.root, "task")
        (self.root / "code.txt").chmod(0o755)
        (self.root / "large.bin").write_bytes(b"y" * (FILE_LIMIT + 2))
        result = self.view.inspect(self.root, "task")
        self.assertEqual(len(result["files"]), 2)
        self.assertIn("Mode/type changed", result["diff"])
        self.assertIn(
            "large", self.view.inspect(self.root, "task", selected="large.bin")["diff"]
        )

    def test_git_view_handles_renames_without_shell_interpolation(self):
        destination = "`touch nope` $(echo bad) ' spaced.txt"
        git(self.root, "mv", "code.txt", destination)
        result = self.view.inspect(self.root, "task", "git")
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["path"], destination)
        self.assertIn("rename", result["diff"])
        self.assertFalse((self.root / "nope").exists())

    def test_staged_and_unstaged_diffs_remain_visible_when_they_cancel_out(self):
        path = self.root / "code.txt"
        path.write_text("staged version\n")
        git(self.root, "add", "code.txt")
        path.write_text("original\n")
        result = self.view.inspect(self.root, "task", "git")
        self.assertEqual(result["files"][0]["status"], "MM")
        self.assertIn("Staged changes", result["diff"])
        self.assertIn("+staged version", result["diff"])
        self.assertIn("Unstaged changes", result["diff"])
        self.assertIn("-staged version", result["diff"])

    def test_unborn_repo_and_non_git_folder(self):
        empty = self.base / "empty"
        empty.mkdir()
        self.view.capture(empty, "plain")
        self.assertIn("requires a Git", self.view.inspect(empty, "plain")["diff"])
        git(empty, "init", "-q")
        (empty / "first.txt").write_text("first\n")
        git(empty, "add", ".")
        result = self.view.inspect(empty, "new", "git")
        self.assertIn("+first", result["diff"])


class StoreTests(unittest.TestCase):
    def test_restart_expires_prompts_and_marks_active_agents_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            agent = store.create_agent("codex", "Test", "/tmp")
            store.update(agent["id"], status="working", turn_id="turn-1")
            store.attention(agent["id"], "approval", "Do it?", pending=True)
            store.event(agent["id"], "assistant", "hello", key="stream")
            store.event(agent["id"], "assistant", " world", key="stream", append=True)
            store.close()
            restored = Store(directory)
            self.addCleanup(restored.close)
            restored.recover()
            self.assertEqual(restored.agent(agent["id"])["status"], "disconnected")
            self.assertIsNone(restored.agent(agent["id"])["turn_id"])
            self.assertEqual(restored.inbox(), [])
            self.assertEqual(restored.events(agent["id"])[0]["message"], "hello world")
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((Path(directory) / "workspace.sqlite3").stat().st_mode),
                0o600,
            )

    def test_untrusted_output_is_plain_terminal_text(self):
        self.assertEqual(
            clean("\x1b[31mred\x1b[0m\x1b]52;c;c2VjcmV0\x07\u202eX\x00"), "redX"
        )
        self.assertEqual(clean("line\n\ttab"), "line\n\ttab")


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="noir-engine-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        repository(self.root)
        self.log = self.base / "protocol.jsonl"

    async def asyncSetUp(self):
        self.engine = Engine(
            self.root,
            self.base / "state",
            codex_command=[sys.executable, str(FIXTURE), str(self.log)],
        )
        self.addAsyncCleanup(self.engine.close)

    async def launch(self, prompt="task"):
        result = await self.engine.launch("codex", "Integration", prompt)
        agent_id = result["agent_id"]
        await until(
            lambda: (
                self.engine.store.agent(agent_id)["status"]
                in {"working", "waiting", "failed"}
            )
        )
        self.assertNotEqual(self.engine.store.agent(agent_id)["status"], "failed")
        return agent_id

    def messages(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def test_codex_stream_steering_and_interrupt_acknowledgement(self):
        agent_id = await self.launch()
        await until(
            lambda: any(
                event["message"] == "Fixture output"
                for event in self.engine.store.events(agent_id)
            )
        )
        prompt = "Keep my draft: `$(do not run)` 'quoted'\nsecond line"
        await self.engine.send(agent_id, prompt)
        steer = next(
            message
            for message in self.messages()
            if message.get("method") == "turn/steer"
        )
        self.assertEqual(steer["params"]["input"][0]["text"], prompt)
        self.assertEqual(steer["params"]["expectedTurnId"], "turn-fixture-1")
        await self.engine.interrupt(agent_id)
        await until(
            lambda: self.engine.store.agent(agent_id)["status"] == "interrupted"
        )
        self.assertIsNone(self.engine.store.agent(agent_id)["turn_id"])
        start = next(
            message
            for message in self.messages()
            if message.get("method") == "thread/start"
        )
        self.assertNotIn("approvalPolicy", start["params"])
        self.assertNotIn("sandbox", start["params"])

    async def test_permission_waits_for_explicit_choice_and_cannot_be_replayed(self):
        agent_id = await self.launch("need approval")
        await until(lambda: bool(self.engine.store.inbox()))
        entry = self.engine.store.inbox()[0]
        self.assertEqual(self.engine.store.agent(agent_id)["status"], "waiting")
        self.assertFalse(
            any(message.get("id") == "approval-1" for message in self.messages())
        )
        with self.assertRaises(ValueError):
            self.engine.resolve(entry["id"], {"decision": "allow-forever"})
        self.engine.resolve(entry["id"], {"decision": "deny"})
        await until(lambda: self.engine.store.agent(agent_id)["status"] == "ready")
        reply = next(
            message for message in self.messages() if message.get("id") == "approval-1"
        )
        self.assertEqual(reply["result"]["decision"], "decline")
        with self.assertRaises(ValueError):
            self.engine.resolve(entry["id"], {"decision": "allow"})

    async def test_failed_commands_go_to_attention(self):
        agent_id = await self.launch("failing command")
        await until(lambda: bool(self.engine.store.inbox()))
        self.assertEqual(self.engine.store.inbox()[0]["kind"], "error")
        self.assertIn("Fixture failure", self.engine.store.inbox()[0]["data"]["detail"])
        events = self.engine.store.events(agent_id)
        command = next(event for event in events if event["kind"] == "command")
        self.assertEqual(command["data"]["exit_code"], 2)

    async def test_completion_before_start_acknowledgement_stays_finished(self):
        agent_id = (await self.engine.launch("codex", "Fast task", "fast complete"))[
            "agent_id"
        ]
        await until(lambda: self.engine.jobs[agent_id].done())
        self.assertEqual(self.engine.store.agent(agent_id)["status"], "ready")
        self.assertIsNone(self.engine.store.agent(agent_id)["turn_id"])

    async def test_canceled_provider_request_disappears_from_inbox(self):
        agent_id = await self.launch()
        request = asyncio.create_task(
            self.engine.ask(
                agent_id, "approval", "Permission", {"choices": ["allow", "deny"]}
            )
        )
        await until(lambda: bool(self.engine.requests))
        entry = self.engine.store.inbox()[0]
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertFalse(self.engine.store.inbox())
        with self.assertRaises(ValueError):
            self.engine.resolve(entry["id"], {"decision": "allow"})

    async def test_child_messages_route_through_parent_and_unsupported_interrupt_rejected(
        self,
    ):
        parent = await self.launch()
        child = self.engine.codex.add_thread(
            {"id": "child-native", "parentThreadId": "fixture-1", "name": "Tests"}
        )
        await self.engine.send(child, "Check the edge case")
        steer = next(
            message
            for message in self.messages()
            if message.get("method") == "turn/steer"
        )
        self.assertEqual(
            steer["params"]["threadId"], self.engine.store.agent(parent)["native_id"]
        )
        self.assertIn("subagent Tests", steer["params"]["input"][0]["text"])
        self.engine.store.update(child, can_interrupt=False)
        with self.assertRaises(ValueError):
            await self.engine.interrupt(child)

    async def test_disconnect_expires_requests_and_keeps_history(self):
        agent_id = await self.launch("need approval")
        await until(lambda: bool(self.engine.requests))
        self.engine.codex.rpc.process.terminate()
        await until(
            lambda: self.engine.store.agent(agent_id)["status"] == "disconnected"
        )
        self.assertFalse(
            any(item["status"] == "pending" for item in self.engine.store.inbox())
        )
        self.assertTrue(self.engine.store.events(agent_id))

    async def test_worktree_starts_at_head_and_preserves_dirty_project(self):
        (self.root / "code.txt").write_text("uncommitted user work\n")
        result = await self.engine.launch(
            "codex", "Isolated task", "task", isolated=True
        )
        agent_id = result["agent_id"]
        await until(
            lambda: self.engine.store.agent(agent_id)["status"] in {"working", "failed"}
        )
        agent = self.engine.store.agent(agent_id)
        self.assertEqual(agent["status"], "working", agent["detail"])
        self.assertEqual((Path(agent["cwd"]) / "code.txt").read_text(), "original\n")
        self.assertEqual(
            (self.root / "code.txt").read_text(), "uncommitted user work\n"
        )
        self.assertNotEqual(agent["cwd"], str(self.root))

    async def test_task_diff_uses_initial_working_files(self):
        (self.root / "code.txt").write_text("existing\n")
        agent_id = await self.launch()
        (self.root / "code.txt").write_text("existing\nnew\n")
        result = await self.engine.changes(agent_id)
        self.assertIn("+new", result["diff"])
        self.assertNotIn("+existing", result["diff"])
        self.assertIn("all writers", result["scope_label"])


class ClaudeAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ResultMessage,
                SystemMessage,
                TextBlock,
            )
        except ImportError:
            self.skipTest("Install scripts/requirements-workspace.txt for SDK checks")
        self.messages = (AssistantMessage, ResultMessage, SystemMessage, TextBlock)
        self.temporary = tempfile.TemporaryDirectory(prefix="noir-claude-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        repository(self.root)
        self.clients = []

    async def asyncSetUp(self):
        owner = self
        assistant, result, system, text = self.messages

        class FakeClient:
            def __init__(self, session):
                self.session = session
                self.queue = asyncio.Queue()
                self.sent = []
                self.closed = False
                self.interrupted = False
                owner.clients.append(self)

            async def connect(self):
                await self.queue.put(
                    system(
                        "init",
                        {"session_id": "claude-fixture", "model": "fixture-model"},
                    )
                )

            async def query(self, prompt):
                self.sent.append(prompt)
                await self.queue.put(
                    assistant(
                        [text("Claude fixture output")],
                        "fixture-model",
                        message_id=f"m-{len(self.sent)}",
                    )
                )

            async def receive_messages(self):
                while True:
                    yield await self.queue.get()

            async def interrupt(self):
                self.interrupted = True
                await self.queue.put(
                    result("success", 1, 1, False, 1, "claude-fixture")
                )

            async def disconnect(self):
                self.closed = True

        self.engine = Engine(self.root, self.base / "state", claude_factory=FakeClient)
        self.addAsyncCleanup(self.engine.close)
        self.agent_id = (
            await self.engine.launch("claude", "Renderer", "Improve labels")
        )["agent_id"]
        await until(lambda: self.clients and self.clients[0].sent)
        self.client = self.clients[0]

    async def test_stream_followup_and_interrupt(self):
        await until(
            lambda: any(
                event["kind"] == "assistant"
                for event in self.engine.store.events(self.agent_id)
            )
        )
        await self.engine.send(self.agent_id, "Also check the cursor")
        self.assertEqual(self.client.sent, ["Improve labels", "Also check the cursor"])
        await self.engine.interrupt(self.agent_id)
        await until(
            lambda: self.engine.store.agent(self.agent_id)["status"] == "interrupted"
        )
        self.assertTrue(self.client.interrupted)

    async def test_hooks_register_subagents_and_errors(self):
        session = self.client.session
        await session.hook(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "child-1",
                "agent_type": "Explore",
            },
            None,
            {},
        )
        child = self.engine.store.agent(session.children["child-1"])
        self.assertEqual(child["parent"], self.agent_id)
        self.assertFalse(child["can_interrupt"])
        await session.hook(
            {
                "hook_event_name": "PostToolUseFailure",
                "agent_id": "child-1",
                "tool_name": "Bash",
                "tool_input": {"command": "fixture-test"},
                "error": "Fixture failure",
            },
            "tool-1",
            {},
        )
        self.assertEqual(self.engine.store.inbox()[0]["agent"], child["id"])
        await session.hook(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "child-1",
                "last_assistant_message": "Reviewed",
            },
            None,
            {},
        )
        self.assertEqual(self.engine.store.agent(child["id"])["status"], "ready")

    async def test_claude_permission_preserves_input_and_uses_one_time_decision(self):
        from types import SimpleNamespace

        session = self.client.session
        tool_input = {"command": "printf 'fixture'", "timeout": 1000}
        task = asyncio.create_task(
            session.permission("Bash", tool_input, SimpleNamespace(tool_use_id="cmd"))
        )
        await until(lambda: bool(self.engine.requests))
        entry = self.engine.store.inbox()[0]
        self.assertFalse(task.done())
        self.engine.resolve(entry["id"], {"decision": "allow"})
        decision = await task
        self.assertEqual(decision.behavior, "allow")
        self.assertEqual(decision.updated_input, tool_input)
        self.assertIsNone(decision.updated_permissions)

    async def test_claude_question_maps_answer_to_original_question(self):
        from types import SimpleNamespace

        task = asyncio.create_task(
            self.client.session.permission(
                "AskUserQuestion",
                {
                    "questions": [
                        {
                            "question": "Compact or wide?",
                            "options": [{"label": "Compact"}],
                        }
                    ]
                },
                SimpleNamespace(tool_use_id="ask"),
            )
        )
        await until(lambda: bool(self.engine.requests))
        entry = self.engine.store.inbox()[0]
        self.engine.resolve(entry["id"], {"answers": {"q0": "Compact"}})
        result = await task
        self.assertEqual(
            result.updated_input["answers"], {"Compact or wide?": "Compact"}
        )

    async def test_native_task_subclasses_and_terminal_patch_update_child_status(self):
        from claude_agent_sdk import TaskStartedMessage, TaskUpdatedMessage

        session = self.client.session
        session.handle(
            TaskStartedMessage(
                "task_started",
                {
                    "task_id": "task-1",
                    "description": "Review",
                    "task_type": "local_agent",
                    "tool_use_id": "tool-1",
                },
                task_id="task-1",
                description="Review",
                uuid="event-1",
                session_id="fixture",
            )
        )
        child_id = session.children["task-1"]
        self.assertEqual(self.engine.store.agent(child_id)["status"], "working")
        session.handle(
            TaskUpdatedMessage(
                "task_updated",
                {"task_id": "task-1", "patch": {"status": "killed"}},
                task_id="task-1",
                patch={"status": "killed"},
            )
        )
        self.assertEqual(self.engine.store.agent(child_id)["status"], "interrupted")
        session.handle(
            TaskStartedMessage(
                "task_started",
                {"task_id": "shell-1", "task_type": "local_bash"},
                task_id="shell-1",
                description="Background command",
                uuid="event-2",
                session_id="fixture",
            )
        )
        self.assertNotIn("shell-1", session.children)

    async def test_queued_turn_stream_restores_working_status(self):
        from claude_agent_sdk import StreamEvent

        self.engine.store.update(self.agent_id, status="ready")
        self.client.session.handle(
            StreamEvent(
                "event-1",
                "fixture",
                {"type": "message_start", "message": {"id": "next-turn"}},
            )
        )
        self.assertEqual(self.engine.store.agent(self.agent_id)["status"], "working")


class DaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_clients_detach_without_stopping_agents_and_restart_keeps_history(
        self,
    ):
        from noir_workspace.daemon import serve

        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        with tempfile.TemporaryDirectory(prefix="noir-daemon-") as directory:
            root = Path(directory)
            project = root / "project"
            repository(project)
            state = root / "state"
            socket = socket_path(state)
            protocol_log = root / "protocol.jsonl"

            def make_engine(project, directory):
                return Engine(
                    project,
                    directory,
                    codex_command=[sys.executable, str(FIXTURE), str(protocol_log)],
                )

            with patch("noir_workspace.daemon.Engine", side_effect=make_engine):
                running = asyncio.create_task(serve(project, state))
                try:
                    await until(socket.exists)
                    agent_id = (
                        await local_call(
                            socket,
                            "launch",
                            {
                                "provider": "codex",
                                "title": "Durable task",
                                "prompt": "task",
                            },
                        )
                    )["agent_id"]
                    async with asyncio.timeout(5):
                        while True:
                            snapshot = await local_call(
                                socket, "snapshot", {"selected": agent_id}
                            )
                            if (
                                snapshot["agents"][0]["status"] == "working"
                                and snapshot["events"]
                            ):
                                break
                            await asyncio.sleep(0.02)
                    self.assertEqual(stat.S_IMODE(socket.stat().st_mode), 0o600)
                    # Every call above detached its client; the provider remains live.
                    await local_call(
                        socket,
                        "send",
                        {"agent_id": agent_id, "prompt": "Follow up after detach"},
                    )
                    with self.assertRaises(RpcError):
                        await local_call(socket, "shutdown")
                    await local_call(socket, "shutdown", {"interrupt": True})
                    await asyncio.wait_for(running, 5)
                    self.assertFalse(socket.exists())
                    running = asyncio.create_task(serve(project, state))
                    await until(socket.exists)
                    restored = await local_call(
                        socket, "snapshot", {"selected": agent_id}
                    )
                    self.assertEqual(restored["agents"][0]["id"], agent_id)
                    self.assertTrue(
                        any(
                            event["message"] == "Follow up after detach"
                            for event in restored["events"]
                        )
                    )
                    await local_call(socket, "shutdown", {"interrupt": True})
                    await asyncio.wait_for(running, 5)
                finally:
                    if not running.done():
                        running.cancel()
                        await asyncio.gather(running, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
