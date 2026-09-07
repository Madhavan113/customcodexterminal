"""A private Unix socket keeps agent sessions alive when the dashboard detaches."""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import stat
from pathlib import Path

from .engine import Engine
from .rpc import MAX_FRAME, RpcError
from .store import ACTIVE, private_directory, socket_path

logger = logging.getLogger(__name__)


async def serve(project, directory):
    os.umask(0o077)
    directory = private_directory(directory)
    with (directory / "daemon.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path = socket_path(directory)
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError(f"Refusing to replace a non-socket file: {path}")
            path.unlink()
        engine = Engine(project, directory)
        stop = asyncio.Event()
        handlers = set()

        async def handle(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            shutdown = False
            try:
                line = await asyncio.wait_for(reader.readline(), 10)
                request = json.loads(line)
                method, params = request.get("method"), request.get("params") or {}
                if not isinstance(params, dict):
                    raise TypeError("Invalid request")
                if method == "ping":
                    result = {
                        "pid": os.getpid(),
                        "project": str(project),
                        "version": "0.1.0",
                    }
                elif method == "snapshot":
                    result = engine.snapshot(params.get("selected"))
                elif method == "launch":
                    result = await engine.launch(**params)
                elif method == "send":
                    result = await engine.send(**params)
                elif method == "interrupt":
                    result = await engine.interrupt(**params)
                elif method == "resolve":
                    result = engine.resolve(**params)
                elif method == "changes":
                    result = await engine.changes(**params)
                elif method == "shutdown":
                    active = [
                        agent
                        for agent in engine.store.agents()
                        if agent["status"] in ACTIVE
                    ]
                    if active and not params.get("interrupt"):
                        raise ValueError(
                            "Agents are still active. Detach to leave them running, or use shutdown --interrupt."
                        )
                    result = {"message": "Session manager stopped"}
                    shutdown = True
                else:
                    raise ValueError("Unknown workspace command")
                response = {"result": result}
            except asyncio.CancelledError:
                writer.close()
                handlers.discard(task)
                raise
            except (ValueError, TypeError, RpcError, TimeoutError) as error:
                response = {"error": str(error)}
            except Exception as error:
                logger.exception("Workspace request failed")
                response = {"error": str(error)}
            try:
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                writer.close()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await writer.wait_closed()
                handlers.discard(task)
                if shutdown:
                    stop.set()

        server = await asyncio.start_unix_server(
            handle, path=str(path), limit=MAX_FRAME
        )
        path.chmod(0o600)
        (directory / "daemon.pid").write_text(str(os.getpid()))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            async with server:
                await stop.wait()
        finally:
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*list(handlers), return_exceptions=True)
            await engine.close()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            path.unlink(missing_ok=True)
            (Path(directory) / "daemon.pid").unlink(missing_ok=True)
