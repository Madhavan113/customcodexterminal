"""Codex App Server adapter. No simulated keystrokes or credential copies."""

import asyncio
import contextlib
import json
import shutil

from .rpc import ProcessRpc, RpcError
from .store import ACTIVE, clean


def nested_parent(value):
    if isinstance(value, dict):
        for key in ("parentThreadId", "parent_thread_id"):
            if value.get(key):
                return value[key]
        for child in value.values():
            if found := nested_parent(child):
                return found
    return None


class CodexHub:
    def __init__(self, engine, command=None):
        self.engine = engine
        self.command = command
        self.rpc = None
        self.lock = asyncio.Lock()
        self.agents = {}
        self.subscribed = set()
        self.finished_turns = {}
        self.discovery = None

    async def connect(self):
        async with self.lock:
            if self.rpc and not self.rpc.closed:
                return
            command = self.command
            if command is None:
                binary = shutil.which("codex-noir") or shutil.which("codex")
                if not binary:
                    raise ValueError(
                        "Codex is not installed. Install and sign in with codex first."
                    )
                command = [binary, "app-server", "--listen", "stdio://"]
            self.rpc = ProcessRpc(self.on_event, self.on_request, self.disconnected)
            await self.rpc.start(command, self.engine.project)
            try:
                await self.rpc.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "noir_terminal",
                            "title": "Noir",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await self.rpc.write({"method": "initialized"})
            except BaseException:
                await self.rpc.close()
                raise
            if self.discovery:
                self.discovery.cancel()
            self.discovery = asyncio.create_task(self.discover())

    async def start(self, agent, prompt):
        await self.connect()
        params = {"cwd": agent["cwd"]}
        if agent.get("model"):
            params["model"] = agent["model"]
        if agent.get("native_id"):
            params.update(threadId=agent["native_id"], excludeTurns=True)
            result = await self.rpc.request("thread/resume", params)
        else:
            result = await self.rpc.request("thread/start", params)
        native = result["thread"]["id"]
        self.agents[native] = agent["id"]
        self.subscribed.add(native)
        self.engine.store.update(
            agent["id"],
            native_id=native,
            model=result.get("model", agent.get("model")),
            status="ready",
            turn_id=None,
        )
        await self.send(self.engine.store.agent(agent["id"]), prompt)

    async def send(self, agent, prompt):
        if not self.rpc or self.rpc.closed or agent["native_id"] not in self.agents:
            return await self.start(agent, prompt)
        params = {
            "threadId": agent["native_id"],
            "input": [{"type": "text", "text": prompt}],
        }
        if agent.get("turn_id") and agent["status"] in ACTIVE:
            params["expectedTurnId"] = agent["turn_id"]
            await self.rpc.request("turn/steer", params)
            self.engine.store.event(agent["id"], "user", prompt)
            return "Instruction delivered to the active turn"
        result = await self.rpc.request("turn/start", params)
        turn = result["turn"]
        current = self.engine.store.agent(agent["id"])
        if (
            self.finished_turns.get(agent["native_id"]) != turn["id"]
            and current.get("turn_id") != turn["id"]
        ):
            self.engine.store.update(
                agent["id"],
                status="starting",
                turn_id=turn["id"],
                detail="Starting turn",
            )
        self.engine.store.event(agent["id"], "user", prompt)
        return "Turn started"

    async def interrupt(self, agent):
        if not self.rpc or self.rpc.closed or not agent.get("turn_id"):
            raise ValueError("This agent has no connected active turn")
        await self.rpc.request(
            "turn/interrupt",
            {"threadId": agent["native_id"], "turnId": agent["turn_id"]},
        )
        # The completion event, not the request acknowledgement, ends the turn.
        current = self.engine.store.agent(agent["id"])
        if current.get("turn_id"):
            self.engine.store.update(
                agent["id"], status="interrupting", detail="Interrupt requested"
            )

    def add_thread(self, thread):
        native = thread["id"]
        if native in self.agents:
            return self.agents[native]
        parent_native = thread.get("parentThreadId") or nested_parent(
            thread.get("source")
        )
        parent_id = self.agents.get(parent_native)
        if not parent_id:
            return None
        parent = self.engine.store.agent(parent_id)
        child = self.engine.store.create_agent(
            "codex",
            thread.get("agentNickname") or thread.get("name") or "Subagent",
            thread.get("cwd") or parent["cwd"],
        )
        self.engine.store.update(
            child["id"],
            parent=parent_id,
            native_id=native,
            can_message=False,
            can_interrupt=True,
            isolated=parent.get("isolated", False),
            baseline_agent=parent.get("baseline_agent", parent_id),
            detail="Subagent connected",
            status="working",
        )
        self.agents[native] = child["id"]
        return child["id"]

    async def discover(self):
        while self.rpc and not self.rpc.closed:
            await asyncio.sleep(3)
            for agent in self.engine.store.agents():
                if (
                    agent["provider"] != "codex"
                    or agent["parent"]
                    or agent.get("native_id") not in self.agents
                ):
                    continue
                try:
                    result = await self.rpc.request(
                        "thread/list",
                        {"ancestorThreadId": agent["native_id"], "limit": 100},
                        timeout=10,
                    )
                    remaining = list(result.get("data", []))
                    for _ in range(len(remaining)):
                        progress = False
                        for thread in remaining[:]:
                            child_id = self.add_thread(thread)
                            if not child_id:
                                continue
                            remaining.remove(thread)
                            progress = True
                            if thread["id"] not in self.subscribed:
                                resumed = await self.rpc.request(
                                    "thread/resume",
                                    {"threadId": thread["id"]},
                                )
                                self.subscribed.add(thread["id"])
                                self.sync_thread(
                                    child_id, resumed.get("thread", thread)
                                )
                            elif (
                                self.engine.store.agent(child_id)["status"] != "waiting"
                            ):
                                self.sync_thread(child_id, thread)
                        if not progress:
                            break
                except (RpcError, KeyError):
                    # Older versions can omit experimental descendant listing.
                    continue

    def sync_thread(self, agent_id, thread):
        status = thread.get("status", {})
        kind = status.get("type") if isinstance(status, dict) else status
        mapped = {
            "active": "working",
            "idle": "ready",
            "notLoaded": "disconnected",
            "systemError": "failed",
        }.get(kind)
        fields = {}
        if mapped:
            fields["status"] = mapped
        if thread.get("canAcceptDirectInput") is not None:
            fields["can_message"] = thread["canAcceptDirectInput"]
        for turn in thread.get("turns", []):
            if turn.get("status") == "inProgress":
                fields["turn_id"] = turn["id"]
        if fields:
            self.engine.store.update(agent_id, **fields)

    def on_event(self, method, params):
        store = self.engine.store
        if method == "thread/started":
            thread = params.get("thread", {})
            if thread.get("id"):
                self.add_thread(thread)
            return
        agent_id = self.agents.get(params.get("threadId"))
        if not agent_id:
            return
        if method == "turn/started":
            self.engine.expire_requests(agent_id)
            store.update(
                agent_id,
                status="working",
                turn_id=params["turn"]["id"],
                detail="Working",
            )
        elif method == "turn/completed":
            turn = params["turn"]
            self.finished_turns[params["threadId"]] = turn["id"]
            status = {
                "completed": "ready",
                "interrupted": "interrupted",
                "failed": "failed",
            }.get(turn.get("status"), "ready")
            detail = clean(
                (turn.get("error") or {}).get("message")
                or ("Turn finished" if status == "ready" else status.capitalize()),
                500,
            )
            self.engine.expire_requests(agent_id)
            store.update(agent_id, status=status, turn_id=None, detail=detail)
            store.event(agent_id, "status", detail)
            if status != "interrupted":
                store.attention(
                    agent_id, "complete" if status == "ready" else "error", detail
                )
        elif method == "thread/status/changed":
            current = store.agent(agent_id)
            # Pending prompts are more specific than the backend's active status.
            if current["status"] != "waiting":
                self.sync_thread(agent_id, {"status": params.get("status")})
        elif method == "item/agentMessage/delta":
            store.event(
                agent_id,
                "assistant",
                params.get("delta", ""),
                key=f"text:{params.get('itemId')}",
                append=True,
            )
        elif method in {"item/started", "item/completed"}:
            item = params.get("item", {})
            kind, item_id = item.get("type"), item.get("id", "")
            completed = method == "item/completed"
            if kind == "agentMessage" and completed:
                store.event(
                    agent_id, "assistant", item.get("text", ""), key=f"text:{item_id}"
                )
            elif kind == "commandExecution":
                command = item.get("command", "Command")
                store.event(
                    agent_id,
                    "command",
                    command,
                    {
                        "output": clean(item.get("aggregatedOutput") or ""),
                        "exit_code": item.get("exitCode"),
                        "status": item.get("status"),
                    },
                    key=f"command:{item_id}",
                )
                store.update(agent_id, detail=clean(command, 160))
                if completed and item.get("exitCode") not in (None, 0):
                    store.attention(
                        agent_id,
                        "error",
                        f"Command exited {item['exitCode']}",
                        {
                            "detail": clean(
                                command + "\n" + (item.get("aggregatedOutput") or "")
                            )
                        },
                    )
            elif kind == "fileChange":
                changes = item.get("changes", [])
                paths = [change.get("path", "") for change in changes]
                store.event(
                    agent_id,
                    "edit",
                    "\n".join(paths),
                    {"changes": changes, "status": item.get("status")},
                    key=f"edit:{item_id}",
                )
                store.update(agent_id, detail=clean("Editing " + ", ".join(paths), 160))
            elif kind in {
                "mcpToolCall",
                "dynamicToolCall",
                "webSearch",
                "collabAgentToolCall",
            }:
                store.event(
                    agent_id,
                    "tool",
                    item.get("tool", kind),
                    {"detail": clean(json.dumps(item, ensure_ascii=False))},
                    key=f"tool:{item_id}",
                )
        elif method == "item/commandExecution/outputDelta":
            store.event(
                agent_id,
                "output",
                params.get("delta", ""),
                key=f"output:{params.get('itemId')}",
                append=True,
            )
        elif method == "turn/diff/updated":
            store.event(
                agent_id,
                "diff",
                params.get("diff", ""),
                key=f"diff:{params.get('turnId')}",
            )
        elif method == "error":
            detail = (params.get("error") or {}).get("message", "Codex error")
            store.event(agent_id, "error", detail)
            store.attention(agent_id, "error", detail)

    async def on_request(self, method, params):
        agent_id = self.agents.get(params.get("threadId"))
        if not agent_id:
            raise RpcError("This request does not belong to a connected Noir agent")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            title = params.get("reason") or (
                "Approve command"
                if "commandExecution" in method
                else "Approve file changes"
            )
            detail = params.get("command") or json.dumps(
                params, indent=2, ensure_ascii=False
            )
            answer = await self.engine.ask(
                agent_id,
                "approval",
                title,
                {"detail": clean(detail), "choices": ["allow", "deny"]},
            )
            return {
                "decision": "accept" if answer.get("decision") == "allow" else "decline"
            }
        if method == "item/tool/requestUserInput":
            answer = await self.engine.ask(
                agent_id,
                "question",
                "Agent needs your input",
                {"questions": params.get("questions", [])},
            )
            return {
                "answers": {
                    key: {"answers": value if isinstance(value, list) else [value]}
                    for key, value in answer.get("answers", {}).items()
                }
            }
        if method == "item/permissions/requestApproval":
            answer = await self.engine.ask(
                agent_id,
                "approval",
                params.get("reason") or "Approve additional permissions for this turn",
                {
                    "detail": json.dumps(params.get("permissions", {}), indent=2),
                    "choices": ["allow", "deny"],
                },
            )
            return {
                "permissions": params.get("permissions", {})
                if answer.get("decision") == "allow"
                else {},
                "scope": "turn",
            }
        self.engine.store.attention(
            agent_id, "error", "Unsupported Codex request", {"detail": method}
        )
        raise RpcError(f"Noir does not support {method}; request was not approved")

    def disconnected(self, reason):
        for agent_id in set(self.agents.values()):
            self.engine.expire_requests(agent_id)
            agent = self.engine.store.agent(agent_id)
            if agent["status"] in ACTIVE:
                self.engine.store.update(
                    agent_id,
                    status="disconnected",
                    turn_id=None,
                    detail=clean(reason, 500),
                )
                self.engine.store.attention(
                    agent_id, "error", "Codex disconnected", {"detail": clean(reason)}
                )
        self.agents.clear()
        self.subscribed.clear()
        self.finished_turns.clear()

    async def close(self):
        if self.discovery:
            self.discovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.discovery
        if self.rpc:
            await self.rpc.close()
