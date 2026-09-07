"""Private, durable workspace state. All writes belong to the daemon thread."""

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path

ACTIVE = {"starting", "working", "waiting", "interrupting", "paused"}
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def clean(value, limit=100_000):
    """Treat model output, filenames and titles as text, never terminal commands."""
    text = ANSI.sub("", str(value))
    return "".join(
        c
        for c in text
        if c in "\n\t"
        or (
            unicodedata.category(c) not in {"Cc", "Cs"}
            and c not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        )
    )[:limit]


def identifier():
    return uuid.uuid4().hex[:12]


def state_path(project, base=None):
    base = Path(
        base
        or os.environ.get("NOIR_STATE_DIR")
        or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noir"
    )
    key = hashlib.sha256(str(Path(project).resolve()).encode()).hexdigest()[:16]
    return base.expanduser().resolve() / key


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"State directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError(f"State directory belongs to another user: {path}")
    path.chmod(0o700)
    return path


def socket_path(directory):
    path = Path(directory) / "control.sock"
    if len(os.fsencode(path)) > 100:
        key = hashlib.sha256(os.fsencode(directory)).hexdigest()[:24]
        path = private_directory(Path("/tmp") / f"noir-{os.getuid()}") / f"{key}.sock"
    return path


class Store:
    def __init__(self, directory):
        self.directory = private_directory(directory)
        self.db = sqlite3.connect(self.directory / "workspace.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL,
                kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL,
                stamp REAL NOT NULL, event_key TEXT,
                UNIQUE(agent, event_key)
            );
            CREATE INDEX IF NOT EXISTS events_agent ON events(agent, id);
            CREATE TABLE IF NOT EXISTS attention (
                id TEXT PRIMARY KEY, agent TEXT NOT NULL, kind TEXT NOT NULL,
                title TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL,
                stamp REAL NOT NULL
            );
        """)
        (self.directory / "workspace.sqlite3").chmod(0o600)
        self.revision = 0

    def close(self):
        self.db.close()

    def agents(self):
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT data FROM agents ORDER BY rowid")
        ]

    def agent(self, agent_id):
        row = self.db.execute(
            "SELECT data FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Agent no longer exists")
        return json.loads(row[0])

    def create_agent(self, provider, title, cwd, **extra):
        now = time.time()
        agent = {
            "id": identifier(),
            "provider": provider,
            "title": clean(title, 80),
            "cwd": str(cwd),
            "parent": None,
            "native_id": None,
            "status": "starting",
            "detail": "Connecting",
            "model": None,
            "turn_id": None,
            "created": now,
            "updated": now,
            "isolated": False,
            "can_message": True,
            "can_interrupt": True,
        }
        agent.update(extra)
        self.db.execute(
            "INSERT INTO agents VALUES (?, ?)", (agent["id"], json.dumps(agent))
        )
        self.db.commit()
        self.revision += 1
        return agent

    def update(self, agent_id, **fields):
        agent = self.agent(agent_id)
        agent.update(fields, updated=time.time())
        self.db.execute(
            "UPDATE agents SET data=? WHERE id=?", (json.dumps(agent), agent_id)
        )
        self.db.commit()
        self.revision += 1
        return agent

    def event(self, agent_id, kind, message, data=None, key=None, append=False):
        message = clean(message)
        existing = None
        if key:
            existing = self.db.execute(
                "SELECT id, message FROM events WHERE agent=? AND event_key=?",
                (agent_id, key),
            ).fetchone()
        if existing:
            message = clean(existing["message"] + message) if append else message
            self.db.execute(
                "UPDATE events SET kind=?, message=?, data=? WHERE id=?",
                (kind, message, json.dumps(data or {}), existing["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO events(agent, kind, message, data, stamp, event_key) VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, kind, message, json.dumps(data or {}), time.time(), key),
            )
        self.db.commit()
        self.revision += 1

    def events(self, agent_id, limit=200):
        rows = self.db.execute(
            "SELECT * FROM events WHERE agent=? ORDER BY id DESC LIMIT ?",
            (agent_id, limit),
        )
        events, size = [], 0
        for row in rows:
            item_size = len(row["message"].encode()) + len(row["data"].encode())
            if events and size + item_size > 2_000_000:
                break
            data = (
                json.loads(row["data"])
                if item_size < 2_000_000
                else {"detail": clean(row["data"], 50_000) + "\n[Preview truncated]"}
            )
            events.append(dict(row, data=data))
            size += item_size
        return list(reversed(events))

    def attention(self, agent_id, kind, title, data=None, pending=False):
        request_id = identifier()
        self.db.execute(
            "INSERT INTO attention VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                agent_id,
                kind,
                clean(title, 200),
                json.dumps(data or {}),
                "pending" if pending else "unread",
                time.time(),
            ),
        )
        self.db.commit()
        self.revision += 1
        return request_id

    def inbox(self):
        rows = self.db.execute(
            "SELECT * FROM attention WHERE status IN ('pending', 'unread') ORDER BY (status='pending') DESC, stamp DESC LIMIT 200"
        )
        return [dict(row, data=json.loads(row["data"])) for row in rows]

    def resolve(self, request_id, status):
        self.db.execute(
            "UPDATE attention SET status=? WHERE id=?", (status, request_id)
        )
        self.db.commit()
        self.revision += 1

    def recover(self):
        """A restarted daemon cannot claim old processes or replay old approvals."""
        for agent in self.agents():
            if agent["status"] in ACTIVE:
                self.update(
                    agent["id"],
                    status="disconnected",
                    turn_id=None,
                    detail="Session manager restarted; send a message to resume",
                )
        self.db.execute("UPDATE attention SET status='expired' WHERE status='pending'")
        self.db.commit()
