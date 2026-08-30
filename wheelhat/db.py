"""SQLite persistence.

Wheels are stored as JSON documents. The dataset for a single streamer is tiny,
so calls are synchronous and guarded by a lock rather than going through an async
driver - the latency is well under the cost of an await hop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from . import config
from .models import SpinRecord, Wheel, now

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS wheels (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    doc         TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spin_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wheel_id   TEXT NOT NULL,
    wheel_name TEXT NOT NULL DEFAULT '',
    slice_id   TEXT NOT NULL,
    label      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual',
    actor      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spin_history_wheel ON spin_history (wheel_id, id DESC);

CREATE TABLE IF NOT EXISTS action_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wheel_id   TEXT NOT NULL DEFAULT '',
    action_id  TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 1,
    detail     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_log_time ON action_log (id DESC);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# --------------------------------------------------------------------------- wheels


def list_wheels() -> list[Wheel]:
    with _lock:
        rows = connect().execute(
            "SELECT doc FROM wheels ORDER BY position ASC, created_at ASC"
        ).fetchall()
    return [Wheel.model_validate_json(r["doc"]) for r in rows]


def get_wheel(wheel_id: str) -> Optional[Wheel]:
    with _lock:
        row = connect().execute("SELECT doc FROM wheels WHERE id = ?", (wheel_id,)).fetchone()
    return Wheel.model_validate_json(row["doc"]) if row else None


def save_wheel(wheel: Wheel) -> Wheel:
    wheel.updated_at = now()
    doc = wheel.model_dump_json()
    with _lock:
        conn = connect()
        exists = conn.execute("SELECT 1 FROM wheels WHERE id = ?", (wheel.id,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE wheels SET name = ?, doc = ?, updated_at = ? WHERE id = ?",
                (wheel.name, doc, wheel.updated_at, wheel.id),
            )
        else:
            nxt = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM wheels").fetchone()["p"]
            conn.execute(
                "INSERT INTO wheels (id, name, position, doc, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (wheel.id, wheel.name, nxt, doc, wheel.created_at, wheel.updated_at),
            )
        conn.commit()
    return wheel


def delete_wheel(wheel_id: str) -> bool:
    with _lock:
        conn = connect()
        cur = conn.execute("DELETE FROM wheels WHERE id = ?", (wheel_id,))
        conn.commit()
        return cur.rowcount > 0


def reorder_wheels(ordered_ids: list[str]) -> None:
    with _lock:
        conn = connect()
        for index, wheel_id in enumerate(ordered_ids):
            conn.execute("UPDATE wheels SET position = ? WHERE id = ?", (index, wheel_id))
        conn.commit()


# ------------------------------------------------------------------------- settings


def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        row = connect().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    payload = json.dumps(value)
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )
        conn.commit()


def delete_setting(key: str) -> None:
    with _lock:
        conn = connect()
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


# -------------------------------------------------------------------------- history


def add_spin(record: SpinRecord) -> SpinRecord:
    with _lock:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO spin_history (wheel_id, wheel_name, slice_id, label, source, actor, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.wheel_id,
                record.wheel_name,
                record.slice_id,
                record.label,
                record.source,
                record.actor,
                record.created_at,
            ),
        )
        conn.commit()
        record.id = cur.lastrowid
    return record


def list_spins(wheel_id: str | None = None, limit: int = 50) -> list[SpinRecord]:
    sql = "SELECT * FROM spin_history"
    params: list[Any] = []
    if wheel_id:
        sql += " WHERE wheel_id = ?"
        params.append(wheel_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock:
        rows = connect().execute(sql, params).fetchall()
    return [SpinRecord(**dict(r)) for r in rows]


def clear_spins(wheel_id: str | None = None) -> None:
    with _lock:
        conn = connect()
        if wheel_id:
            conn.execute("DELETE FROM spin_history WHERE wheel_id = ?", (wheel_id,))
        else:
            conn.execute("DELETE FROM spin_history")
        conn.commit()


def log_action(
    *, wheel_id: str, action_id: str, action_type: str, name: str, ok: bool, detail: str
) -> None:
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO action_log (wheel_id, action_id, action_type, name, ok, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wheel_id, action_id, action_type, name, 1 if ok else 0, detail[:2000], now()),
        )
        # Keep the log bounded; this is a debugging aid, not an audit trail.
        conn.execute(
            "DELETE FROM action_log WHERE id < (SELECT MAX(id) - 500 FROM action_log)"
        )
        conn.commit()


def clear_action_log() -> None:
    with _lock:
        conn = connect()
        conn.execute("DELETE FROM action_log")
        conn.commit()


def list_action_log(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        rows = connect().execute(
            "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
