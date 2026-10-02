"""SQLite audit history: every detected change is recorded."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snippet_id TEXT NOT NULL,
    file_url TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    old_code TEXT NOT NULL DEFAULT '',
    new_code TEXT NOT NULL DEFAULT '',
    diff TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    commit_url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_changes_snippet ON changes(snippet_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_changes_url ON changes(file_url, id DESC);
"""


@dataclass
class ChangeRecord:
    id: int
    snippet_id: str
    file_url: str
    checked_at: str
    old_code: str
    new_code: str
    diff: str
    summary: str
    commit_sha: str
    commit_url: str


def default_db_path(config_path: str, override: str = "") -> str:
    if override.strip():
        p = os.path.expanduser(override.strip())
        return p if os.path.isabs(p) else os.path.join(os.path.dirname(config_path), p)
    return os.path.join(os.path.dirname(config_path) or ".", "echelon-history.db")


def _connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    return conn


def record_change(
    db_path: str,
    *,
    snippet_id: str,
    file_url: str,
    old_code: str,
    new_code: str,
    diff: str,
    summary: str = "",
    commit_sha: str = "",
    commit_url: str = "",
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO changes (snippet_id, file_url, checked_at, old_code, new_code, diff, summary, commit_sha, commit_url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snippet_id,
                file_url,
                datetime.now(UTC).isoformat(timespec="seconds"),
                old_code,
                new_code,
                diff,
                summary or "",
                commit_sha or "",
                commit_url or "",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_changes(db_path: str, *, file_url: str = "", limit: int = 20) -> list[ChangeRecord]:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        try:
            if file_url:
                rows = conn.execute(
                    "SELECT id, snippet_id, file_url, checked_at, old_code, new_code, diff, summary, commit_sha, commit_url"
                    " FROM changes WHERE file_url = ? ORDER BY id DESC LIMIT ?",
                    (file_url, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, snippet_id, file_url, checked_at, old_code, new_code, diff, summary, commit_sha, commit_url"
                    " FROM changes ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []  # fresh/foreign db without schema
        return [ChangeRecord(*r) for r in rows]
    finally:
        conn.close()
