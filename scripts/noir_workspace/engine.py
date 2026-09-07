"""Agent ownership, requests and task baselines shared by every terminal client."""

import asyncio
import contextlib
import logging
import re
import time
from pathlib import Path

from .claude import ClaudeSession
from .codex import CodexHub
from .git_view import GitView, git
from .store import Store, clean

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, project, directory, codex_command=None, claude_factory=None):
        self.project = Path(project)
        self.store = Store(directory)
        self.store.recover()
        self.git = GitView(directory)
        self.codex = CodexHub(self, command=codex_command)
        self.claude_factory = claude_factory
        self.claude = {}
        self.jobs = {}
        self.requests = {}
        self.changes_cache = {}
        self.git_lock = asyncio.Lock()
        self.agent_locks = {}

    def job(self, agent_id, coroutine):
        async def run():
            try:
                await coroutine
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Agent launch failed")
                current = self.store.agent(agent_id)
                if not current.get("turn_id"):
                    self.store.update(
                        agent_id, status="failed", detail=clean(error, 500)
                    )
                self.store.event(agent_id, "error", str(error))
                self.store.attention(
                    agent_id, "error", "Agent could not start", {"detail": clean(error)}
                )

        task = asyncio.create_task(run())
        self.jobs[agent_id] = task
        return task

    async def launch(self, provider, title, prompt, model=None, isolated=False):
        if provider not in {"codex", "claude"}:
            raise ValueError("Choose Codex or Claude")
        prompt = self.validate_prompt(prompt)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Give the agent a task name")
        if model is not None and (not isinstance(model, str) or len(model) > 200):
            raise ValueError("Invalid model name")
        agent = self.store.create_agent(provider, title.strip(), self.project)
        self.store.update(agent["id"], model=model or None, isolated=bool(isolated))

        async def start():
            if isolated:
                slug = (
                    re.sub(r"[^a-z0-9-]+", "-", title.lower()).strip("-")[:32] or "task"
                )
                worktree = self.store.directory / "worktrees" / agent["id"]
                worktree.parent.mkdir(exist_ok=True, mode=0o700)
                async with self.git_lock:
                    await asyncio.to_thread(
                        git,
                        self.project,
                        "worktree",
                        "add",
                        "-b",
                        f"noir/{slug}-{agent['id'][:6]}",
                        "--",
                        str(worktree),
                        "HEAD",
                    )
                self.store.update(agent["id"], cwd=str(worktree))
            current = self.store.agent(agent["id"])
            self.store.update(agent["id"], detail="Capturing task baseline")
            async with self.git_lock:
                await asyncio.to_thread(self.git.capture, current["cwd"], current["id"])
            await self.start_backend(self.store.agent(agent["id"]), prompt)

        self.job(agent["id"], start())
        return {"agent_id": agent["id"]}

    async def start_backend(self, agent, prompt):
        if agent["provider"] == "codex":
            await self.codex.start(agent, prompt)
        else:
            session = ClaudeSession(self, agent["id"], self.claude_factory)
            self.claude[agent["id"]] = session
            await session.run(prompt)

    @staticmethod
    def validate_prompt(prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Write a message first")
        if len(prompt.encode()) > 64_000:
            raise ValueError("Message is larger than 64 KB")
        return prompt

    async def send(self, agent_id, prompt):
        prompt = self.validate_prompt(prompt)
        agent = self.store.agent(agent_id)
        if not agent.get("can_message", True):
            if not agent.get("parent"):
                raise ValueError("This agent cannot accept direct messages")
            prompt = f"For your subagent {agent['title']} ({agent.get('native_id') or agent_id}):\n{prompt}"
            agent_id = agent["parent"]
            agent = self.store.agent(agent_id)
            while not agent.get("can_message", True) and agent.get("parent"):
                agent_id = agent["parent"]
                agent = self.store.agent(agent_id)
        lock = self.agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = self.store.agent(agent_id)
            if agent["status"] == "starting":
                raise ValueError("Agent is still starting; the draft has been kept")
            if agent["status"] == "interrupting":
                raise ValueError(
                    "Wait for the interrupt to finish; the draft has been kept"
                )
            if agent["provider"] == "codex":
                result = await self.codex.send(agent, prompt)
            else:
                session = self.claude.get(agent_id)
                if not session or not session.connected:
                    if (task := self.jobs.get(agent_id)) and not task.done():
                        raise ValueError(
                            "Claude is reconnecting; the draft has been kept"
                        )
                    self.store.update(
                        agent_id, status="starting", detail="Resuming Claude"
                    )
                    self.job(
                        agent_id, self.start_backend(self.store.agent(agent_id), prompt)
                    )
                    return {
                        "message": "Resuming the saved Claude session",
                        "agent_id": agent_id,
                    }
                result = await session.send(prompt)
            return {"message": result or "Message sent", "agent_id": agent_id}

    async def interrupt(self, agent_id):
        agent = self.store.agent(agent_id)
        if not agent.get("can_interrupt", True):
            raise ValueError(
                "Interrupt the parent session to stop this Claude subagent"
            )
        if agent["status"] not in {"working", "waiting"}:
            raise ValueError("This agent has no active run to interrupt")
        if agent["provider"] == "codex":
            await self.codex.interrupt(agent)
        else:
            session = self.claude.get(agent_id)
            if not session:
                raise ValueError("Claude is not connected")
            await session.interrupt()
        self.store.event(agent_id, "control", "Interrupt requested by you")
        return {"message": "Interrupt requested"}

    async def ask(self, agent_id, kind, title, data):
        request_id = self.store.attention(agent_id, kind, title, data, pending=True)
        future = asyncio.get_running_loop().create_future()
        self.requests[request_id] = (agent_id, future)
        self.store.update(agent_id, status="waiting", detail=clean(title, 160))
        try:
            return await future
        finally:
            if future.cancelled():
                self.store.resolve(request_id, "expired")
            self.requests.pop(request_id, None)
            still_waiting = any(
                owner == agent_id for owner, _ in self.requests.values()
            )
            if not still_waiting and self.store.agent(agent_id)["status"] == "waiting":
                self.store.update(agent_id, status="working", detail="Working")

    def resolve(self, request_id, answer):
        entry = next(
            (item for item in self.store.inbox() if item["id"] == request_id), None
        )
        if entry is None:
            raise ValueError("This item was already handled or has expired")
        if entry["status"] == "unread":
            self.store.resolve(request_id, "read")
            return {"message": "Dismissed"}
        pending = self.requests.get(request_id)
        if not pending or pending[1].done():
            self.store.resolve(request_id, "expired")
            raise ValueError("This request is no longer active")
        if entry["kind"] == "approval":
            if answer.get("decision") not in entry["data"].get("choices", []):
                raise ValueError("Choose Allow once or Deny")
        elif entry["kind"] == "question":
            answers = answer.get("answers", {})
            if not isinstance(answers, dict):
                raise ValueError("Answers must be an object")
            for question in entry["data"].get("questions", []):
                value = answers.get(question.get("id"))
                if not isinstance(value, (str, list)) or not value:
                    raise ValueError("Answer every question before sending")
                if isinstance(value, list) and not all(
                    isinstance(item, str) for item in value
                ):
                    raise ValueError("Invalid answer")
        self.store.resolve(request_id, "resolved")
        pending[1].set_result(answer)
        self.store.event(
            entry["agent"],
            "control",
            "Permission allowed once"
            if answer.get("decision") == "allow"
            else "Permission denied"
            if answer.get("decision") == "deny"
            else "Question answered",
        )
        return {"message": "Response delivered"}

    def expire_requests(self, agent_id):
        for request_id, (owner, future) in list(self.requests.items()):
            if owner == agent_id:
                self.store.resolve(request_id, "expired")
                if not future.done():
                    future.cancel()

    async def changes(self, agent_id, scope="task", selected=None):
        agent = self.store.agent(agent_id)
        if scope not in {"task", "git"}:
            raise ValueError("Unknown changes view")
        key = (agent_id, scope, selected)
        cached = self.changes_cache.get(key)
        if cached and time.monotonic() - cached[0] < 1.5:
            return cached[1]
        baseline = agent.get("baseline_agent", agent_id)
        async with self.git_lock:
            result = await asyncio.to_thread(
                self.git.inspect, agent["cwd"], baseline, scope, selected
            )
        result["scope_label"] = (
            "Git: staged + unstaged + untracked"
            if scope == "git"
            else "Since agent started · separate worktree"
            if agent.get("isolated")
            else "Since agent started · shared folder, all writers"
        )
        self.changes_cache[key] = (time.monotonic(), result)
        if len(self.changes_cache) > 100:
            self.changes_cache = {key: self.changes_cache[key]}
        return result

    def snapshot(self, selected=None):
        agents = self.store.agents()
        if selected not in {agent["id"] for agent in agents}:
            selected = agents[0]["id"] if agents else None
        return {
            "project": str(self.project),
            "revision": self.store.revision,
            "agents": agents,
            "selected": selected,
            "events": self.store.events(selected) if selected else [],
            "inbox": self.store.inbox(),
        }

    async def close(self):
        # Cancel tools through their provider before closing the connection.
        for agent in self.store.agents():
            if agent["status"] in {"working", "waiting"} and agent.get(
                "can_interrupt", True
            ):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.interrupt(agent["id"]), 5)
        for agent_id, task in self.jobs.items():
            self.expire_requests(agent_id)
            task.cancel()
        await self.codex.close()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for _, future in list(self.requests.values()):
            future.cancel()
        with contextlib.suppress(Exception):
            self.store.close()
