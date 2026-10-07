"""Pauses of the main agent when its quota runs out, persisted in SQLite with atomic exits.

    paused --resume--> resumed    (same provider, at the reset)
           --resend--> resent     (another route, chosen by the user)
           --cancel--> cancelled

Every exit is ``UPDATE ... WHERE state = 'paused'``: if two tabs (or the reset and a click) resolve at once,
SQLite serializes the writes and only one changes the row; the other gets False.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

ACTIONS = {"resume": "resumed", "resend": "resent", "cancel": "cancelled"}
_FIELDS = ("id", "session", "provider", "model", "reset_at", "state", "created")
_COLUMNS = ", ".join(_FIELDS)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pauses (
    id INTEGER PRIMARY KEY,
    session TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    reset_at TEXT,
    state TEXT NOT NULL DEFAULT 'paused',
    target TEXT,
    created REAL NOT NULL,
    resolved REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_session ON pauses (session) WHERE state = 'paused';
"""

# v0.1 kept the pauses in <HERMES_HOME>/orquehelx/pausas.db with Spanish names.
_LEGACY_COPY = """
INSERT OR IGNORE INTO pauses (id, session, provider, model, reset_at, state, target, created, resolved)
SELECT id, sesion, proveedor, modelo, reinicio,
       CASE estado WHEN 'pausado' THEN 'paused' WHEN 'reanudado' THEN 'resumed'
                   WHEN 'reenviado' THEN 'resent' WHEN 'cancelado' THEN 'cancelled' ELSE estado END,
       destino, creado, resuelto
FROM legacy.pausas
"""


def open_db(path: Path, legacy: Path | None = None) -> sqlite3.Connection:
    """Open (and create) the database. Until the v0.1 pauses from ``legacy`` are copied, every open tries again:
    a failed copy raises and leaves nothing marked. The old file stays untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout: a concurrent write waits its turn instead of failing with "database is locked".
    con = sqlite3.connect(path, timeout=5)
    try:
        con.executescript(_SCHEMA)
        if con.execute("PRAGMA user_version").fetchone()[0] == 0:
            _carry_over(con, legacy)
    except Exception:
        con.close()
        raise
    return con


def _carry_over(con: sqlite3.Connection, legacy: Path | None) -> None:
    """Copy the v0.1 rows and mark the copy done (user_version 1) in the same transaction. OR IGNORE: two first
    opens racing copy the same rows, and the second one keeps the first one's."""
    if legacy is None or not legacy.exists():
        with con:
            con.execute("PRAGMA user_version = 1")
        return
    con.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
    try:
        with con:
            con.execute(_LEGACY_COPY)
            con.execute("PRAGMA user_version = 1")
    finally:
        con.execute("DETACH DATABASE legacy")


def _dict(row) -> dict:
    return dict(zip(_FIELDS, row))


def create(con: sqlite3.Connection, session: str, provider: str, model: str | None,
           reset_at: datetime | None) -> dict:
    """Pause the session; if it already had a pending pause, return that one (the unique index ensures it)."""
    try:
        with con:
            con.execute("INSERT INTO pauses (session, provider, model, reset_at, created) VALUES (?, ?, ?, ?, ?)",
                        (session, provider, model, reset_at.isoformat() if reset_at else None, time.time()))
    except sqlite3.IntegrityError:
        # Duplicate on the "one pending per session" index: the existing one stands. Any other conflict raises.
        if (existing := _pending(con, session)) is None:
            raise
        return existing
    return _pending(con, session)


def _pending(con: sqlite3.Connection, session: str) -> dict | None:
    row = con.execute(f"SELECT {_COLUMNS} FROM pauses WHERE session = ? AND state = 'paused'",
                      (session,)).fetchone()
    return _dict(row) if row else None


def pending(con: sqlite3.Connection) -> list[dict]:
    return [_dict(r) for r in con.execute(f"SELECT {_COLUMNS} FROM pauses WHERE state = 'paused' ORDER BY id")]


def resolve(con: sqlite3.Connection, pause: int, action: str, target: str | None = None) -> bool:
    """True if this call took the pause out of 'paused'; False if another one had already resolved it."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; use one of: {', '.join(ACTIONS)}")
    with con:
        cursor = con.execute(
            "UPDATE pauses SET state = ?, target = ?, resolved = ? WHERE id = ? AND state = 'paused'",
            (ACTIONS[action], target, time.time(), pause))
    return cursor.rowcount == 1
