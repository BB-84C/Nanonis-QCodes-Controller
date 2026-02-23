from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from nanonis_qcodes_controller.trajectory.monitor import TrajectoryMonitorRunner
from nanonis_qcodes_controller.trajectory.sqlite_store import TrajectorySQLiteStore


def _build_runner(
    *,
    store: TrajectorySQLiteStore,
    run_id: int,
    spec_values: list[dict[str, object]],
    action_window_s: float = 2.5,
) -> TrajectoryMonitorRunner:
    now_s = 100.0
    specs_iter = iter(spec_values)

    def monotonic_time_s() -> float:
        return now_s

    def sleep_s(duration_s: float) -> None:
        nonlocal now_s
        now_s += duration_s

    return TrajectoryMonitorRunner(
        store=store,
        run_id=run_id,
        run_start_utc="2026-02-22T00:00:00Z",
        interval_s=0.1,
        rotate_entries=10,
        action_window_s=action_window_s,
        poll_signals=lambda: {"Z Position": 1.0},
        poll_specs=lambda: next(specs_iter),
        monotonic_time_s=monotonic_time_s,
        sleep_s=sleep_s,
    )


def test_emits_action_only_when_spec_value_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        runner = _build_runner(
            store=store,
            run_id=run_id,
            spec_values=[
                {"Bias": 0.5},
                {"Bias": 0.5},
                {"Bias": 0.75},
            ],
        )

        runner.run_iterations(3)

        action_events = store.list_action_events(run_id=run_id)
        signal_rows = store._connection.execute(
            "SELECT id FROM signal_samples WHERE run_id = ?",
            (run_id,),
        ).fetchall()

        assert len(signal_rows) == 3
        assert len(action_events) == 1
        assert action_events[0]["spec_label"] == "Bias"
        assert json.loads(action_events[0]["old_value_json"]) == 0.5
        assert json.loads(action_events[0]["new_value_json"]) == 0.75
        assert action_events[0]["delta_value"] == pytest.approx(0.25)
    finally:
        store.close()


def test_no_action_emitted_when_values_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        runner = _build_runner(
            store=store,
            run_id=run_id,
            spec_values=[
                {"Bias": 1.0},
                {"Bias": 1.0},
                {"Bias": 1.0},
            ],
        )

        runner.run_iterations(3)

        action_events = store.list_action_events(run_id=run_id)
        assert action_events == []
    finally:
        store.close()


def test_action_window_bounds_respect_configurable_action_window(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        runner = _build_runner(
            store=store,
            run_id=run_id,
            action_window_s=4.0,
            spec_values=[
                {"Bias": 0.2},
                {"Bias": 0.4},
            ],
        )

        runner.run_iterations(2)

        action_events = store.list_action_events(run_id=run_id)
        assert len(action_events) == 1
        event = action_events[0]

        assert event["signal_window_start_dt_s"] == pytest.approx(-3.9)
        assert event["signal_window_end_dt_s"] == pytest.approx(4.1)
    finally:
        store.close()


def test_detected_at_utc_is_valid_iso_utc_string(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        runner = _build_runner(
            store=store,
            run_id=run_id,
            spec_values=[
                {"Bias": 0.1},
                {"Bias": 0.2},
            ],
        )

        runner.run_iterations(2)

        action_events = store.list_action_events(run_id=run_id)
        assert len(action_events) == 1

        detected_at_utc = action_events[0]["detected_at_utc"]

        assert detected_at_utc.endswith("Z")
        parsed = datetime.fromisoformat(detected_at_utc.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() is not None
    finally:
        store.close()
