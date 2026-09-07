"""Verify installed CLIs against local inert model responses, with no model API calls."""

import asyncio
import contextlib
import http.server
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from noir_workspace.engine import Engine

OUTPUT = Path(__file__).resolve().parent.parent / "output/workspace-verification"


class ModelFixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.requests.append(self.path)
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":10}')
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if "responses" in self.path:
                self.emit_responses()
            else:
                self.messages(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def emit(self, event):
        self.wfile.write(
            (
                "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
            ).encode()
        )
        self.wfile.flush()

    def emit_responses(self):
        response = {
            "id": f"resp_noir_{time.time_ns()}",
            "object": "response",
            "created_at": 0,
            "model": "noir-fixture",
            "status": "in_progress",
            "output": [],
        }
        self.emit({"type": "response.created", "response": response})
        if self.server.hold:
            self.server.release.wait(15)
        item = {
            "id": f"message_noir_{time.time_ns()}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": "Noir local fixture completed.",
                    "annotations": [],
                }
            ],
        }
        self.emit(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []},
            }
        )
        self.emit(
            {
                "type": "response.output_text.delta",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": "Noir local fixture completed.",
            }
        )
        self.emit(
            {"type": "response.output_item.done", "output_index": 0, "item": item}
        )
        self.emit(
            {
                "type": "response.completed",
                "response": {
                    **response,
                    "status": "completed",
                    "output": [item],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 8,
                        "total_tokens": 18,
                    },
                },
            }
        )

    def messages(self, body):
        self.emit(
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_noir_{time.time_ns()}",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": body.get("model", "claude-fixture"),
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            }
        )
        if self.server.hold:
            self.server.release.wait(15)
        if self.server.write_request:
            self.server.write_request = False
            self.emit(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_noir_fixture",
                        "name": "Write",
                        "input": {},
                    },
                }
            )
            self.emit(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(
                            {
                                "file_path": str(self.server.target),
                                "content": "fixture content\n",
                            }
                        ),
                    },
                }
            )
            stop_reason = "tool_use"
        else:
            self.emit(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            self.emit(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": "Noir local fixture completed.",
                    },
                }
            )
            stop_reason = "end_turn"
        self.emit({"type": "content_block_stop", "index": 0})
        self.emit(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 8},
            }
        )
        self.emit({"type": "message_stop"})


async def wait_for(predicate, timeout=40):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def verify():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="native-", dir=OUTPUT) as temporary,
        contextlib.ExitStack() as cleanup,
    ):
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
        server.requests = []
        server.hold = False
        server.release = threading.Event()
        server.write_request = False
        server.target = project / "permission-fixture.txt"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cleanup.callback(server.server_close)
        cleanup.callback(server.shutdown)
        cleanup.callback(server.release.set)
        endpoint = f"http://127.0.0.1:{server.server_port}"
        results = {}
        codex_state = root / "codex"
        codex_state.mkdir()
        (codex_state / "config.toml").write_text(
            'model = "noir-fixture"\nmodel_provider = "local_fixture"\ncheck_for_update_on_startup = false\n'
            '[model_providers.local_fixture]\nname = "Local fixture"\n'
            f'base_url = "{endpoint}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
            f'[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
        )
        engine = Engine(project, root / "codex-workspace")
        try:
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_state)}):
                launched = await engine.launch(
                    "codex", "Native fixture", "Return the fixture response."
                )
                agent_id = launched["agent_id"]
                await wait_for(
                    lambda: (
                        any(
                            event["kind"] == "status"
                            for event in engine.store.events(agent_id)
                        )
                        or engine.store.agent(agent_id)["status"]
                        in {"failed", "disconnected"}
                    )
                )
                status = engine.store.agent(agent_id)["status"]
                if status != "ready":
                    raise RuntimeError(
                        f"Codex native turn failed: {engine.store.agent(agent_id)['detail']}"
                    )
                if not any(
                    "Noir local fixture completed" in event["message"]
                    for event in engine.store.events(agent_id)
                ):
                    raise RuntimeError("Codex response did not reach the workspace")
                results["codex_stream"] = "passed"
                server.hold = True
                before = len(server.requests)
                await engine.send(agent_id, "Hold this fixture turn.")
                await wait_for(
                    lambda: (
                        len(server.requests) > before
                        and engine.store.agent(agent_id)["status"] == "working"
                    )
                )
                await engine.interrupt(agent_id)
                await wait_for(
                    lambda: (
                        engine.store.agent(agent_id)["status"]
                        in {"interrupted", "ready"}
                    )
                )
                if engine.store.agent(agent_id)["turn_id"] is not None:
                    raise RuntimeError("Codex interrupt left an active turn")
                results["codex_interrupt"] = "passed"
                server.hold = False
                server.release.set()
        finally:
            await engine.close()

        def claude_client(session):
            client = session.make_client()
            client.options.setting_sources = []
            client.options.strict_mcp_config = True
            client.options.permission_mode = "default"
            client.options.model = "sonnet"
            client.options.env = {
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "ANTHROPIC_API_KEY": "noir-local-fixture",
                "ANTHROPIC_BASE_URL": endpoint,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1",
            }
            return client

        engine = Engine(
            project, root / "claude-workspace", claude_factory=claude_client
        )
        try:
            launched = await engine.launch(
                "claude", "Native fixture", "Return the fixture response."
            )
            agent_id = launched["agent_id"]
            await wait_for(
                lambda: (
                    engine.store.agent(agent_id)["status"]
                    in {"ready", "failed", "disconnected"}
                )
            )
            if engine.store.agent(agent_id)["status"] != "ready":
                raise RuntimeError(
                    f"Claude native turn failed: {engine.store.agent(agent_id)['detail']}"
                )
            if not any(
                "Noir local fixture completed" in event["message"]
                for event in engine.store.events(agent_id)
            ):
                raise RuntimeError("Claude response did not reach the workspace")
            results["claude_stream"] = "passed"
            server.write_request = True
            await engine.send(agent_id, "Request the fixture file write.")
            await wait_for(lambda: bool(engine.requests))
            entry = next(
                item for item in engine.store.inbox() if item["status"] == "pending"
            )
            if entry["kind"] != "approval":
                raise RuntimeError("Expected a native Claude permission request")
            engine.resolve(entry["id"], {"decision": "deny"})
            await wait_for(lambda: engine.store.agent(agent_id)["status"] == "ready")
            if server.target.exists():
                raise RuntimeError("Declined fixture write was executed")
            results["claude_permission_denial"] = "passed"
            server.hold = True
            server.release.clear()
            await engine.send(agent_id, "Hold this fixture turn.")
            await asyncio.sleep(0.2)
            await engine.interrupt(agent_id)
            await wait_for(
                lambda: engine.store.agent(agent_id)["status"] == "interrupted"
            )
            results["claude_interrupt"] = "passed"
        finally:
            server.hold = False
            server.release.set()
            await engine.close()
        results["local_http_requests"] = len(server.requests)
        (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(verify())
