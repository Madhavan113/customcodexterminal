"""Inert Codex protocol peer for integration tests; never connects to a model."""

import json
import sys
from pathlib import Path


def send(value):
    print(json.dumps(value), flush=True)


def event(method, params):
    send({"method": method, "params": params})


def main():
    log = Path(sys.argv[1])
    counter = 0
    active = {}
    approval_thread = None
    for line in sys.stdin:
        request = json.loads(line)
        with log.open("a") as stream:
            stream.write(json.dumps(request) + "\n")
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")
        result = {}
        if method == "initialize":
            result = {"userAgent": "inert-noir-test"}
        elif method == "initialized":
            continue
        elif method == "thread/start":
            counter += 1
            result = {
                "thread": {"id": f"fixture-{counter}"},
                "model": params.get("model") or "configured-model",
            }
        elif method == "thread/resume":
            result = {
                "thread": {"id": params["threadId"], "status": {"type": "idle"}},
                "model": "configured-model",
            }
        elif method == "thread/list":
            result = {"data": []}
        elif method == "turn/start":
            native = params["threadId"]
            turn = {"id": f"turn-{native}", "status": "inProgress"}
            active[native] = turn["id"]
            event("turn/started", {"threadId": native, "turn": turn})
            result = {"turn": turn}
            if params["input"][0]["text"] == "fast complete":
                event(
                    "turn/completed",
                    {
                        "threadId": native,
                        "turn": {"id": turn["id"], "status": "completed"},
                    },
                )
            send({"id": request_id, "result": result})
            event(
                "item/agentMessage/delta",
                {"threadId": native, "itemId": "message-1", "delta": "Fixture output"},
            )
            text = params["input"][0]["text"]
            if text == "need approval":
                approval_thread = native
                send(
                    {
                        "id": "approval-1",
                        "method": "item/commandExecution/requestApproval",
                        "params": {
                            "threadId": native,
                            "turnId": turn["id"],
                            "itemId": "cmd-1",
                            "command": "printf 'fixture'",
                            "reason": "Fixture command permission",
                            "startedAtMs": 1,
                        },
                    }
                )
            elif text == "failing command":
                event(
                    "item/completed",
                    {
                        "threadId": native,
                        "item": {
                            "id": "cmd-1",
                            "type": "commandExecution",
                            "command": "fixture-test",
                            "exitCode": 2,
                            "aggregatedOutput": "Fixture failure",
                            "status": "completed",
                        },
                    },
                )
            continue
        elif method == "turn/steer":
            result = {"turnId": active[params["threadId"]]}
        elif method == "turn/interrupt":
            event(
                "turn/completed",
                {
                    "threadId": params["threadId"],
                    "turn": {"id": params["turnId"], "status": "interrupted"},
                },
            )
        elif method is None and request_id == "approval-1":
            event(
                "item/agentMessage/delta",
                {
                    "threadId": approval_thread,
                    "itemId": "decision",
                    "delta": request["result"]["decision"],
                },
            )
            event(
                "turn/completed",
                {
                    "threadId": approval_thread,
                    "turn": {"id": active[approval_thread], "status": "completed"},
                },
            )
            continue
        else:
            send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unsupported fixture method {method}",
                    },
                }
            )
            continue
        send({"id": request_id, "result": result})


if __name__ == "__main__":
    main()
