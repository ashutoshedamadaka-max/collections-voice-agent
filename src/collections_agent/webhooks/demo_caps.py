"""Server-side rate/spend limits for the public demo's live-call path.

Plain `sqlite3` (stdlib, no new dependency) — one small table, opened fresh per call rather
than held open, matching how little persistence this project needs elsewhere
(`sheets/client.py`'s own thin-wrapper style, no ORM). Every check here is enforced
server-side: the browser never sees the demo assistant's `publicKey`/`assistantOverrides`
unless `demo_live.py` has already decided a call is allowed (see its module docstring).

Day boundary for the daily ceiling is the UTC calendar day — simple and deterministic,
not the visitor's local day.

Known limitation, accepted not fixed (see docs/SHIP_PLAN.md): `record_call_cost` attributes
the real Vapi cost to whichever row is "most recent with no cost yet," which is only correct
if calls are processed one at a time — the same single-caller assumption `demo_live.py`'s
live SSE channel already documents. True concurrent calls from different visitors would
misattribute cost between their rows.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    cost_usd REAL
)
"""


@contextmanager
def _connect(db_path: str | Path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_call_start(db_path: str | Path, visitor_id: str) -> None:
    """Called the moment a live call is granted (not when it actually connects) — this is
    what the caps count, so a burst of rapid double-clicks can't race past a limit."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO demo_calls (visitor_id, started_at, cost_usd) VALUES (?, ?, NULL)",
            (visitor_id, datetime.now(UTC).isoformat()),
        )


def record_call_cost(db_path: str | Path, cost_usd: float) -> None:
    """Called once the real end-of-call-report arrives. See module docstring for the
    single-caller assumption this relies on."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE demo_calls SET cost_usd = ?
            WHERE id = (
                SELECT id FROM demo_calls WHERE cost_usd IS NULL
                ORDER BY started_at DESC LIMIT 1
            )
            """,
            (cost_usd,),
        )


def visitor_call_count(db_path: str | Path, visitor_id: str, window_hours: float) -> int:
    cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM demo_calls WHERE visitor_id = ? AND started_at >= ?",
            (visitor_id, cutoff),
        ).fetchone()
        return row[0]


def calls_today_count(db_path: str | Path) -> int:
    today_start = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM demo_calls WHERE started_at >= ?", (today_start,)
        ).fetchone()
        return row[0]


def cumulative_spend(db_path: str | Path) -> float:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM demo_calls").fetchone()
        return row[0]
