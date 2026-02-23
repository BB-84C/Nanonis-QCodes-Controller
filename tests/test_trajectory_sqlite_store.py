from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nanonis_qcodes_controller.trajectory.sqlite_store import TrajectorySQLiteStore

REQUIRED_TABLES: set[str] = {
    "runs",
    "signal_catalog",
    "spec_catalog",
    "signal_samples",
    "spec_samples",
    "action_events",
    "monitor_errors",
}


def test_schema_contains_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()

        assert REQUIRED_TABLES.issubset(store.table_names())
    finally:
        store.close()


def test_can_create_run_and_insert_read_action_events(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        event_id = store.insert_action_event(
            run_id=run_id,
            dt_s=1.25,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=1.0,
            signal_window_end_dt_s=1.5,
            old_value_json={"value": 0.1},
            new_value_json={"value": 0.2},
        )

        event = store.get_action_event(event_id)

        assert event is not None
        assert event["id"] == event_id
        assert event["run_id"] == run_id
        assert event["action_kind"] == "set"
        assert event["detected_at_utc"] == "2026-02-22T00:00:01Z"
        assert event["spec_label"] == "Bias"
        assert event["signal_window_start_dt_s"] == pytest.approx(1.0)
        assert event["signal_window_end_dt_s"] == pytest.approx(1.5)
        assert json.loads(event["old_value_json"]) == {"value": 0.1}
        assert json.loads(event["new_value_json"]) == {"value": 0.2}

        events = store.list_action_events(run_id=run_id)
        assert [row["id"] for row in events] == [event_id]
    finally:
        store.close()


def test_can_query_signal_window_by_dt_bounds(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        signal_id = store.insert_signal_catalog(run_id=run_id, signal_label="Z Position", unit="m")
        store.insert_signal_sample(run_id=run_id, signal_id=signal_id, dt_s=0.1, values_json=[1.0])
        store.insert_signal_sample(run_id=run_id, signal_id=signal_id, dt_s=0.2, values_json=[2.0])
        store.insert_signal_sample(run_id=run_id, signal_id=signal_id, dt_s=0.3, values_json=[3.0])

        rows = store.get_signal_window(
            run_id=run_id,
            signal_id=signal_id,
            dt_min_s=0.1,
            dt_max_s=0.2,
        )

        assert [row["dt_s"] for row in rows] == [0.1, 0.2]
        assert [json.loads(row["values_json"]) for row in rows] == [[1.0], [2.0]]
    finally:
        store.close()


def test_rejects_invalid_foreign_key_in_signal_samples(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")

        with pytest.raises(sqlite3.IntegrityError):
            store.insert_signal_sample(
                run_id=run_id,
                signal_id=999_999,
                dt_s=0.1,
                values_json=[1.0],
            )
    finally:
        store.close()


def test_rejects_cross_run_signal_sample_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_one = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        run_two = store.create_run(run_name="run-002", started_at_utc="2026-02-22T00:01:00Z")
        signal_id = store.insert_signal_catalog(run_id=run_one, signal_label="Z Position", unit="m")

        with pytest.raises(sqlite3.IntegrityError):
            store.insert_signal_sample(
                run_id=run_two,
                signal_id=signal_id,
                dt_s=0.1,
                values_json=[1.0],
            )
    finally:
        store.close()


def test_json_columns_preserve_string_semantics(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        event_id = store.insert_action_event(
            run_id=run_id,
            dt_s=0.2,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=0.1,
            signal_window_end_dt_s=0.3,
            old_value_json="plain-text-value",
            new_value_json='{"already": "json"}',
        )

        event = store.get_action_event(event_id)

        assert event is not None
        assert json.loads(event["old_value_json"]) == "plain-text-value"
        assert json.loads(event["new_value_json"]) == '{"already": "json"}'
    finally:
        store.close()


def test_insert_sample_pair_rolls_back_if_second_insert_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        signal_id = store.insert_signal_catalog(run_id=run_id, signal_label="Z Position", unit="m")

        with pytest.raises(sqlite3.IntegrityError):
            store.insert_sample_pair(
                run_id=run_id,
                signal_id=signal_id,
                spec_id=999_999,
                dt_s=0.1,
                signal_values_json={"Z Position": 1.23},
                spec_vals_json={"Bias": 0.5},
            )

        signal_count = store._connection.execute(
            "SELECT COUNT(*) AS c FROM signal_samples WHERE run_id = ?",
            (run_id,),
        ).fetchone()["c"]
        spec_count = store._connection.execute(
            "SELECT COUNT(*) AS c FROM spec_samples WHERE run_id = ?",
            (run_id,),
        ).fetchone()["c"]

        assert signal_count == 0
        assert spec_count == 0
    finally:
        store.close()


def test_create_run_rejects_duplicate_run_name(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")

        with pytest.raises(ValueError, match="run_name"):
            store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:01:00Z")
    finally:
        store.close()
