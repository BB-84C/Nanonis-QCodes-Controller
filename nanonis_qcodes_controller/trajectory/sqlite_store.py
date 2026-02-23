from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class TrajectorySQLiteStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_name TEXT NOT NULL UNIQUE,
                    started_at_utc TEXT NOT NULL
                )
                """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS signal_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    signal_label TEXT NOT NULL,
                    unit TEXT,
                    metadata_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    UNIQUE(id, run_id)
                )
                """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS spec_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    spec_label TEXT NOT NULL,
                    unit TEXT,
                    metadata_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    UNIQUE(id, run_id)
                )
                """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS signal_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    signal_id INTEGER NOT NULL,
                    dt_s REAL NOT NULL,
                    values_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    FOREIGN KEY(signal_id, run_id) REFERENCES signal_catalog(id, run_id)
                )
                """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS spec_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    spec_id INTEGER NOT NULL,
                    dt_s REAL NOT NULL,
                    vals_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    FOREIGN KEY(spec_id, run_id) REFERENCES spec_catalog(id, run_id)
                )
                """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS action_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    dt_s REAL NOT NULL,
                    action_kind TEXT NOT NULL,
                    detected_at_utc TEXT NOT NULL,
                    spec_label TEXT NOT NULL,
                    signal_window_start_dt_s REAL NOT NULL,
                    signal_window_end_dt_s REAL NOT NULL,
                    delta_value REAL,
                    old_value_json TEXT,
                    new_value_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                )
                """)
            action_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(action_events)").fetchall()
            }
            if "delta_value" not in action_columns:
                self._connection.execute("ALTER TABLE action_events ADD COLUMN delta_value REAL")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS monitor_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    dt_s REAL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                )
                """)

    def table_names(self) -> set[str]:
        cursor = self._connection.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """)
        return {str(row["name"]) for row in cursor.fetchall()}

    def close(self) -> None:
        self._connection.close()

    def create_run(self, *, run_name: str, started_at_utc: str) -> int:
        existing = self._connection.execute(
            "SELECT id FROM runs WHERE run_name = ? LIMIT 1",
            (run_name,),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"run_name '{run_name}' already exists; run_name must be unique.")

        with self._connection:
            try:
                cursor = self._connection.execute(
                    "INSERT INTO runs (run_name, started_at_utc) VALUES (?, ?)",
                    (run_name, started_at_utc),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"run_name '{run_name}' already exists; run_name must be unique."
                ) from exc
        return _require_row_id(cursor.lastrowid)

    def insert_signal_catalog(
        self,
        *,
        run_id: int,
        signal_label: str,
        unit: str | None = None,
        metadata_json: Any | None = None,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO signal_catalog (run_id, signal_label, unit, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, signal_label, unit, _to_json_text(metadata_json)),
            )
        return _require_row_id(cursor.lastrowid)

    def insert_spec_catalog(
        self,
        *,
        run_id: int,
        spec_label: str,
        unit: str | None = None,
        metadata_json: Any | None = None,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO spec_catalog (run_id, spec_label, unit, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, spec_label, unit, _to_json_text(metadata_json)),
            )
        return _require_row_id(cursor.lastrowid)

    def insert_signal_sample(
        self,
        *,
        run_id: int,
        signal_id: int,
        dt_s: float,
        values_json: Any,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO signal_samples (run_id, signal_id, dt_s, values_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, signal_id, dt_s, _to_json_text(values_json)),
            )
        return _require_row_id(cursor.lastrowid)

    def insert_spec_sample(
        self,
        *,
        run_id: int,
        spec_id: int,
        dt_s: float,
        vals_json: Any,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO spec_samples (run_id, spec_id, dt_s, vals_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, spec_id, dt_s, _to_json_text(vals_json)),
            )
        return _require_row_id(cursor.lastrowid)

    def insert_sample_pair(
        self,
        *,
        run_id: int,
        signal_id: int,
        spec_id: int,
        dt_s: float,
        signal_values_json: Any,
        spec_vals_json: Any,
    ) -> tuple[int, int]:
        with self._connection:
            signal_cursor = self._connection.execute(
                """
                INSERT INTO signal_samples (run_id, signal_id, dt_s, values_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, signal_id, dt_s, _to_json_text(signal_values_json)),
            )
            spec_cursor = self._connection.execute(
                """
                INSERT INTO spec_samples (run_id, spec_id, dt_s, vals_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, spec_id, dt_s, _to_json_text(spec_vals_json)),
            )
        return _require_row_id(signal_cursor.lastrowid), _require_row_id(spec_cursor.lastrowid)

    def insert_action_event(
        self,
        *,
        run_id: int,
        dt_s: float,
        action_kind: str,
        detected_at_utc: str,
        spec_label: str,
        signal_window_start_dt_s: float,
        signal_window_end_dt_s: float,
        delta_value: float | None = None,
        old_value_json: Any | None = None,
        new_value_json: Any | None = None,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO action_events (
                    run_id,
                    dt_s,
                    action_kind,
                    detected_at_utc,
                    spec_label,
                    signal_window_start_dt_s,
                    signal_window_end_dt_s,
                    delta_value,
                    old_value_json,
                    new_value_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    dt_s,
                    action_kind,
                    detected_at_utc,
                    spec_label,
                    signal_window_start_dt_s,
                    signal_window_end_dt_s,
                    delta_value,
                    _to_json_text(old_value_json),
                    _to_json_text(new_value_json),
                ),
            )
        return _require_row_id(cursor.lastrowid)

    def list_action_events(self, *, run_id: int | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            cursor = self._connection.execute("""
                SELECT *
                FROM action_events
                ORDER BY run_id ASC, dt_s ASC, id ASC
                """)
        else:
            cursor = self._connection.execute(
                """
                SELECT *
                FROM action_events
                WHERE run_id = ?
                ORDER BY dt_s ASC, id ASC
                """,
                (run_id,),
            )
        return [dict(row) for row in cursor.fetchall()]

    def get_action_event(self, action_event_id: int) -> dict[str, Any] | None:
        cursor = self._connection.execute(
            "SELECT * FROM action_events WHERE id = ?",
            (action_event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_signal_window(
        self,
        *,
        run_id: int,
        signal_id: int,
        dt_min_s: float,
        dt_max_s: float,
    ) -> list[dict[str, Any]]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM signal_samples
            WHERE run_id = ? AND signal_id = ? AND dt_s >= ? AND dt_s <= ?
            ORDER BY dt_s ASC, id ASC
            """,
            (run_id, signal_id, dt_min_s, dt_max_s),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_latest_run_id(self) -> int | None:
        cursor = self._connection.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            return None
        return int(row["id"])

    def get_run_id_by_name(self, run_name: str) -> int | None:
        cursor = self._connection.execute(
            "SELECT id FROM runs WHERE run_name = ? ORDER BY id DESC LIMIT 2",
            (run_name,),
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"Multiple runs found for run_name '{run_name}'; run_name must be unique."
            )
        return int(rows[0]["id"])

    def get_action_event_by_idx(self, *, run_id: int, action_idx: int) -> dict[str, Any] | None:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM action_events
            WHERE run_id = ?
            ORDER BY dt_s ASC, id ASC
            LIMIT 1 OFFSET ?
            """,
            (run_id, action_idx),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def list_signal_samples_in_window(
        self,
        *,
        run_id: int,
        dt_min_s: float,
        dt_max_s: float,
    ) -> list[dict[str, Any]]:
        cursor = self._connection.execute(
            """
            SELECT
                signal_samples.id,
                signal_samples.run_id,
                signal_samples.signal_id,
                signal_catalog.signal_label,
                signal_samples.dt_s,
                signal_samples.values_json
            FROM signal_samples
            INNER JOIN signal_catalog
                ON signal_samples.signal_id = signal_catalog.id
                AND signal_samples.run_id = signal_catalog.run_id
            WHERE signal_samples.run_id = ? AND signal_samples.dt_s >= ? AND signal_samples.dt_s <= ?
            ORDER BY signal_samples.dt_s ASC, signal_samples.id ASC
            """,
            (run_id, dt_min_s, dt_max_s),
        )
        return [dict(row) for row in cursor.fetchall()]


def _to_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _require_row_id(row_id: int | None) -> int:
    if row_id is None:
        raise RuntimeError("SQLite did not return a row id.")
    return int(row_id)
