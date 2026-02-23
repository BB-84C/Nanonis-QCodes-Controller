from __future__ import annotations

import json
from pathlib import Path

from nanonis_qcodes_controller.trajectory.monitor import TrajectoryMonitorRunner
from nanonis_qcodes_controller.trajectory.sqlite_store import TrajectorySQLiteStore


def test_run_iterations_writes_signal_and_spec_rows_and_increments_sample_idx(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        times = iter([10.0, 10.0, 10.0, 10.5, 10.5, 11.5, 11.5])

        runner = TrajectoryMonitorRunner(
            store=store,
            run_id=run_id,
            run_start_utc="2026-02-22T00:00:00Z",
            interval_s=0.1,
            rotate_entries=3,
            poll_signals=lambda: {"Z Position": 1.23},
            poll_specs=lambda: {"Bias": 0.5},
            monotonic_time_s=lambda: next(times),
            sleep_s=lambda _duration_s: None,
        )

        completed = runner.run_iterations(3)

        signal_rows = store._connection.execute(
            "SELECT dt_s, values_json FROM signal_samples WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        spec_rows = store._connection.execute(
            "SELECT dt_s, vals_json FROM spec_samples WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()

        assert runner.sample_idx == 3
        assert completed == 3
        assert len(signal_rows) == 3
        assert len(spec_rows) == 3
        assert [row["dt_s"] for row in signal_rows] == [0.0, 0.5, 1.5]
        assert [row["dt_s"] for row in spec_rows] == [0.0, 0.5, 1.5]
        assert [json.loads(row["values_json"]) for row in signal_rows] == [
            {"Z Position": 1.23},
            {"Z Position": 1.23},
            {"Z Position": 1.23},
        ]
        assert [json.loads(row["vals_json"]) for row in spec_rows] == [
            {"Bias": 0.5},
            {"Bias": 0.5},
            {"Bias": 0.5},
        ]
    finally:
        store.close()


def test_segment_rotation_uses_rotate_entries_boundary(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        times = iter([100.0, 100.0, 100.0, 100.1, 100.1, 100.2, 100.2, 100.3, 100.3, 100.4, 100.4])

        runner = TrajectoryMonitorRunner(
            store=store,
            run_id=run_id,
            run_start_utc="2026-02-22T00:00:00Z",
            interval_s=0.1,
            rotate_entries=3,
            poll_signals=lambda: {"Z Position": 1.0},
            poll_specs=lambda: {"Bias": 2.0},
            monotonic_time_s=lambda: next(times),
            sleep_s=lambda _duration_s: None,
        )

        runner.run_iterations(5)

        signal_rows = store._connection.execute(
            """
            SELECT signal_catalog.signal_label
            FROM signal_samples
            INNER JOIN signal_catalog
                ON signal_samples.signal_id = signal_catalog.id
               AND signal_samples.run_id = signal_catalog.run_id
            WHERE signal_samples.run_id = ?
            ORDER BY signal_samples.id ASC
            """,
            (run_id,),
        ).fetchall()
        spec_rows = store._connection.execute(
            """
            SELECT spec_catalog.spec_label
            FROM spec_samples
            INNER JOIN spec_catalog
                ON spec_samples.spec_id = spec_catalog.id
               AND spec_samples.run_id = spec_catalog.run_id
            WHERE spec_samples.run_id = ?
            ORDER BY spec_samples.id ASC
            """,
            (run_id,),
        ).fetchall()

        assert [row["signal_label"] for row in signal_rows] == [
            "segment-0",
            "segment-0",
            "segment-0",
            "segment-1",
            "segment-1",
        ]
        assert [row["spec_label"] for row in spec_rows] == [
            "segment-0",
            "segment-0",
            "segment-0",
            "segment-1",
            "segment-1",
        ]
    finally:
        store.close()


def test_dt_s_is_non_negative_and_monotonic_increasing(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        times = iter([5.0, 5.0, 5.0, 5.001, 5.001, 5.25, 5.25, 5.5, 5.5])

        runner = TrajectoryMonitorRunner(
            store=store,
            run_id=run_id,
            run_start_utc="2026-02-22T00:00:00Z",
            interval_s=0.1,
            rotate_entries=10,
            poll_signals=lambda: {"Z Position": 1.0},
            poll_specs=lambda: {"Bias": 1.0},
            monotonic_time_s=lambda: next(times),
            sleep_s=lambda _duration_s: None,
        )

        runner.run_iterations(4)

        signal_rows = store._connection.execute(
            "SELECT dt_s FROM signal_samples WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        dt_values = [row["dt_s"] for row in signal_rows]

        assert dt_values[0] == 0.0
        assert all(value >= 0.0 for value in dt_values)
        assert dt_values == sorted(dt_values)
    finally:
        store.close()


def test_run_iterations_uses_interval_s_for_drift_aware_cadence(tmp_path: Path) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-001", started_at_utc="2026-02-22T00:00:00Z")
        now_s = 100.0
        sleep_calls: list[float] = []

        def monotonic_time_s() -> float:
            return now_s

        def sleep_s(duration_s: float) -> None:
            nonlocal now_s
            sleep_calls.append(duration_s)
            now_s += duration_s

        def poll_signals() -> dict[str, object]:
            nonlocal now_s
            now_s += 0.03
            return {"Z Position": 1.0}

        def poll_specs() -> dict[str, object]:
            nonlocal now_s
            now_s += 0.02
            return {"Bias": 2.0}

        runner = TrajectoryMonitorRunner(
            store=store,
            run_id=run_id,
            run_start_utc="2026-02-22T00:00:00Z",
            interval_s=0.1,
            rotate_entries=10,
            poll_signals=poll_signals,
            poll_specs=poll_specs,
            monotonic_time_s=monotonic_time_s,
            sleep_s=sleep_s,
        )

        runner.run_iterations(3)

        dt_rows = store._connection.execute(
            "SELECT dt_s FROM signal_samples WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()

        assert [round(value, 10) for value in sleep_calls] == [0.05, 0.05]
        assert [round(row["dt_s"], 10) for row in dt_rows] == [0.0, 0.1, 0.2]
    finally:
        store.close()
