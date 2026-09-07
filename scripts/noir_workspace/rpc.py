"""JSON-lines transport for the Codex control protocol and Noir's local socket."""

import asyncio
import contextlib
import json
import logging

MAX_FRAME = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


class RpcError(RuntimeError):
    pass


class ProcessRpc:
    def __init__(self, on_event, on_request, on_disconnect):
        self.on_event = on_event
        self.on_request = on_request
        self.on_disconnect = on_disconnect
        self.process = None
        self.pending = {}
        self.counter = 0
        self.closed = True
        self.tasks = set()
        self.stderr = ""

    async def start(self, command, cwd, env=None):
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME,
            start_new_session=True,
        )
        self.closed = False
        self.reader = asyncio.create_task(self._read())
        self.error_reader = asyncio.create_task(self._stderr())

    async def _stderr(self):
        while data := await self.process.stderr.read(4096):
            self.stderr = (self.stderr + data.decode(errors="replace"))[-8000:]

    async def write(self, message):
        if self.closed or not self.process or self.process.returncode is not None:
            raise RpcError("Codex connection is closed")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params=None, timeout=45):
        self.counter += 1
        request_id = f"noir-{self.counter}"
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.write(
                {"id": request_id, "method": method, "params": params or {}}
            )
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            raise RpcError(f"Codex did not acknowledge {method}") from error
        finally:
            self.pending.pop(request_id, None)

    async def _answer(self, message):
        try:
            result = await self.on_request(message["method"], message.get("params", {}))
            await self.write({"id": message["id"], "result": result})
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.exception("Could not answer a Codex control request")
            with contextlib.suppress(RpcError, BrokenPipeError, ConnectionResetError):
                await self.write(
                    {
                        "id": message["id"],
                        "error": {"code": -32603, "message": str(error)},
                    }
                )

    async def _read(self):
        reason = "Codex disconnected"
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        task = asyncio.create_task(self._answer(message))
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                    else:
                        self.on_event(message["method"], message.get("params", {}))
                elif "id" in message:
                    future = self.pending.get(message["id"])
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        future.set_exception(
                            RpcError(
                                message["error"].get("message", "Codex request failed")
                            )
                        )
                    else:
                        future.set_result(message.get("result", {}))
            await self.process.wait()
            if self.process.returncode:
                reason = (
                    self.stderr.strip()
                    or f"Codex exited with status {self.process.returncode}"
                )
        except asyncio.CancelledError:
            reason = "Session manager stopped"
        except Exception as error:
            logger.exception("Codex transport failed")
            reason = f"Codex connection failed: {error}"
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RpcError(reason))
            for task in list(self.tasks):
                task.cancel()
            self.on_disconnect(reason)

    async def close(self):
        if self.process is None:
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(
            self.reader, self.error_reader, *list(self.tasks), return_exceptions=True
        )


async def local_call(path, method, params=None, timeout=15):
    async def exchange():
        reader, writer = await asyncio.open_unix_connection(str(path), limit=MAX_FRAME)
        try:
            writer.write(
                (json.dumps({"method": method, "params": params or {}}) + "\n").encode()
            )
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise RpcError("Session manager disconnected")
            response = json.loads(line)
            if "error" in response:
                raise RpcError(response["error"])
            return response["result"]
        finally:
            writer.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()

    return await asyncio.wait_for(exchange(), timeout)
