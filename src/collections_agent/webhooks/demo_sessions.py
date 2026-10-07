"""Visitor-owned live sessions, bounded leases and resumable events in the demo SQLite DB.

One live reservation at a time. Configure DEMO_STATE_DB_PATH on persistent storage to keep
usage and events across deployments; SQLite on Render's free filesystem is ephemeral.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from collections_agent.webhooks import demo_caps

TERMINAL = {"complete", "failed", "cancelled", "expired"}


def connect(db_path):
    return demo_caps._connect(db_path)


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS live_sessions (
            id TEXT PRIMARY KEY, visitor_id TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, state TEXT NOT NULL, call_id TEXT UNIQUE,
            usage_id INTEGER, processing INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS live_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            name TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS live_events_session ON live_events(session_id, id);
        CREATE TABLE IF NOT EXISTS live_tools (
            session_id TEXT NOT NULL, tool_id TEXT NOT NULL, result TEXT NOT NULL,
            PRIMARY KEY (session_id, tool_id)
        );
    """)


def _event(conn, session_id, name, payload):
    conn.execute(
        "INSERT INTO live_events(session_id,name,payload) VALUES (?,?,?)",
        (session_id, name, json.dumps(payload)),
    )


def _expire(conn):
    now = time.time()
    rows = conn.execute(
        "SELECT id FROM live_sessions WHERE expires < ? AND state IN ('reserved','active','analyzing')",
        (now,),
    ).fetchall()
    for (session_id,) in rows:
        _event(
            conn,
            session_id,
            "session_error",
            {"message": "The live session timed out. You can try again or replay a demo."},
        )
        conn.execute("UPDATE live_sessions SET state='expired' WHERE id=?", (session_id,))
    # Keep a day's reconnect history, but retain the separate usage ledger indefinitely.
    cutoff = now - 86400
    for table in ("live_events", "live_tools"):
        conn.execute(
            f"DELETE FROM {table} WHERE session_id IN (SELECT id FROM live_sessions WHERE created < ?)",
            (cutoff,),
        )
    conn.execute("DELETE FROM live_sessions WHERE created < ?", (cutoff,))


def busy(db_path) -> bool:
    with connect(db_path) as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        _expire(conn)
        return (
            conn.execute(
                "SELECT 1 FROM live_sessions WHERE state IN ('reserved','active','analyzing')"
            ).fetchone()
            is not None
        )


def reserve(db_path, visitor_id: str) -> str | None:
    with connect(db_path) as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        _expire(conn)
        if conn.execute(
            "SELECT 1 FROM live_sessions WHERE state IN ('reserved','active','analyzing')"
        ).fetchone():
            return None
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        conn.execute(
            "INSERT INTO live_sessions(id,visitor_id,created,expires,state) VALUES (?,?,?,?,'reserved')",
            (session_id, visitor_id, now, now + 90),
        )
        return session_id


def get(db_path, session_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        initialize(conn)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def correlate(db_path, message: dict[str, Any]) -> str | None:
    """Only a signed provider webhook with explicit correlation can select a session.

    Never fall back to the latest caller, including for old tool payloads without call IDs.
    """
    call = message.get("call") or {}
    call_id = call.get("id")
    if not call_id:
        return None
    variables = (call.get("assistantOverrides") or {}).get("variableValues") or {}
    session_id = variables.get("demo_session_id")
    with connect(db_path) as conn:
        initialize(conn)
        row = conn.execute("SELECT id FROM live_sessions WHERE call_id=?", (call_id,)).fetchone()
        if row:
            return row[0] if session_id in (None, row[0]) else None
        if not isinstance(session_id, str):
            return None
        row = conn.execute(
            "SELECT id FROM live_sessions WHERE id=? AND call_id IS NULL AND state='reserved' AND expires>?",
            (session_id, time.time()),
        ).fetchone()
        if row:
            conn.execute("UPDATE live_sessions SET call_id=? WHERE id=?", (call_id, session_id))
            return session_id
        return None


def connected(db_path, session_id: str, call_id: str) -> bool:
    from datetime import UTC, datetime

    with connect(db_path) as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT visitor_id,call_id,usage_id,state,expires FROM live_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row or row[3] in TERMINAL or row[4] < time.time() or row[1] not in (None, call_id):
            return False
        if conn.execute(
            "SELECT 1 FROM live_sessions WHERE call_id=? AND id<>?", (call_id, session_id)
        ).fetchone():
            return False
        if row[2] is None:
            cursor = conn.execute(
                "INSERT INTO demo_calls(visitor_id,started_at) VALUES (?,?)",
                (row[0], datetime.now(UTC).isoformat()),
            )
            conn.execute(
                "UPDATE live_sessions SET usage_id=?,call_id=?,state='active',expires=? WHERE id=?",
                (cursor.lastrowid, call_id, time.time() + 420, session_id),
            )
        return True


def cancel(db_path, session_id: str) -> None:
    with connect(db_path) as conn:
        initialize(conn)
        # A browser cannot refund a connected call or discard its pending analysis.
        conn.execute(
            "UPDATE live_sessions SET state='cancelled' WHERE id=? AND state='reserved' AND call_id IS NULL",
            (session_id,),
        )


def begin_analysis(db_path, session_id: str, call_id: str, cost: float | None) -> bool:
    with connect(db_path) as conn:
        initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT usage_id,processing,state FROM live_sessions WHERE id=? AND call_id=?",
            (session_id, call_id),
        ).fetchone()
        if not row or row[1] or row[2] in TERMINAL:
            return False
        conn.execute(
            "UPDATE live_sessions SET processing=1,state='analyzing',expires=? WHERE id=?",
            (time.time() + 180, session_id),
        )
        if row[0] and cost is not None:
            conn.execute("UPDATE demo_calls SET cost_usd=? WHERE id=?", (cost, row[0]))
        return True


def push(db_path, session_id: str, name: str, payload: dict) -> None:
    with connect(db_path) as conn:
        initialize(conn)
        _event(conn, session_id, name, payload)
        if name in ("final", "session_error"):
            state = "complete" if name == "final" else "failed"
            conn.execute("UPDATE live_sessions SET state=? WHERE id=?", (state, session_id))
        if name == "final":
            conn.execute(
                "UPDATE demo_calls SET cost_usd=COALESCE(cost_usd,0)+? "
                "WHERE id=(SELECT usage_id FROM live_sessions WHERE id=?)",
                (payload["cost"]["pipeline_cost"], session_id),
            )


def events(db_path, session_id: str, after: int):
    with connect(db_path) as conn:
        initialize(conn)
        _expire(conn)
        return conn.execute(
            "SELECT id,name,payload FROM live_events WHERE session_id=? AND id>? ORDER BY id",
            (session_id, after),
        ).fetchall()


def tool_result(db_path, session_id: str, tool_id: str, result: dict | None = None):
    with connect(db_path) as conn:
        initialize(conn)
        if result is not None:
            conn.execute(
                "INSERT OR IGNORE INTO live_tools VALUES (?,?,?)", (session_id, tool_id, json.dumps(result))
            )
        row = conn.execute(
            "SELECT result FROM live_tools WHERE session_id=? AND tool_id=?", (session_id, tool_id)
        ).fetchone()
        return json.loads(row[0]) if row else None
