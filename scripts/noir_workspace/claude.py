"""Claude's official SDK connection, using the user's installed Claude CLI."""

import asyncio
import json
import logging
import shutil

from .store import ACTIVE, clean

logger = logging.getLogger(__name__)


class ClaudeSession:
    def __init__(self, engine, agent_id, client_factory=None):
        self.engine = engine
        self.agent_id = agent_id
        self.client_factory = client_factory
        self.client = None
        self.connected = False
        self.interrupt_requested = False
        self.children = {}
        self.tool_agents = {}
        self.stream_ids = {}
        self.failed_tools = set()

    def make_client(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

        agent = self.engine.store.agent(self.agent_id)
        executable = shutil.which("claude")
        if not executable:
            raise ValueError(
                "Claude Code is not installed. Install and sign in with claude first."
            )
        options = ClaudeAgentOptions(
            cli_path=executable,
            cwd=agent["cwd"],
            model=agent.get("model"),
            resume=agent.get("native_id"),
            setting_sources=["user", "project", "local"],
            system_prompt={"type": "preset", "preset": "claude_code"},
            can_use_tool=self.permission,
            include_partial_messages=True,
            forward_subagent_text=True,
            hooks={
                event: [HookMatcher(hooks=[self.hook])]
                for event in (
                    "PreToolUse",
                    "PostToolUse",
                    "PostToolUseFailure",
                    "SubagentStart",
                    "SubagentStop",
                    "Notification",
                )
            },
            stderr=lambda line: self.engine.store.event(
                self.agent_id, "diagnostic", line, key="sdk-stderr", append=True
            ),
        )
        return ClaudeSDKClient(options=options)

    async def run(self, prompt):
        store = self.engine.store
        try:
            self.client = (
                self.client_factory(self) if self.client_factory else self.make_client()
            )
            await self.client.connect()
            self.connected = True
            await self.send(prompt)
            async for message in self.client.receive_messages():
                self.handle(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Claude connection failed")
            store.update(
                self.agent_id, status="disconnected", detail=clean(str(error), 500)
            )
            store.attention(
                self.agent_id,
                "error",
                "Claude connection failed",
                {"detail": clean(error)},
            )
        finally:
            self.connected = False
            self.engine.expire_requests(self.agent_id)
            for child_id in self.children.values():
                self.engine.expire_requests(child_id)
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    logger.exception("Claude disconnect failed")
            if store.agent(self.agent_id)["status"] in ACTIVE:
                store.update(
                    self.agent_id,
                    status="disconnected",
                    detail="Claude connection closed",
                )

    async def send(self, prompt):
        if not self.connected:
            raise ValueError("Claude is still connecting")
        store = self.engine.store
        active = store.agent(self.agent_id)["status"] in {"working", "waiting"}
        await self.client.query(prompt)
        store.event(self.agent_id, "user", prompt)
        if not active:
            self.interrupt_requested = False
            store.update(self.agent_id, status="working", detail="Working")
        return "Message queued by Claude" if active else "Turn started"

    async def interrupt(self):
        if not self.connected:
            raise ValueError("Claude is not connected")
        self.interrupt_requested = True
        await self.client.interrupt()
        agent = self.engine.store.agent(self.agent_id)
        if agent["status"] in ACTIVE:
            self.engine.store.update(
                self.agent_id, status="interrupting", detail="Interrupt requested"
            )

    def child(self, native_id, title="Subagent", tool_id=None, parent_native=None):
        if not native_id:
            return self.agent_id
        if native_id not in self.children:
            root = self.engine.store.agent(self.agent_id)
            agent = self.engine.store.create_agent("claude", title, root["cwd"])
            self.children[native_id] = agent["id"]
            self.engine.store.update(
                agent["id"],
                native_id=native_id,
                parent=self.children.get(parent_native, self.agent_id),
                status="working",
                detail="Subagent working",
                can_message=False,
                can_interrupt=False,
                baseline_agent=self.agent_id,
                isolated=root.get("isolated", False),
            )
        child_id = self.children[native_id]
        if tool_id:
            self.tool_agents[tool_id] = child_id
        return child_id

    def tool_failure(self, agent_id, tool_id, title, detail):
        if tool_id and tool_id in self.failed_tools:
            return
        if tool_id:
            self.failed_tools.add(tool_id)
        self.engine.store.attention(agent_id, "error", title, {"detail": clean(detail)})

    async def hook(self, payload, tool_id, _context):
        store = self.engine.store
        event = payload.get("hook_event_name")
        native = payload.get("agent_id")
        agent_id = self.children.get(native, self.agent_id)
        if event == "SubagentStart":
            self.child(
                native,
                payload.get("agent_type", "Subagent"),
                tool_id,
                payload.get("parent_agent_id"),
            )
        elif event == "SubagentStop":
            child_id = self.children.get(native)
            if child_id:
                self.engine.expire_requests(child_id)
                store.update(child_id, status="ready", detail="Subagent finished")
                if payload.get("last_assistant_message"):
                    store.event(
                        child_id,
                        "assistant",
                        payload["last_assistant_message"],
                        key="subagent-result",
                    )
        elif event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
            effective_tool_id = payload.get("tool_use_id") or tool_id
            if effective_tool_id:
                self.tool_agents[effective_tool_id] = agent_id
            tool = payload.get("tool_name", "Tool")
            args = payload.get("tool_input") or {}
            detail = (
                args.get("command") or args.get("file_path") or args.get("path") or tool
            )
            kind = (
                "command"
                if tool == "Bash"
                else "edit"
                if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}
                else "tool"
            )
            data = {
                "tool": tool,
                "input": args,
                "detail": clean(
                    json.dumps(
                        payload.get("tool_response") or payload.get("error") or "",
                        ensure_ascii=False,
                    )
                ),
                "status": "running"
                if event == "PreToolUse"
                else "failed"
                if event == "PostToolUseFailure"
                else "completed",
            }
            store.event(
                agent_id,
                kind,
                detail,
                data,
                key=f"hook:{payload.get('tool_use_id') or tool_id or tool}",
            )
            if store.agent(agent_id)["status"] != "waiting":
                store.update(agent_id, status="working", detail=clean(detail, 160))
            if event == "PostToolUseFailure":
                self.tool_failure(
                    agent_id,
                    effective_tool_id,
                    f"{tool} failed",
                    payload.get("error") or detail,
                )
        elif event == "Notification":
            store.attention(
                agent_id,
                "notification",
                payload.get("message", "Claude needs attention"),
            )
        return {}

    async def permission(self, tool, args, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        agent_id = self.tool_agents.get(
            getattr(context, "tool_use_id", None), self.agent_id
        )
        if tool == "AskUserQuestion":
            questions = []
            for index, question in enumerate(args.get("questions", [])):
                questions.append({**question, "id": f"q{index}"})
            answer = await self.engine.ask(
                agent_id,
                "question",
                "Claude needs your input",
                {"questions": questions},
            )
            answers = {}
            for question in questions:
                value = answer.get("answers", {}).get(question["id"], "")
                answers[question["question"]] = (
                    ", ".join(value) if isinstance(value, list) else value
                )
            return PermissionResultAllow(updated_input={**args, "answers": answers})
        detail = args.get("command") or json.dumps(args, indent=2, ensure_ascii=False)
        answer = await self.engine.ask(
            agent_id,
            "approval",
            f"Claude requests {tool}",
            {"detail": clean(detail), "choices": ["allow", "deny"]},
        )
        if answer.get("decision") == "allow":
            return PermissionResultAllow(updated_input=args)
        return PermissionResultDeny(message="Declined by the user in Noir")

    def handle(self, message):
        from claude_agent_sdk import SystemMessage

        store = self.engine.store
        kind = type(message).__name__
        parent_tool = getattr(message, "parent_tool_use_id", None)
        agent_id = self.tool_agents.get(parent_tool, self.agent_id)
        if isinstance(message, SystemMessage):
            data = message.data
            if message.subtype == "init":
                store.update(
                    self.agent_id,
                    native_id=data.get("session_id"),
                    model=data.get("model"),
                    status="working",
                )
            elif message.subtype == "task_started":
                task_type = data.get("task_type")
                if task_type in {"local_bash", "remote_bash"}:
                    store.event(
                        self.agent_id,
                        "task",
                        data.get("description") or "Background command started",
                    )
                else:
                    child_id = self.child(
                        data.get("agent_id") or data.get("task_id"),
                        data.get("description") or "Subagent",
                        data.get("tool_use_id"),
                    )
                    if data.get("task_id"):
                        self.children[data["task_id"]] = child_id
            elif message.subtype in {
                "task_notification",
                "task_progress",
                "task_updated",
            }:
                if message.subtype == "task_updated":
                    data = {**data, **(data.get("patch") or {})}
                child_id = self.children.get(data.get("task_id"))
                if child_id:
                    status = {
                        "completed": "ready",
                        "failed": "failed",
                        "stopped": "interrupted",
                        "killed": "interrupted",
                        "paused": "paused",
                    }.get(data.get("status"), "working")
                    if status in {"ready", "failed", "interrupted"}:
                        self.engine.expire_requests(child_id)
                    store.update(
                        child_id,
                        status=status,
                        detail=clean(
                            data.get("summary") or data.get("description") or status,
                            160,
                        ),
                    )
                    if data.get("summary"):
                        store.event(child_id, "status", data["summary"])
                    if status == "failed":
                        store.attention(
                            child_id,
                            "error",
                            "Subagent failed",
                            {
                                "detail": clean(
                                    data.get("summary") or data.get("error") or ""
                                )
                            },
                        )
        elif kind == "StreamEvent":
            event = message.event
            if event.get("type") == "message_start":
                self.stream_ids[parent_tool] = event.get("message", {}).get(
                    "id", message.uuid
                )
                if store.agent(agent_id)["status"] not in {"waiting", "interrupting"}:
                    store.update(agent_id, status="working", detail="Working")
            elif (
                event.get("type") == "content_block_delta"
                and event.get("delta", {}).get("type") == "text_delta"
            ):
                stream_id = self.stream_ids.get(parent_tool, message.uuid)
                store.event(
                    agent_id,
                    "assistant",
                    event["delta"].get("text", ""),
                    key=f"text:{stream_id}",
                    append=True,
                )
        elif kind == "AssistantMessage":
            message_id = getattr(message, "message_id", None) or self.stream_ids.get(
                parent_tool
            )
            text = "\n".join(
                block.text
                for block in message.content
                if type(block).__name__ == "TextBlock"
            )
            if text:
                store.event(
                    agent_id,
                    "assistant",
                    text,
                    key=f"text:{message_id}" if message_id else None,
                )
            if message.model and agent_id == self.agent_id:
                store.update(agent_id, model=message.model)
            if message.error:
                store.attention(agent_id, "error", f"Claude: {message.error}")
        elif kind == "UserMessage" and isinstance(message.content, list):
            for block in message.content:
                if type(block).__name__ == "ToolResultBlock" and block.is_error:
                    owner = self.tool_agents.get(block.tool_use_id, agent_id)
                    detail = (
                        block.content
                        if isinstance(block.content, str)
                        else json.dumps(block.content)
                    )
                    self.tool_failure(
                        owner, block.tool_use_id, "Claude tool failed", detail
                    )
        elif kind == "ResultMessage":
            self.engine.expire_requests(self.agent_id)
            status = (
                "interrupted"
                if self.interrupt_requested
                else "failed"
                if message.is_error
                else "ready"
            )
            detail = "Turn finished" if status == "ready" else status.capitalize()
            store.update(
                self.agent_id,
                native_id=message.session_id,
                status=status,
                detail=detail,
            )
            store.event(self.agent_id, "status", detail)
            if message.is_error and not self.interrupt_requested:
                store.attention(
                    self.agent_id,
                    "error",
                    "Claude could not complete the turn",
                    {
                        "detail": clean(
                            "\n".join(getattr(message, "errors", None) or [])
                            or message.result
                            or message.subtype
                        )
                    },
                )
            elif status == "ready":
                store.attention(
                    self.agent_id,
                    "complete",
                    "Turn finished; changes are ready to inspect",
                )
            self.interrupt_requested = False
