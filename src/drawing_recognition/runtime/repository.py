"""SQLite persistence for uploaded drawings and P1/P2 recognition runs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "runtime"
DATABASE_PATH = DATA_ROOT / "recognition.db"
UPLOAD_ROOT = DATA_ROOT / "uploads"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_repository() -> None:
    connection = _connect()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS recognition_runs (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, file_path TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, progress INTEGER NOT NULL,
                message TEXT NOT NULL, result_json TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS recognition_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                phase TEXT NOT NULL, progress INTEGER NOT NULL, message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.commit()
    finally:
        connection.close()


def create_run(filename: str, file_path: Path) -> dict[str, Any]:
    initialize_repository()
    run_id, now = str(uuid.uuid4()), _now()
    record = {
        "id": run_id, "filename": filename, "file_path": str(file_path), "status": "queued",
        "phase": "queued", "progress": 0, "message": "图纸已入队，等待分析。",
        "created_at": now, "updated_at": now, "result": None, "error": None,
    }
    connection = _connect()
    try:
        connection.execute(
            """INSERT INTO recognition_runs
            (id, filename, file_path, status, phase, progress, message, created_at, updated_at)
            VALUES (:id, :filename, :file_path, :status, :phase, :progress, :message, :created_at, :updated_at)""",
            record,
        )
        _append_event(connection, run_id, "queued", 0, record["message"])
        connection.commit()
    finally:
        connection.close()
    return record


def _append_event(connection: sqlite3.Connection, run_id: str, phase: str, progress: int, message: str) -> None:
    connection.execute(
        "INSERT INTO recognition_events (run_id, phase, progress, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, phase, progress, message, _now()),
    )


def update_run(run_id: str, *, status: str, phase: str, progress: int, message: str, result: dict | None = None, error: str | None = None) -> None:
    connection = _connect()
    try:
        connection.execute(
            """UPDATE recognition_runs SET status=?, phase=?, progress=?, message=?, result_json=?, error=?, updated_at=?
               WHERE id=?""",
            (status, phase, progress, message, json.dumps(result, ensure_ascii=False) if result else None, error, _now(), run_id),
        )
        _append_event(connection, run_id, phase, progress, message)
        connection.commit()
    finally:
        connection.close()


def get_run(run_id: str) -> dict[str, Any] | None:
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    result = dict(row)
    result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
    result.pop("file_path", None)
    return result


def list_events(run_id: str) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT phase, progress, message, created_at FROM recognition_events WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_run_path(run_id: str) -> Path | None:
    connection = _connect()
    try:
        row = connection.execute("SELECT file_path FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
    finally:
        connection.close()
    return Path(row["file_path"]) if row else None